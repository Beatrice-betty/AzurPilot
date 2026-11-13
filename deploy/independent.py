"""独立安装包的 ALAS 更新与恢复。运行环境独立于应用文件。"""

import base64
import hashlib
import http.client
import json
import ntpath
import os
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

GITCODE_REPOSITORY = 'https://gitcode.com/Beatrice-betty/AzurPilot.git'
GITHUB_REPOSITORY = 'https://github.com/Beatrice-betty/AzurPilot.git'
GITHUB_RELEASE = 'https://raw.githubusercontent.com/Beatrice-betty/AzurPilot/independent-windows/'
SOURCES = (GITCODE_REPOSITORY, GITHUB_REPOSITORY)
RELEASE_BRANCH = 'refs/heads/independent-windows'
DEFAULT_BRANCH = 'dev'
MAX_MANIFEST_SIZE = 64 * 1024
MAX_FRONTEND_SIZE = 40 * 1024 * 1024
APP_DIRS = ('assets', 'bin', 'campaign', 'deploy', 'module', 'submodule', 'licenses')
APP_FILES = ('alas.py', 'gui.py', 'mcp_server_sse.py', 'tui.py', 'pyproject.toml', 'uv.lock', 'LICENSE')
RECEIPT = Path('.azurpilot/independent.json')
FILES = Path('.azurpilot/independent-files.json')
WORK = Path('.azurpilot/independent-update')
CREATE_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class GitOffer(NamedTuple):
    release: dict
    repository: str
    branch: str


def enabled():
    return os.environ.get('AZURPILOT_INDEPENDENT') == '1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def dependency_id(root):
    root = Path(root)
    return hashlib.sha256((root / 'uv.lock').read_bytes() + b'\0' + (root / 'pyproject.toml').read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        file.write(canonical(value))
        file.flush()
        os.fsync(file.fileno())
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def app_path(name, *, frontend=False, build_frontend=False):
    posix = PurePosixPath(name)
    win = PureWindowsPath(name)
    if (posix.as_posix() != name or posix.is_absolute() or win.drive or '\\' in name
            or not posix.parts or '..' in posix.parts or any(
                part.endswith(('.', ' ')) or any(c in part for c in '<>:"|?*') or ntpath.isreserved(part)
                for part in posix.parts)):
        return False
    if frontend:
        return len(posix.parts) > 2 and posix.parts[:2] == ('frontend', 'dist')
    if build_frontend:
        return len(posix.parts) > 1 and posix.parts[0] == 'frontend'
    if len(posix.parts) == 1:
        return name in APP_FILES
    if posix.parts[0] == 'config':
        return len(posix.parts) == 2 and (
            posix.name == 'template.json'
            or posix.name.startswith('deploy.template') and posix.suffix == '.yaml'
        )
    return posix.parts[0] in APP_DIRS


def check_target(root, name):
    if not (app_path(name) or app_path(name, frontend=True)):
        raise ValueError(f'更新包路径无效 {name}')
    path = root / name
    if path.resolve() != path or path.is_symlink() or path.is_junction():
        raise ValueError(f'应用文件不能通过链接更新 {name}')
    return path


def unpack(archive, target, *, frontend=False, build_frontend=False):
    hashes = {}
    seen = set()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            name = member.filename
            if not app_path(name, frontend=frontend, build_frontend=build_frontend):
                continue
            if name.casefold() in seen or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError(f'更新包包含重复路径或链接 {name}')
            seen.add(name.casefold())
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, path.open('xb') as output:
                shutil.copyfileobj(source, output)
            hashes[name] = digest(path)
    return hashes


def verify_manifest(document, key):
    payload = document['release']
    Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True)).verify(
        base64.b64decode(document['signature'], validate=True), canonical(payload)
    )
    if (payload.get('schema') != 1 or not isinstance(payload.get('sequence'), int)
            or not re.fullmatch('[0-9a-f]{40}', payload.get('revision', ''))
            or not re.fullmatch('[0-9a-f]{64}', payload.get('dependencies', ''))
            or not re.fullmatch('[0-9a-f]{64}', payload.get('frontendSha256', ''))):
        raise ValueError('发布清单格式不匹配')
    return payload


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'AzurPilot-Independent/1'})
    with urllib.request.urlopen(request, timeout=8) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError('发布文件超过预期大小')
    return body


def update_source(root):
    from deploy.utils import poor_yaml_read

    values = poor_yaml_read(root / 'config/deploy.yaml')
    repository = str(values.get('Repository') or GITCODE_REPOSITORY).strip().strip('"')
    branch = str(values.get('Branch') or DEFAULT_BRANCH).strip().strip('"')
    if repository in ('cn', 'cnb', 'gitcode'):
        repository = GITCODE_REPOSITORY
    elif repository == 'global':
        repository = GITHUB_REPOSITORY
    url = urlsplit(repository)
    if (url.scheme != 'https' or not url.hostname or url.username is not None
            or any(char in repository for char in ('?', '#', '%')) or not url.path.strip('/')
            or re.search(r'[\s\\]', repository)):
        raise ValueError('更新仓库需要使用不带账号或令牌的 HTTPS Git 地址')
    try:
        _ = url.port
    except ValueError as exc:
        raise ValueError('更新仓库的端口无效') from exc
    if (not re.fullmatch(r'[A-Za-z0-9_][\w./-]*', branch, flags=re.ASCII)
            or '..' in branch or any(not part or part.startswith('.') or part.endswith(('.', '.lock'))
                                     for part in branch.split('/'))):
        raise ValueError('更新分支名称无效')
    repository = repository.rstrip('/')
    for official in SOURCES:
        if repository.removesuffix('.git') == official.removesuffix('.git'):
            repository = official
            break
    return repository, branch


def _remote_revision(root, repository, branch):
    ref = f'refs/heads/{branch}'
    listing = git(root, gitcode_checkout(root), 'ls-remote', '--exit-code', '--refs', repository, ref,
                  stdout=subprocess.PIPE, timeout=15).stdout.decode('ascii')
    for line in listing.splitlines():
        revision, _, name = line.partition('\t')
        if name == ref and re.fullmatch('[0-9a-f]{40}', revision):
            return revision
    raise ValueError('没有找到配置的更新分支')


def check(root=None):
    root = Path(root or Path.cwd()).resolve()
    current = read_json(root / RECEIPT)
    repository, branch = update_source(root)
    mirrors = SOURCES if repository == GITCODE_REPOSITORY else (repository,)
    revision = None
    for mirror in mirrors:
        try:
            # 这对镜像由 GitHub 单向同步。不同步时以 GitHub 的提交为准，防止退回旧镜像。
            revision = _remote_revision(root, mirror, branch)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    if revision is None:
        raise RuntimeError('无法读取所选仓库的分支，当前版本可以继续运行')
    if revision != current['revision']:
        return GitOffer({'revision': revision}, repository, branch)


def git(root, cwd, *args, stdout=subprocess.DEVNULL, timeout=210):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(
        [str(root / 'runtime/git/cmd/git.exe'), '-c', 'http.sslVerify=true',
         '-c', 'credential.helper=', '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
         '-c', 'http.lowSpeedLimit=1024', '-c', 'http.lowSpeedTime=20',
         '-c', 'http.userAgent=Mozilla/5.0 AzurPilotIndependent', *args],
        cwd=cwd, check=True, timeout=timeout, env=env, creationflags=CREATE_FLAGS,
        stdout=stdout, stderr=subprocess.PIPE,
    )


def probe(root, staged):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    command = (
        "import uvicorn; from module.api.app import create_app; "
        f"uvicorn.run(create_app(password='', manage_runtime=False, mount_mcp=False), host='127.0.0.1', port={port})"
    )
    env = os.environ.copy()
    for name in ('AZURPILOT_CONFIG_DIR', 'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        env.pop(name, None)
    env.update(AZURPILOT_INDEPENDENT='1', PYTHONNOUSERSITE='1')
    child = subprocess.Popen(
        [str(root / 'runtime/python/python.exe'), '-B', '-c', command],
        cwd=staged, env=env, creationflags=CREATE_FLAGS,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and child.poll() is None:
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
            try:
                connection.request('GET', '/healthz')
                response = connection.getresponse()
                response.read()
                if response.status == 200:
                    connection.request('GET', '/')
                    response = connection.getresponse()
                    if response.status == 200 and b'<div id="root">' in response.read(16 * 1024):
                        return
            except OSError:
                pass
            finally:
                connection.close()
            time.sleep(0.2)
        raise RuntimeError('候选版未通过本机控制台启动检查，继续使用当前版')
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def gitcode_checkout(root):
    # 缓存发布分支的 Git 对象，重复检查时可以复用已经下载的网页包。
    checkout = _transaction(root).parent / 'gitcode-release'
    checkout.mkdir(parents=True, exist_ok=True)
    if checkout.resolve() != checkout:
        raise ValueError('GitCode 发布缓存不能是链接')
    if not (checkout / '.git').is_dir():
        git(root, checkout, 'init', '-q')
    return checkout


def fetch_gitcode_release(root, checkout):
    git(root, checkout, 'fetch', '--depth=1', '--no-tags', GITCODE_REPOSITORY, RELEASE_BRANCH, timeout=25)
    revision = git(root, checkout, 'rev-parse', '--verify', 'FETCH_HEAD', stdout=subprocess.PIPE).stdout
    revision = revision.decode('ascii').strip()
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('GitCode 发布提交无效')
    return revision


def gitcode_release_file(root, checkout, revision, name, limit):
    object_name = f'{revision}:{name}'
    size = int(git(root, checkout, 'cat-file', '-s', object_name, stdout=subprocess.PIPE).stdout)
    if not 0 < size <= limit:
        raise ValueError('发布文件超过预期大小')
    return git(root, checkout, 'show', object_name, stdout=subprocess.PIPE).stdout


def _matching_frontend(root, repository, revision, frontend):
    """可选发布分支只用来取与当前源码提交匹配的预构建网页。"""
    if repository == GITCODE_REPOSITORY:
        checkout = gitcode_checkout(root)
        release = fetch_gitcode_release(root, checkout)
        manifest = json.loads(gitcode_release_file(root, checkout, release, 'release.json', MAX_MANIFEST_SIZE))
        if manifest['release']['revision'] != revision:
            return False
        data = gitcode_release_file(root, checkout, release, 'frontend.zip', MAX_FRONTEND_SIZE)
    else:
        manifest = json.loads(fetch(GITHUB_RELEASE + 'release.json', MAX_MANIFEST_SIZE))
        if manifest['release']['revision'] != revision:
            return False
        data = fetch(GITHUB_RELEASE + 'frontend.zip', MAX_FRONTEND_SIZE)
    if hashlib.sha256(data).hexdigest() != manifest['release']['frontendSha256']:
        raise ValueError('网页包校验不符')
    frontend.write_bytes(data)
    return True


def _fetch_source(root, checkout, offer):
    mirrors = SOURCES if offer.repository == GITCODE_REPOSITORY else (offer.repository,)
    for repo in mirrors:
        try:
            revision = offer.release['revision']
            git(root, checkout, 'fetch', '--depth=1', '--no-tags', repo, revision)
            return repo
        except (OSError, subprocess.SubprocessError) as exc:
            failure = exc
    raise RuntimeError('无法从所选仓库下载 ALAS 提交，当前版本可以继续运行') from failure


def _node_command(root):
    bundled = root / 'runtime/nodejs'
    node = bundled / 'node.exe'
    cli = bundled / 'node_modules/npm/bin/npm-cli.js'
    if node.is_file() and cli.is_file():
        return [str(node), str(cli)]
    from deploy.frontend import npm_command

    return npm_command()


def _build_frontend(root, archive, temp, staged):
    build = temp / 'build'
    build.mkdir()
    unpack(archive, build, build_frontend=True)
    project = build / 'frontend'
    for name in ('package.json', 'package-lock.json'):
        if not (project / name).is_file():
            raise ValueError('仓库没有兼容的预构建网页或前端源码')
    command = _node_command(root)
    env = os.environ.copy()
    env.pop('NODE_OPTIONS', None)
    env['PATH'] = str(Path(command[0]).parent) + os.pathsep + env.get('PATH', '')
    env['npm_config_cache'] = str(temp / 'npm-cache')
    try:
        for registry in ('https://registry.npmmirror.com', 'https://registry.npmjs.org'):
            try:
                subprocess.run([*command, 'ci', '--no-audit', '--no-fund', f'--registry={registry}'],
                               cwd=project, env=env, check=True, timeout=300, creationflags=CREATE_FLAGS,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if registry == 'https://registry.npmjs.org':
                    raise
        subprocess.run([*command, 'run', 'build'], cwd=project, env=env, check=True, timeout=180,
                       creationflags=CREATE_FLAGS, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('所选仓库的网页构建失败，当前版本可以继续运行') from exc
    dist = project / 'dist'
    if not (dist / 'index.html').is_file() or not (dist / 'assets').is_dir():
        raise RuntimeError('所选仓库的构建没有生成完整网页，当前版本可以继续运行')
    files = {}
    for path in dist.rglob('*'):
        name = path.relative_to(build).as_posix()
        if path.is_symlink() or path.is_junction():
            raise ValueError('网页构建目录包含链接')
        if path.is_file():
            if not app_path(name, frontend=True):
                raise ValueError('网页构建生成了无效路径')
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            files[name] = digest(target)
    return files


def _frontend_for_commit(root, repo, revision, archive, temp, staged):
    files = unpack(archive, staged, frontend=True)
    if files:
        return files
    if repo in SOURCES:
        frontend = temp / 'frontend.zip'
        for mirror in (repo, *(url for url in SOURCES if url != repo)):
            try:
                matched = _matching_frontend(root, mirror, revision, frontend)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                continue
            if matched:
                return unpack(frontend, staged, frontend=True)
    return _build_frontend(root, archive, temp, staged)


def prepare(offer, root=None):
    root = Path(root or Path.cwd()).resolve()
    if update_source(root) != (offer.repository, offer.branch):
        raise RuntimeError('更新源已更改，请重新检查更新')
    current = read_json(root / RECEIPT)
    if dependency_id(root) != current['dependencies']:
        raise RuntimeError('本机依赖清单已经改变，请用完整安装包修复')
    directory = _transaction(root).parent
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='download-', dir=directory) as temp:
        temp = Path(temp)
        checkout = temp / 'git'
        checkout.mkdir()
        git(root, checkout, 'init', '-q')
        repo = _fetch_source(root, checkout, offer)
        revision = offer.release['revision']
        archive = temp / 'source.zip'
        git(root, checkout, 'archive', '--format=zip', f'--output={archive}', revision)
        staged = temp / 'app'
        staged.mkdir()
        files = unpack(archive, staged)
        if not all(name in files for name in ('gui.py', 'uv.lock', 'pyproject.toml', 'deploy/independent.py')):
            raise RuntimeError('仓库不兼容此独立版，当前版本可以继续运行')
        if dependency_id(staged) != current['dependencies']:
            raise RuntimeError('所选版本需要不同的 Python 依赖，请使用对应完整安装包')
        if update_source(root) != (offer.repository, offer.branch):
            raise RuntimeError('更新源已更改，请重新检查更新')
        try:
            files.update(_frontend_for_commit(root, repo, revision, archive, temp, staged))
        except zipfile.BadZipFile as exc:
            raise RuntimeError('网页包无效，当前版本可以继续运行') from exc
        if not all(name in files for name in ('gui.py', 'frontend/dist/index.html', 'uv.lock')):
            raise ValueError('应用包缺少必要文件')
        probe(root, staged)
        ready = Path(tempfile.mkdtemp(prefix='ready-', dir=directory))
        os.replace(staged, ready / 'app')
        write_json(ready / 'files.json', files)
        write_json(ready / 'version.json', {**current, 'revision': revision})
        write_json(ready / 'source.json', [offer.repository, offer.branch])
        return ready


def backup_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def restore_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
        temp = Path(file.name)
    try:
        shutil.copy2(source, temp)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def _transaction(root):
    directory = root / WORK
    if directory.exists() and directory.resolve() != directory:
        raise ValueError('更新目录不能是链接')
    return directory / 'transaction.json'


def backup_config(config, destination):
    database = config / 'azurpilot.db'
    sidecars = {'azurpilot.db', 'azurpilot.db-wal', 'azurpilot.db-shm', 'azurpilot.db-journal'}

    def ignore_database(directory, names):
        return sidecars.intersection(names) if Path(directory) == config and database.is_file() else ()

    shutil.copytree(config, destination, ignore=ignore_database)
    if database.is_file():
        # 存储服务可能还在提交 WAL，使用 SQLite 的一致快照作为回退副本。
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(destination / database.name)) as backup:
                source.backup(backup)


def apply(ready, root=None):
    root = Path(root or Path.cwd()).resolve()
    if (ready / 'source.json').is_file():
        if read_json(ready / 'source.json') != list(update_source(root)):
            raise RuntimeError('更新源已更改，请重新检查更新')
    journal = _transaction(root)
    if journal.exists():
        raise RuntimeError('正在恢复上一轮更新')
    old, new = read_json(root / FILES), read_json(ready / 'files.json')
    changed = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
    for name in changed:
        target = check_target(root, name)
        if target.exists() and (name not in old or not target.is_file() or digest(target) != old[name]):
            raise RuntimeError(f'检测到本机修改，保留现有文件 {name}')
    backup = ready / 'previous'
    config = root / 'config'
    if config.is_dir():
        if config.resolve() != config:
            raise ValueError('配置目录不能是链接')
        for dirpath, names, files in os.walk(config, followlinks=False):
            if any((Path(dirpath) / name).is_symlink() or (Path(dirpath) / name).is_junction()
                   for name in (*names, *files)):
                raise ValueError('配置包含链接，当前安装保留原状')
        backup_config(config, backup / 'config')
    for name in changed:
        target = root / name
        if target.is_file():
            backup_file(target, backup / 'app' / name)
    for name in (RECEIPT, FILES):
        backup_file(root / name, backup / name)
    write_json(journal, {'ready': ready.name, 'changed': changed, 'phase': 'installing'})
    try:
        for name in changed:
            target = root / name
            if name in new:
                restore_file(ready / 'app' / name, target)
            else:
                target.unlink(missing_ok=True)
        write_json(root / FILES, new)
        write_json(root / RECEIPT, read_json(ready / 'version.json'))
        write_json(journal, {'ready': ready.name, 'changed': changed, 'phase': 'trial'})
    except BaseException:
        recover(root)
        raise


def _previous(root, record):
    name = record['ready']
    if not re.fullmatch(r'ready-[a-z0-9_]+', name):
        raise ValueError('更新恢复记录无效')
    backup = root / WORK / name / 'previous'
    if backup.resolve() != backup or not backup.is_dir():
        raise ValueError('更新恢复副本缺失')
    return backup


def recover(root=None):
    root = Path(root or Path.cwd()).resolve()
    journal = _transaction(root)
    if not journal.is_file():
        return False
    record = read_json(journal)
    backup = _previous(root, record)
    if record['phase'] == 'trial' and (backup / 'config').is_dir():
        # 保留启动失败后产生的配置副本。两次目录替换之间断电可从记录继续恢复。
        failed = backup.parent / 'config-after-failed-update'
        prepared = backup.parent / 'config-to-restore'
        config = root / 'config'
        if not failed.exists():
            shutil.copytree(backup / 'config', prepared, dirs_exist_ok=True)
            if config.exists():
                os.replace(config, failed)
        if not config.exists():
            os.replace(prepared, config)
    for name in record['changed']:
        target = check_target(root, name)
        original = backup / 'app' / name
        if original.is_file():
            restore_file(original, target)
        else:
            target.unlink(missing_ok=True)
    for name in (RECEIPT, FILES):
        restore_file(backup / name, root / name)
    journal.unlink()
    shutil.rmtree(backup.parent / 'app', ignore_errors=True)
    return True


def confirm(root=None):
    root = Path(root or Path.cwd()).resolve()
    journal = _transaction(root)
    if journal.is_file():
        record = read_json(journal)
        if record['phase'] == 'trial':
            backup = _previous(root, record)
            journal.unlink()
            shutil.rmtree(backup.parent / 'app', ignore_errors=True)


def trial_pending(root=None):
    return _transaction(Path(root or Path.cwd()).resolve()).is_file()


if __name__ == '__main__':
    if sys.argv[1:] != ['recover']:
        raise SystemExit('用法 independent_update.py recover')
    recover()
