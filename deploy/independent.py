"""独立安装包的 ALAS 更新与恢复。运行环境独立于应用文件。"""

import base64
import hashlib
import http.client
import json
import logging
import ntpath
import os
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple
from urllib.parse import urlsplit

if __name__ != '__main__':
    # 恢复脚本独立运行，首次部署的轻量包则已自带 deploy.utils。
    from deploy.utils import (
        CNB_REPOSITORY, GITHUB_REPOSITORY, NPM_REGISTRIES, REPOSITORY_SOURCES,
        official_repository, repository_candidates,
    )

GITHUB_RAW = 'https://raw.githubusercontent.com/Beatrice-betty/AzurPilot/'
RELEASE_BRANCH = 'independent-windows'
DEFAULT_BRANCH = 'dev'
MAX_MANIFEST_SIZE = 64 * 1024
MAX_FRONTEND_SIZE = 40 * 1024 * 1024
APP_DIRS = ('assets', 'bin', 'campaign', 'deploy', 'module', 'submodule', 'licenses')
APP_FILES = ('alas.py', 'gui.py', 'mcp_server_sse.py', 'tui.py', 'pyproject.toml', 'uv.lock', 'LICENSE')
DEPENDENCY_FILES = ('uv.lock', 'pyproject.toml')
RECEIPT = Path('.azurpilot/independent.json')
FILES = Path('.azurpilot/independent-files.json')
WORK = Path('.azurpilot/independent-update')
BUNDLED_PYTHON = Path('runtime/python/python.exe')
PYTHON_ENVIRONMENTS = Path('.azurpilot/independent-python/environments')
PYTHON_ENVIRONMENT_KEY = 'pythonEnvironment'
DEPENDENCY_BASE_KEY = 'dependencyBaseRevision'
DEPENDENCY_RELEASE_TAG = 'refs/tags/azurpilot-independent-deps/'
SOURCE_REF = 'refs/azurpilot/source'
CREATE_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_history_lock = threading.Lock()
_prepared_updates = {}


class GitOffer(NamedTuple):
    release: dict
    repository: str
    branch: str
    source: str | None = None


class UpdateBranchNotFound(ValueError):
    """Git 已返回结果，但没有所选分支。"""


class UpdateSourceUnavailable(RuntimeError):
    """暂时读不到上游，保留查询所用的仓库和分支。"""

    def __init__(self, repository, branch):
        super().__init__('暂时无法获取上游版本，当前版本可以继续运行')
        self.source = repository, branch


def enabled():
    return os.environ.get('AZURPILOT_INDEPENDENT') == '1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def dependency_id(root, *, staged=None):
    root = Path(root)
    return hashlib.sha256(b'\0'.join(
        _projected_file(root, staged, name).read_bytes() for name in DEPENDENCY_FILES
    )).hexdigest()


def _projected_file(root, staged, name):
    if staged is not None and (staged / name).is_file():
        return staged / name
    return root / name


def _dependency_spec(root, staged=None):
    """读取会影响 Python 环境的配置，忽略排版和测试工具设置。"""
    manifests = {}
    for name in DEPENDENCY_FILES:
        with _projected_file(root, staged, name).open('rb') as file:
            try:
                manifests[name] = tomllib.load(file)
            except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(f'{name} 格式无效，无法校验 Python 依赖') from exc
    pyproject = manifests['pyproject.toml']
    project = pyproject.get('project', {})
    tool = pyproject.get('tool', {})
    if not isinstance(project, dict) or not isinstance(tool, dict):
        raise ValueError('pyproject.toml 项目配置格式无效，无法校验 Python 依赖')
    project_fields = ('name', 'requires-python', 'dependencies', 'optional-dependencies', 'dynamic')
    return (
        manifests['uv.lock'],
        {name: project.get(name) for name in project_fields},
        pyproject.get('build-system', {}),
        pyproject.get('dependency-groups', {}),
        tool.get('uv', {}),
    )


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


def unpack(archive, target, *, frontend=False, build_frontend=False, previous=None):
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
            if previous is not None and name in previous:
                with bundle.open(member) as source:
                    hashes[name] = hashlib.file_digest(source, 'sha256').hexdigest()
                if hashes[name] == previous[name]:
                    continue
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, path.open('xb') as output:
                shutil.copyfileobj(source, output)
            if name not in hashes:
                hashes[name] = digest(path)
    return hashes


def verify_manifest(document, key):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

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
    repository = str(values.get('Repository') or CNB_REPOSITORY).strip().strip('"')
    branch = str(values.get('Branch') or DEFAULT_BRANCH).strip().strip('"')
    repository = official_repository(repository)
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
    return repository.rstrip('/'), branch


def _remote_revisions(root, repository, branch, *, dependency_releases):
    ref = f'refs/heads/{branch}'
    patterns = [ref]
    if dependency_releases:
        patterns.append(f'{DEPENDENCY_RELEASE_TAG}*')
    checkout = gitcode_checkout(root)
    args = ('ls-remote', '--exit-code', '--refs', repository, *patterns)
    for options in ((), ('-c', 'http.version=HTTP/1.1')):
        try:
            listing = git(root, checkout, *options, *args, stdout=subprocess.PIPE, timeout=15).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            if getattr(error, 'returncode', None) == 2:
                raise UpdateBranchNotFound('没有找到配置的更新分支') from error
            if options:
                raise
        else:
            break
    refs = {}
    for line in listing.decode('utf-8', errors='replace').splitlines():
        revision, _, name = line.partition('\t')
        if re.fullmatch('[0-9a-f]{40}', revision):
            refs[name] = revision
    base = refs.get(ref)
    if base is None:
        raise UpdateBranchNotFound('没有找到配置的更新分支')
    candidate = refs.get(f'{DEPENDENCY_RELEASE_TAG}{base}') if dependency_releases else None
    return base, candidate if candidate != base else None


def check(root=None, *, hot_reload=False):
    root = Path(root or Path.cwd()).resolve()
    clean_abandoned(root)
    current = read_json(root / RECEIPT)
    repository, branch = update_source(root)
    mirrors = repository_candidates(repository)
    # 冷启动可选择新解释器。旧桌面父进程内的 WebUI 必须等完整重启。
    dependency_releases = True
    if hot_reload:
        from deploy.independent_runtime import supports_hot_reload

        dependency_releases = supports_hot_reload()
    expected = None
    if len(mirrors) > 1:
        try:
            expected = _remote_revisions(root, GITHUB_REPOSITORY, branch, dependency_releases=dependency_releases)
        except (OSError, subprocess.SubprocessError) as error:
            raise UpdateSourceUnavailable(repository, branch) from error
    transport_error = None
    for mirror in mirrors:
        try:
            if mirror == GITHUB_REPOSITORY and expected is not None:
                base, candidate = expected
            else:
                base, candidate = _remote_revisions(root, mirror, branch, dependency_releases=dependency_releases)
                if expected is not None and (base, candidate) != expected:
                    logging.getLogger(__name__).info('更新源与 GitHub 的分支或依赖发布不一致，跳过 %s', mirror)
                    continue
            if candidate is None and base == current.get(DEPENDENCY_BASE_KEY) and branch == current.get('branch'):
                return None
            revision = candidate or base
            if revision == current['revision']:
                return None
            if expected is not None and branch == DEFAULT_BRANCH and current['revision']:
                if not _includes_installed(root, mirror, current['revision'], revision):
                    logging.getLogger(__name__).info('更新源尚未包含当前提交，继续使用本地版本')
                    return None
        except UpdateBranchNotFound:
            if expected is None:
                raise
        except (OSError, subprocess.SubprocessError) as error:
            transport_error = error
            logging.getLogger(__name__).warning('独立版读取上游版本失败 [%s] %s',
                                                urlsplit(mirror).hostname, type(error).__name__)
        else:
            release = {'revision': revision}
            if candidate is not None:
                release[DEPENDENCY_BASE_KEY] = base
            return GitOffer(release, repository, branch, mirror)
    raise UpdateSourceUnavailable(repository, branch) from transport_error


def git(root, cwd, *args, stdout=subprocess.DEVNULL, timeout=210):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    command = [str(root / 'runtime/git/cmd/git.exe'), '-c', 'http.sslVerify=true',
               '-c', 'credential.helper=', '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
               '-c', 'http.lowSpeedLimit=1024', '-c', 'http.lowSpeedTime=20',
               '-c', 'http.userAgent=Mozilla/5.0 AzurPilotIndependent', *args]
    # MinGit 启动的 HTTPS 和解包子进程也可能持有输出管道。超时后直接回收进程树。
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        capture = stdout == subprocess.PIPE
        with subprocess.Popen(command, cwd=cwd, env=env, creationflags=CREATE_FLAGS,
                              stdout=output if capture else stdout, stderr=errors) as child:
            try:
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == 'nt' and child.poll() is None:
                    taskkill = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/taskkill.exe'
                    try:
                        subprocess.run([str(taskkill), '/PID', str(child.pid), '/T', '/F'], timeout=5,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=CREATE_FLAGS)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                if child.poll() is None:
                    child.kill()
                raise
            output.seek(0)
            errors.seek(0)
            result = subprocess.CompletedProcess(command, child.returncode,
                                                 output.read() if capture else None, errors.read())
            result.check_returncode()
            return result


@contextmanager
def _history_checkout(root):
    from deploy.config_transaction import config_transaction

    cache = _transaction(root).parent / 'history.git'
    if cache.resolve() != cache:
        raise ValueError('提交历史缓存不能是链接')
    with _history_lock, config_transaction(cache):
        cache.mkdir(parents=True, exist_ok=True)
        if not (cache / 'HEAD').is_file():
            git(root, cache, 'init', '--bare', '-q')
        yield cache


def _has_commit(root, checkout, revision, timeout=5):
    try:
        git(root, checkout, '--no-lazy-fetch', 'cat-file', '-e', f'{revision}^{{commit}}', timeout=timeout)
        return True
    except subprocess.CalledProcessError:
        return False


def _fetch_history(root, checkout, repository, revision, timeout):
    git(root, checkout, '-c', 'protocol.version=2', 'fetch', '--no-tags', '--filter=tree:0',
        '--no-write-fetch-head', repository, f'{revision}:refs/azurpilot/history/{revision}', timeout=timeout)


def _includes_installed(root, repository, installed, revision):
    if not re.fullmatch('[0-9a-f]{40}', installed):
        raise ValueError('当前版本的提交记录无效')
    with _history_checkout(root) as cache:
        # 先用提交历史判定镜像是否已经同步，避免下载旧镜像的游戏资源。
        if not _has_commit(root, cache, revision):
            _fetch_history(root, cache, repository, revision, timeout=15)
        if not _has_commit(root, cache, installed):
            return False
        try:
            git(root, cache, '--no-lazy-fetch', 'merge-base', '--is-ancestor', installed, revision, timeout=5)
        except subprocess.CalledProcessError as exc:
            if exc.returncode == 1:
                return False
            raise
        return True


def history_refs(root, revisions):
    """按需缓存可达的提交对象，供没有工作区 Git 的独立版分页使用。"""
    root = root.resolve()
    with _history_checkout(root) as cache:
        deadline = time.monotonic() + 25
        refs = []
        source = None
        for revision in dict.fromkeys(revisions):
            if not re.fullmatch(r'[0-9a-f]{40}', revision):
                continue
            if not _has_commit(root, cache, revision):
                if source is None:
                    source, _ = update_source(root)
                mirrors = repository_candidates(source)
                for mirror in mirrors:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        _fetch_history(root, cache, mirror, revision, timeout=min(10, remaining))
                    except (OSError, subprocess.SubprocessError):
                        continue
                    break
                if not _has_commit(root, cache, revision, timeout=3):
                    continue
            refs.append(revision)
        if not refs:
            raise RuntimeError('提交记录暂不可用，请检查更新源或网络后重试')
        return cache, refs


def probe(root, staged, *, python=None):
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
        [str(python or root / BUNDLED_PYTHON), '-B', '-c', command],
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
        raise ValueError('发布缓存不能是链接')
    if not (checkout / '.git').is_dir():
        git(root, checkout, 'init', '-q')
    return checkout


def git_release_file(root, checkout, revision, name, limit):
    object_name = f'{revision}:{name}'
    size = int(git(root, checkout, 'cat-file', '-s', object_name, stdout=subprocess.PIPE).stdout)
    if not 0 < size <= limit:
        raise ValueError('发布文件超过预期大小')
    return git(root, checkout, 'show', object_name, stdout=subprocess.PIPE).stdout


def _matching_frontend(root, repository, revision, frontend, *, release_commit=None):
    """可选发布分支只用来取与当前源码提交匹配的预构建网页。"""
    if repository != GITHUB_REPOSITORY:
        if release_commit is None:
            return False
        checkout = gitcode_checkout(root)
        git(root, checkout, 'fetch', '--depth=1', '--no-tags', '--no-write-fetch-head',
            repository, release_commit, timeout=25)
        manifest = json.loads(git_release_file(root, checkout, release_commit, 'release.json', MAX_MANIFEST_SIZE))
        if manifest['release']['revision'] != revision:
            return False
        data = git_release_file(root, checkout, release_commit, 'frontend.zip', MAX_FRONTEND_SIZE)
    else:
        url = f'{GITHUB_RAW}{release_commit or RELEASE_BRANCH}/'
        manifest = json.loads(fetch(url + 'release.json', MAX_MANIFEST_SIZE))
        if manifest['release']['revision'] != revision:
            return False
        data = fetch(url + 'frontend.zip', MAX_FRONTEND_SIZE)
    if hashlib.sha256(data).hexdigest() != manifest['release']['frontendSha256']:
        raise ValueError('网页包校验不符')
    frontend.write_bytes(data)
    return True


def _fetch_source(root, directory, offer, *, cache=None):
    mirrors = repository_candidates(offer.source or offer.repository)
    revision = offer.release['revision']
    flags = ()
    if cache is not None:
        # 保留上次下载的引用，供 Git 协商已有对象。浅提交之间可以更新此缓存引用。
        revision = f'+{revision}:{SOURCE_REF}'
        flags = ('--no-write-fetch-head', '--no-auto-maintenance')
    for repo in mirrors:
        args = ('fetch', '--depth=1', '--no-tags', *flags, repo, revision)
        tls_options = ((), ('-c', 'http.version=HTTP/1.1')) if repo == GITHUB_REPOSITORY else ((),)
        for options in tls_options:
            checkout = cache
            if checkout is None:
                # 初装的每次尝试使用干净的临时目录，避开上次中断留下的 Git 锁。
                checkout = Path(tempfile.mkdtemp(prefix='git-', dir=directory))
                git(root, checkout, 'init', '-q')
            try:
                if cache is not None:
                    # 调用方持有缓存锁。清理中断留下的 Git 锁，保留已下载的对象。
                    for name in ('shallow.lock', f'{SOURCE_REF}.lock'):
                        lock = cache / name
                        if lock.resolve() != lock:
                            raise ValueError('源码缓存锁不能是链接')
                        lock.unlink(missing_ok=True)
                git(root, checkout, *options, *args)
            except OSError as exc:
                failure = exc
                break
            except subprocess.SubprocessError as exc:
                failure = exc
                continue
            return repo, checkout
    raise RuntimeError('无法从所选仓库下载 ALAS 提交，当前版本可以继续运行') from failure


@contextmanager
def _source_checkout(root, directory, offer):
    from deploy.config_transaction import config_transaction

    # 同一个更新源的两个镜像共用对象，自定义源各用自己的缓存。
    source_id = hashlib.sha256(offer.repository.encode()).hexdigest()[:16]
    cache = directory / f'source-{source_id}.git'
    if cache.resolve() != cache:
        raise ValueError('源码缓存不能是链接')
    with config_transaction(cache):
        cache.mkdir(exist_ok=True)
        if not (cache / 'HEAD').is_file() or not (cache / 'config').is_file():
            git(root, cache, 'init', '--bare', '-q')
        yield _fetch_source(root, directory, offer, cache=cache)


def _same_frontend(root, checkout, previous, revision):
    if not re.fullmatch('[0-9a-f]{40}', previous or ''):
        return False
    try:
        git(root, checkout, 'diff-tree', '--quiet', previous, revision, '--', 'frontend', '.gitattributes')
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _read_archive(root, checkout, revision, directory, *, paths=(), build_frontend=False):
    """从 Git 读取归档内容。没有目标目录时只计算摘要。"""
    read_fd, write_fd = os.pipe()

    def stream_archive():
        with os.fdopen(write_fd, 'wb') as output:
            selected = ('--', *paths) if paths else ()
            git(root, checkout, '--literal-pathspecs', 'archive', '--format=tar', revision,
                *selected, stdout=output)

    files = {}
    seen = set()
    with ThreadPoolExecutor(max_workers=1) as worker:
        archive = worker.submit(stream_archive)
        try:
            with os.fdopen(read_fd, 'rb') as source:
                with tarfile.open(fileobj=source, mode='r|', bufsize=256 * 1024) as bundle:
                    for member in bundle:
                        name = member.name
                        if member.isdir():
                            continue
                        if build_frontend:
                            allowed = app_path(name, build_frontend=True)
                        else:
                            allowed = app_path(name) or app_path(name, frontend=True)
                        if not allowed:
                            continue
                        if name.casefold() in seen or not member.isfile():
                            raise ValueError(f'更新包包含重复路径或链接 {name}')
                        seen.add(name.casefold())
                        with bundle.extractfile(member) as contents:
                            if directory is None:
                                files[name] = hashlib.file_digest(contents, 'sha256').hexdigest()
                            else:
                                target = directory / name
                                target.parent.mkdir(parents=True, exist_ok=True)
                                with target.open('xb') as output:
                                    shutil.copyfileobj(contents, output)
                                if not build_frontend:
                                    files[name] = digest(target)
                # tarfile 遇到结束标记就停止。读完 Git 输出的填充，等待命令正常退出。
                while source.read(256 * 1024):
                    pass
        except tarfile.TarError:
            archive.result()
            raise
        archive.result()
    return files


def _source_tree(root, checkout, revision):
    entries = git(root, checkout, 'ls-tree', '-r', '-z', revision, stdout=subprocess.PIPE).stdout
    files = {}
    seen = set()
    for entry in entries.split(b'\0'):
        if not entry:
            continue
        fields, name = entry.split(b'\t', 1)
        mode, kind, blob = fields.split()
        name = name.decode('utf-8')
        if kind != b'blob' or not (app_path(name) or app_path(name, frontend=True)):
            continue
        if mode == b'120000' or name.casefold() in seen:
            raise ValueError(f'更新包包含重复路径或链接 {name}')
        seen.add(name.casefold())
        files[name] = blob
    return files


def _uses_export_attributes(root, checkout, revision):
    try:
        git(root, checkout, 'grep', '-q', '-E', 'export-(ignore|subst)', revision,
            '--', ':(glob)**/.gitattributes')
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 1:
            return False
        raise
    return True


def _path_batches(paths):
    # 给 MinGit 的 Windows 命令行留足余量，按 UTF-16 长度分批传入路径。
    batch = []
    size = 0
    for name in sorted(paths):
        length = len(name.encode('utf-16-le')) // 2 + 3
        if batch and size + length > 8000:
            yield batch
            batch = []
            size = 0
        batch.append(name)
        size += length
    if batch:
        yield batch


def _stage_changes(root, checkout, revision, installed, previous, staged, *, export_attributes):
    comparable = (
        not export_attributes
        and _has_commit(root, checkout, installed)
        and not _uses_export_attributes(root, checkout, installed)
    )
    if comparable:
        old_tree = _source_tree(root, checkout, installed)
        new_tree = _source_tree(root, checkout, revision)
        files = {name: previous[name] for name, blob in new_tree.items()
                 if name in previous and blob == old_tree.get(name)}
        changed = new_tree.keys() - files.keys()
    else:
        # 缓存缺少旧提交，或自定义仓库修改了归档内容时，逐个文件比对已安装清单。
        files = _read_archive(root, checkout, revision, None)
        changed = {name for name, checksum in files.items() if previous.get(name) != checksum}
    for batch in _path_batches(changed):
        contents = _read_archive(root, checkout, revision, staged, paths=batch)
        if contents.keys() != set(batch):
            raise ValueError('Git 更新缺少待替换文件')
        files.update(contents)
    return files, comparable


def _installed_frontend(root, previous):
    files = {
        name: expected for name, expected in previous.items()
        if app_path(name, frontend=True)
    }
    if 'frontend/dist/index.html' not in files:
        return {}
    try:
        for name, expected in files.items():
            path = check_target(root, name)
            if not path.is_file() or digest(path) != expected:
                return {}
    except (OSError, ValueError):
        return {}
    return files


def _node_command(root):
    bundled = root / 'runtime/nodejs'
    node = bundled / 'node.exe'
    cli = bundled / 'node_modules/npm/bin/npm-cli.js'
    if node.is_file() and cli.is_file():
        return [str(node), str(cli)]
    from deploy.frontend import npm_command

    return npm_command()


def _build_frontend(root, temp, staged, *, previous=None):
    build = temp / 'build'
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
        for registry in NPM_REGISTRIES:
            try:
                subprocess.run([*command, 'ci', '--no-audit', '--no-fund', f'--registry={registry}'],
                               cwd=project, env=env, check=True, timeout=300, creationflags=CREATE_FLAGS,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if registry == NPM_REGISTRIES[-1]:
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
            files[name] = digest(path)
            if previous is None or files[name] != previous.get(name):
                backup_file(path, staged / name)
    return files


def _released_frontend(root, repo, revision, temp, staged, *, previous=None):
    if repo in REPOSITORY_SOURCES:
        frontend = temp / 'frontend.zip'
        mirrors = repository_candidates(repo)
        release_commit = None
        if len(mirrors) > 1:
            try:
                release_commit, _ = _remote_revisions(root, GITHUB_REPOSITORY, RELEASE_BRANCH, dependency_releases=False)
            except (OSError, UpdateBranchNotFound, subprocess.SubprocessError):
                pass
        for mirror in mirrors:
            try:
                matched = _matching_frontend(root, mirror, revision, frontend, release_commit=release_commit)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                continue
            if matched:
                return unpack(frontend, staged, frontend=True, previous=previous)


def _frontend_for_commit(root, repo, revision, archive, temp, staged):
    """初次部署和修复仍可从安装引导器的 ZIP 读取源码。"""
    files = unpack(archive, staged, frontend=True)
    if files:
        return files
    files = _released_frontend(root, repo, revision, temp, staged)
    if files is not None:
        return files
    unpack(archive, temp / 'build', build_frontend=True)
    return _build_frontend(root, temp, staged)


def _stage_frontend(root, repo, checkout, revision, temp, staged, previous, *, unchanged):
    if unchanged:
        files = _installed_frontend(root, previous)
        if files:
            return files
    files = _released_frontend(root, repo, revision, temp, staged, previous=previous)
    if files is not None:
        return files
    _read_archive(root, checkout, revision, temp / 'build', paths=('frontend',), build_frontend=True)
    return None


def prepare(offer, root=None, *, hot_reload=False):
    root = Path(root or Path.cwd()).resolve()
    if update_source(root) != (offer.repository, offer.branch):
        raise RuntimeError('更新源已更改，请重新检查更新')
    with ExitStack() as lease:
        lease.enter_context(_update_lock(root))
        journal = _transaction(root)
        if journal.exists():
            raise RuntimeError('正在恢复上一轮更新')
        _clean_abandoned(root)
        ready = Path(tempfile.mkdtemp(prefix='ready-', dir=journal.parent))
        try:
            _prepare_update(offer, root, ready, hot_reload=hot_reload)
        except BaseException:
            discard_ready(ready, root)
            raise
        # 活跃请求在等待正在运行的任务退出时也持有锁，避免被另一进程误清理。
        _prepared_updates[ready] = lease.pop_all()
        return ready


def _validate_dependency_release(root, checkout, revision, base):
    # 浅克隆仍保留原始提交头，可直接验证依赖版本衔接自哪个兼容提交。
    commit = git(root, checkout, 'cat-file', 'commit', revision, stdout=subprocess.PIPE).stdout
    headers = commit.partition(b'\n\n')[0].splitlines()
    parents = [line for line in headers if line.startswith(b'parent ')]
    if parents != [f'parent {base}'.encode('ascii')]:
        raise ValueError('依赖更新与当前分支的兼容版本不匹配，请重新获取更新')


def _prepare_update(offer, root, ready, *, hot_reload):
    current = read_json(root / RECEIPT)
    previous = read_json(root / FILES)
    # 程序文件清单随每次更新写入，安装包的原始依赖指纹继续供安装和修复使用。
    for name in DEPENDENCY_FILES:
        path = check_target(root, name)
        if not path.is_file() or digest(path) != previous.get(name):
            raise RuntimeError('本机依赖清单已经改变，请用完整安装包修复')
    installed = current['revision']
    revision = offer.release['revision']
    dependency_base = offer.release.get(DEPENDENCY_BASE_KEY)
    # 先记录候选所属的版本，启动器退出后可以识别尚未应用的临时目录。
    write_json(ready / 'base.json', installed)
    staged = ready / 'app'
    environment = current.get(PYTHON_ENVIRONMENT_KEY)
    sync_dependencies = False
    try:
        with _source_checkout(root, ready.parent, offer) as (repo, checkout):
            if dependency_base is not None:
                _validate_dependency_release(root, checkout, revision, dependency_base)
            export_attributes = _uses_export_attributes(root, checkout, revision)
            files, comparable = _stage_changes(root, checkout, revision, installed, previous, staged,
                                               export_attributes=export_attributes)
            if not all(name in files for name in ('gui.py', 'deploy/independent.py', *DEPENDENCY_FILES)):
                raise RuntimeError('仓库不兼容此独立版，当前版本可以继续运行')
            sync_dependencies = (any((staged / name).is_file() for name in DEPENDENCY_FILES)
                                 and _dependency_spec(root, staged) != _dependency_spec(root))
            if 'deploy/independent_runtime.py' not in files and (environment is not None or sync_dependencies):
                if dependency_id(root, staged=staged) != current['dependencies']:
                    raise RuntimeError('所选版本无法使用已安装的 Python 环境，继续使用当前版')
                # 回到仍与安装包原始环境匹配的旧分支时，旧入口可以直接启动。
                environment = None
                sync_dependencies = False
            if sync_dependencies and hot_reload:
                from deploy.independent_runtime import supports_hot_reload

                if not supports_hot_reload():
                    raise RuntimeError('请先退出并重新启动 AzurPilot，再更新 Python 依赖')
            if update_source(root) != (offer.repository, offer.branch):
                raise RuntimeError('更新源已更改，请重新检查更新')
            frontend_sources = None
            if not export_attributes:
                entries = git(root, checkout, 'ls-tree', '-z', revision, '--', 'frontend', '.gitattributes',
                              stdout=subprocess.PIPE).stdout
                frontend_sources = hashlib.sha256(entries).hexdigest()
            if not any(name.startswith('frontend/dist/') for name in files):
                unchanged = (frontend_sources is not None
                             and current.get('frontendSources') == [installed, frontend_sources])
                if not unchanged and comparable:
                    unchanged = _same_frontend(root, checkout, installed, revision)
                with tempfile.TemporaryDirectory(prefix='frontend-', dir=ready, ignore_cleanup_errors=True) as temp:
                    temp = Path(temp)
                    frontend = _stage_frontend(root, repo, checkout, revision, temp, staged, previous,
                                               unchanged=unchanged)
                    if frontend is None:
                        frontend = _build_frontend(root, temp, staged, previous=previous)
                    files.update(frontend)
    except (tarfile.TarError, subprocess.SubprocessError) as exc:
        raise RuntimeError('无法读取所选提交的应用文件，当前版本可以继续运行') from exc
    except zipfile.BadZipFile as exc:
        raise RuntimeError('网页包无效，当前版本可以继续运行') from exc
    index_name = 'frontend/dist/index.html'
    if index_name not in files or not any(name.startswith('frontend/dist/assets/') for name in files):
        raise ValueError('应用包缺少必要的网页文件')
    with _projected_file(root, staged, index_name).open('rb') as index:
        if b'<div id="root">' not in index.read(16 * 1024):
            raise ValueError('候选版网页入口无效，继续使用当前版本')
    gui = staged / 'gui.py'
    if gui.is_file():
        try:
            compile(gui.read_bytes(), 'gui.py', 'exec')
        except SyntaxError as exc:
            raise ValueError('候选版启动入口无效，继续使用当前版本') from exc
    if sync_dependencies:
        from deploy.independent_runtime import sync_environment

        project = ready / 'python-project'
        project.mkdir()
        for name in DEPENDENCY_FILES:
            backup_file(_projected_file(root, staged, name), project / name)
        environment = ready.name
        python = sync_environment(root, environment, project, current)
        # 增量包只暂存变更文件，试运行需要完整的候选程序，配置和数据库留在原目录。
        with tempfile.TemporaryDirectory(prefix='probe-', dir=ready, ignore_cleanup_errors=True) as trial:
            trial = Path(trial)
            for name in files:
                path = _projected_file(root, staged, name)
                if path.is_file() and path.resolve() == path:
                    backup_file(path, trial / name)
            probe(root, trial, python=python)
    write_json(ready / 'files.json', files)
    version = {
        **current, 'revision': revision, 'branch': offer.branch,
        'frontendSources': [revision, frontend_sources] if frontend_sources is not None else None,
    }
    if environment is None:
        version.pop(PYTHON_ENVIRONMENT_KEY, None)
    else:
        version[PYTHON_ENVIRONMENT_KEY] = environment
    if dependency_base is None:
        version.pop(DEPENDENCY_BASE_KEY, None)
    else:
        version[DEPENDENCY_BASE_KEY] = dependency_base
    write_json(ready / 'version.json', version)
    write_json(ready / 'source.json', [offer.repository, offer.branch])


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


def _restore_snapshot(source, destination):
    # 尚未写到的文件可能正是应用更新时无法替换的文件，原内容完好时无需重新写入。
    if not destination.is_file() or digest(source) != digest(destination):
        restore_file(source, destination)


def _transaction(root):
    directory = root / WORK
    if directory.exists() and directory.resolve() != directory:
        raise ValueError('更新目录不能是链接')
    return directory / 'transaction.json'


def _update_lock(root):
    from deploy.config_transaction import config_transaction

    return config_transaction(_transaction(root))


def clean_abandoned(root=None):
    root = Path(root or Path.cwd()).resolve()
    journal = _transaction(root)
    if journal.parent.is_dir() and not journal.exists():
        with _update_lock(root):
            _clean_abandoned(root)


def _clean_abandoned(root):
    journal = _transaction(root)
    if journal.exists():
        return
    for ready in journal.parent.glob('ready-*'):
        if (ready not in _prepared_updates and ready.resolve() == ready
                and re.fullmatch(r'ready-[a-z0-9_]+', ready.name) and (ready / 'base.json').is_file()):
            _discard_ready(ready, root)
    environments = root / PYTHON_ENVIRONMENTS
    if environments.is_dir() and environments.resolve() == environments:
        active = read_json(root / RECEIPT).get(PYTHON_ENVIRONMENT_KEY)
        for path in environments.glob('ready-*'):
            if (path.name != active and journal.parent / path.name not in _prepared_updates
                    and path.is_dir() and path.resolve() == path
                    and re.fullmatch(r'ready-[a-z0-9_]+', path.name)):
                _remove_update_tree(path)


def _release_prepared(ready):
    lease = _prepared_updates.pop(ready, None)
    if lease is not None:
        lease.close()


def discard_ready(ready, root=None):
    """释放已结束的候选目录，并保留失败的新版本写出的配置。"""
    root = Path(root or Path.cwd()).resolve()
    try:
        _discard_ready(ready, root)
    finally:
        _release_prepared(ready)


def _environment_directory(root, name):
    if not isinstance(name, str) or not re.fullmatch(r'ready-[a-z0-9_]+', name):
        raise ValueError('Python 环境记录无效')
    directory = root / PYTHON_ENVIRONMENTS / name
    if directory.resolve() != directory:
        raise ValueError('Python 环境目录不能是链接')
    return directory


def installed_python(root, receipt=None):
    if receipt is None:
        receipt = read_json(root / RECEIPT)
    environment = receipt.get(PYTHON_ENVIRONMENT_KEY)
    if environment is None:
        return root / BUNDLED_PYTHON
    return _environment_directory(root, environment) / 'Scripts/python.exe'


def _remove_update_tree(path):
    # 候选环境内的监督进程也会执行回滚，等进程退出后再清理它的环境。
    if path == Path(sys.prefix).resolve():
        return

    def remove_readonly(remove, name, error):
        path = Path(name)
        if os.name != 'nt' or not isinstance(error, PermissionError) or path.resolve() != path:
            raise error
        path.chmod(stat.S_IWRITE)
        remove(name)

    try:
        shutil.rmtree(path, onexc=remove_readonly)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logging.getLogger(__name__).warning('更新临时目录清理失败：%s', exc)


def _discard_ready(ready, root):
    journal = _transaction(root)
    if (ready.parent != journal.parent or not re.fullmatch(r'ready-[a-z0-9_]+', ready.name)
            or ready.resolve() != ready):
        raise ValueError('待清理的更新目录无效')
    try:
        if read_json(journal)['ready'] == ready.name:
            return
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError):
        return

    failed_config = ready / 'config-after-failed-update'
    preserve_failed_config = failed_config.exists() and not _unchanged_failed_config(ready)
    paths = (ready / 'app', ready / 'previous') if preserve_failed_config else (ready,)
    for path in paths:
        _remove_update_tree(path)
    candidate = root / PYTHON_ENVIRONMENTS / ready.name
    if candidate.is_dir() and candidate.resolve() == candidate:
        if read_json(root / RECEIPT).get(PYTHON_ENVIRONMENT_KEY) != ready.name:
            _remove_update_tree(candidate)


def backup_config(config, destination):
    database = config / 'azurpilot.db'
    database_files = {'azurpilot.db', 'azurpilot.db-wal', 'azurpilot.db-shm', 'azurpilot.db-journal'}
    had_database = database.is_file()

    def ignore_database(directory, names):
        return database_files.intersection(names) if Path(directory) == config else ()

    shutil.copytree(config, destination, ignore=ignore_database)
    if not had_database and not database.is_file():
        if ((destination / 'azurpilot.migrated').exists()
                or any((config / name).exists() for name in database_files)):
            raise RuntimeError('缺少 azurpilot.db，无法备份现有业务数据，本次更新已取消')
        return None

    snapshot = destination / database.name
    # 使用 SQLite 的一致快照，以便包含已经提交到 WAL 的数据。
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
        with closing(sqlite3.connect(snapshot)) as backup:
            source.backup(backup)
            if backup.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise RuntimeError('业务数据库快照未通过完整性检查，本次更新已取消')
    return snapshot


def _preserve_database_snapshot(root, snapshot):
    with closing(sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True)) as backup:
        tables = backup.execute("""
            SELECT name FROM sqlite_schema
            WHERE type = 'table' AND name NOT GLOB 'sqlite_*' AND name <> 'storage_migrations'
        """).fetchall()
        for (table,) in tables:
            quoted = table.replace('"', '""')
            if backup.execute(f'SELECT 1 FROM "{quoted}" LIMIT 1').fetchone():
                break
        else:
            # 初始化生成的空库不能覆盖已有的历史数据副本。
            return

    destination = root.with_name(root.name + '_Data_Backup') / 'before-update' / snapshot.name
    if destination.resolve() != destination or destination.is_symlink():
        raise ValueError('业务数据库备份路径不能通过链接写入')
    restore_file(snapshot, destination)


def _config_digest(config):
    if config.resolve() != config or not config.is_dir():
        return None
    files = {}

    def raise_walk_error(error):
        raise error

    for directory, names, filenames in os.walk(config, onerror=raise_walk_error, followlinks=False):
        subdirs = set(names)
        for name in (*names, *filenames):
            path = Path(directory) / name
            if path.is_symlink() or path.is_junction():
                return None
            files[path.relative_to(config).as_posix()] = None if name in subdirs else digest(path)
    return hashlib.sha256(canonical(files)).hexdigest()


def _unchanged_failed_config(ready):
    try:
        recorded = ready / 'config-digest.json'
        if recorded.resolve() != recorded:
            return False
        before = read_json(recorded)
        return before is not None and before == _config_digest(ready / 'config-after-failed-update')
    except (OSError, ValueError):
        return False


def apply(ready, root=None):
    root = Path(root or Path.cwd()).resolve()
    try:
        with _update_lock(root):
            _apply(ready, root)
    except BaseException:
        discard_ready(ready, root)
        raise
    finally:
        _release_prepared(ready)


def _files_to_replace(root, ready, old, new):
    pending = []
    for name in sorted(old.keys() | new.keys()):
        before, after = old.get(name), new.get(name)
        if before == after:
            continue
        target = check_target(root, name)
        actual = None
        if target.exists():
            if not target.is_file():
                raise RuntimeError(f'检测到本机修改，保留现有文件 {name}')
            actual = digest(target)
        # 本机可能已经收到完全相同的文件，保留现有内容并更新安装记录。
        if actual == after:
            continue
        if actual is not None and actual != before:
            raise RuntimeError(f'检测到本机修改，保留现有文件 {name}')
        if after is not None and digest(ready / 'app' / name) != after:
            raise RuntimeError(f'候选文件校验失败 {name}')
        pending.append(name)
    return pending


def _apply(ready, root):
    if (ready / 'source.json').is_file():
        if read_json(ready / 'source.json') != list(update_source(root)):
            raise RuntimeError('更新源已更改，请重新检查更新')
    journal = _transaction(root)
    if journal.exists():
        raise RuntimeError('正在恢复上一轮更新')
    base = ready / 'base.json'
    if base.is_file() and read_json(base) != read_json(root / RECEIPT)['revision']:
        raise RuntimeError('本地版本已改变，请重新检查更新')
    version = read_json(ready / 'version.json')
    if version.get(PYTHON_ENVIRONMENT_KEY) and not installed_python(root, version).is_file():
        raise RuntimeError('候选 Python 环境已不可用，继续使用当前版')
    old, new = read_json(root / FILES), read_json(ready / 'files.json')
    changed = _files_to_replace(root, ready, old, new)
    backup = ready / 'previous'
    config = root / 'config'
    if changed and config.is_dir():
        if config.resolve() != config:
            raise ValueError('配置目录不能是链接')
        for dirpath, names, files in os.walk(config, followlinks=False):
            if any((Path(dirpath) / name).is_symlink() or (Path(dirpath) / name).is_junction()
                   for name in (*names, *files)):
                raise ValueError('配置包含链接，当前安装保留原状')
        snapshot = backup_config(config, backup / 'config')
        if snapshot is not None:
            _preserve_database_snapshot(root, snapshot)
        try:
            before = _config_digest(config)
            if before is not None:
                # 旧版恢复脚本可能先清理 previous，启动器随后仍需辨认重复配置。
                write_json(ready / 'config-digest.json', before)
        except OSError:
            # 无法确认失败副本是否相同也可以回退，届时保留原数据。
            pass
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
        write_json(root / RECEIPT, version)
        if changed:
            write_json(journal, {'ready': ready.name, 'changed': changed, 'phase': 'trial'})
        else:
            journal.unlink()
    except BaseException:
        recover(root)
        raise
    if not changed:
        _discard_ready(ready, root)


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
            _restore_snapshot(original, target)
        else:
            target.unlink(missing_ok=True)
    for name in (RECEIPT, FILES):
        _restore_snapshot(backup / name, root / name)
    journal.unlink()
    discard_ready(backup.parent, root)
    return True


def confirm(root=None):
    root = Path(root or Path.cwd()).resolve()
    journal = _transaction(root)
    if journal.is_file():
        record = read_json(journal)
        if record['phase'] == 'trial':
            backup = _previous(root, record)
            journal.unlink()
            discard_ready(backup.parent, root)


def trial_pending(root=None):
    return _transaction(Path(root or Path.cwd()).resolve()).is_file()


if __name__ == '__main__':
    if sys.argv[1:] != ['recover']:
        raise SystemExit('用法 independent_update.py recover')
    recover()
