"""本地安装包与已签名的 ALAS 发布包。运行环境独立于应用文件。"""

import base64
import hashlib
import http.client
import json
import ntpath
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


SOURCES = (
    ('https://raw.gitcode.com/Beatrice-betty/AzurPilot/raw/independent-windows/',
     'https://gitcode.com/Beatrice-betty/AzurPilot.git'),
    ('https://raw.githubusercontent.com/Beatrice-betty/AzurPilot/independent-windows/',
     'https://github.com/Beatrice-betty/AzurPilot.git'),
)
APP_DIRS = ('assets', 'bin', 'campaign', 'deploy', 'module', 'submodule', 'licenses')
APP_FILES = ('alas.py', 'gui.py', 'mcp_server_sse.py', 'tui.py', 'pyproject.toml', 'uv.lock', 'LICENSE')
RECEIPT = Path('.azurpilot/independent.json')
FILES = Path('.azurpilot/independent-files.json')
WORK = Path('.azurpilot/independent-update')
CREATE_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


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


def app_path(name, *, frontend=False):
    posix = PurePosixPath(name)
    win = PureWindowsPath(name)
    if (posix.as_posix() != name or posix.is_absolute() or win.drive or '\\' in name
            or not posix.parts or '..' in posix.parts or any(
                part.endswith(('.', ' ')) or any(c in part for c in '<>:"|?*') or ntpath.isreserved(part)
                for part in posix.parts)):
        return False
    if frontend:
        return len(posix.parts) > 2 and posix.parts[:2] == ('frontend', 'dist')
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


def unpack(archive, target, *, frontend=False):
    hashes = {}
    seen = set()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            name = member.filename
            if not app_path(name, frontend=frontend):
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


def check(root=None):
    root = Path(root or Path.cwd()).resolve()
    current = read_json(root / RECEIPT)
    error = None
    valid = False
    for source, repository in SOURCES:
        try:
            document = json.loads(fetch(source + 'release.json', 64 * 1024))
            candidate = verify_manifest(document, current['publicKey'])
            valid = True
            if candidate['sequence'] > current['sequence'] and candidate['revision'] != current['revision']:
                return candidate, source, repository
        except Exception as exc:
            error = exc
    if not valid:
        raise RuntimeError('两个发布源暂时不可用，当前版本可以继续运行') from error
    return None


def git(root, cwd, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(
        [str(root / 'runtime/git/cmd/git.exe'), '-c', 'http.sslVerify=true',
         '-c', 'credential.helper=', '-c', 'http.lowSpeedLimit=1024', '-c', 'http.lowSpeedTime=20',
         '-c', 'http.userAgent=Mozilla/5.0 AzurPilotIndependent', *args],
        cwd=cwd, check=True, timeout=210, env=env, creationflags=CREATE_FLAGS,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
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


def prepare(offer, root=None):
    root = Path(root or Path.cwd()).resolve()
    candidate, source, repository = offer
    current = read_json(root / RECEIPT)
    if candidate['dependencies'] != current['dependencies'] or dependency_id(root) != current['dependencies']:
        raise RuntimeError('这个版本需要新的完整安装包，当前版本可以继续运行')
    directory = root / WORK
    directory.mkdir(parents=True, exist_ok=True)
    for base, repo in ((source, repository), *(pair for pair in SOURCES if pair[0] != source)):
        try:
            with tempfile.TemporaryDirectory(prefix='download-', dir=directory) as temp:
                temp = Path(temp)
                frontend = temp / 'frontend.zip'
                frontend.write_bytes(fetch(base + 'frontend.zip', 40 * 1024 * 1024))
                if digest(frontend) != candidate['frontendSha256']:
                    raise ValueError('网页包校验不符')
                checkout = temp / 'git'
                checkout.mkdir()
                git(root, checkout, 'init', '-q')
                git(root, checkout, 'fetch', '--depth=1', '--no-tags', repo, candidate['revision'])
                source_archive = temp / 'source.zip'
                git(root, checkout, 'archive', '--format=zip', f'--output={source_archive}', candidate['revision'])
                staged = temp / 'app'
                staged.mkdir()
                files = unpack(source_archive, staged)
                files.update(unpack(frontend, staged, frontend=True))
                if not all(name in files for name in ('gui.py', 'frontend/dist/index.html', 'uv.lock')):
                    raise ValueError('应用包缺少必要文件')
                if dependency_id(staged) != current['dependencies']:
                    raise ValueError('候选应用与运行环境不匹配')
                probe(root, staged)
                ready = Path(tempfile.mkdtemp(prefix='ready-', dir=directory))
                os.replace(staged, ready / 'app')
                write_json(ready / 'files.json', files)
                write_json(ready / 'version.json', {**current, **candidate})
                return ready
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            failure = exc
    raise RuntimeError('ALAS 更新包下载或启动检查失败，当前版本可以继续运行') from failure


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


def apply(ready, root=None):
    root = Path(root or Path.cwd()).resolve()
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
        shutil.copytree(config, backup / 'config')
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
    return True


def confirm(root=None):
    root = Path(root or Path.cwd()).resolve()
    journal = _transaction(root)
    if journal.is_file() and read_json(journal)['phase'] == 'trial':
        journal.unlink()


def trial_pending(root=None):
    return _transaction(Path(root or Path.cwd()).resolve()).is_file()


if __name__ == '__main__':
    if sys.argv[1:] != ['recover']:
        raise SystemExit('用法 independent_update.py recover')
    recover()
