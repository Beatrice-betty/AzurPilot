"""国服 B 站登录数据的受限 ADB 传输，不在主机落地明文。"""
import base64
import re
import shlex
import sqlite3
import subprocess
import uuid
import xml.etree.ElementTree as ET
from contextlib import closing

from module.api.protocol import ApiError

PACKAGE = 'com.bilibili.azurlane'
BASES = (f'/data/user/0/{PACKAGE}', f'/data/data/{PACKAGE}')
DATABASE = 'databases/users.db'
SDK_PREFS = 'shared_prefs/com.bilibili.azurlane_preferences.xml'
PLAYER_PREFS = 'shared_prefs/com.bilibili.azurlane.v2.playerprefs.xml'
FILES = (DATABASE, SDK_PREFS, PLAYER_PREFS)


def account_key(name):
    return name.startswith(('user.', 'server.id', 'loginedServer_'))


class AccountDevice:
    def __init__(self, serial, adb):
        if not serial or serial == 'auto' or not re.fullmatch(r'[A-Za-z0-9_.:\-]+', serial):
            raise ApiError('DEVICE_REQUIRED', '账号管理需要明确的模拟器 ADB 地址')
        self.serial, self.adb = serial, str(adb)
        self.use_su = False
        if self.command('id -u').strip() != b'0':
            # 只用无副作用的 UID 查询协商 su 形式，不对恢复写入做失败重试。
            for mode in ('command', 'uid', 'root'):
                self.use_su = mode
                try:
                    if self.command('id -u').strip() == b'0':
                        break
                except ApiError:
                    continue
            else:
                raise ApiError('ROOT_REQUIRED', 'ADB 已连接，但无法取得 root；已检查 su -c、su 0 和 su root，请启用模拟器 root 权限')

    def resolve_base(self):
        """同时检查两种私有目录及可读文件，失败只报告路径，不输出账号内容。"""
        checks = []
        for base in BASES:
            checks.append(f'test -d {base}')
            checks.extend(f'test -f {base}/{name} && test -r {base}/{name}' for name in FILES)
        script = '; '.join(f'if {check}; then printf 1; else printf 0; fi' for check in checks)
        result = self.command(script).strip()
        if not re.fullmatch(rb'[01]{8}', result):
            raise ApiError('ACCOUNT_DEVICE_FAILED', '账号目录检测返回异常，请检查 ADB 和 root 权限')
        states = [result[index * 4:(index + 1) * 4] for index in range(len(BASES))]
        for base, state in zip(BASES, states):
            if state[:2] == b'11':
                return base
        details = []
        for base, state in zip(BASES, states):
            if state[0:1] == b'0':
                details.append(f'{base}：目录不存在或不可访问')
            else:
                missing = [DATABASE]
                details.append(f'{base}：缺少或无法读取 ' + '、'.join(missing))
        raise ApiError('ACCOUNT_DATA_NOT_FOUND', f'ADB {self.serial} 账号文件检测失败；' + '；'.join(details))

    def command(self, script, data=None):
        writing = data is not None
        if data is not None:
            # Windows adb 的 stdin 会把二进制 Ctrl-Z 当作 EOF；仅通过管道传输 Base64。
            script = f'base64 -d | ({script})'
            data = base64.b64encode(data) + b'\n'
        # shell v2 传递远端退出码；exec-out 会吞掉退出码，不能用于恢复事务。
        # 输出也使用 Base64，避免 Windows 标准流改写二进制换行或 Ctrl-Z。
        script = f'set -o pipefail; (\n{script}\n) | base64'
        if not writing:
            # MuMu Android 12 的 su 拒绝约 4 KiB 以上的命令参数。
            # 将脚本通过标准输入传给 sh，完整保留事务、退出码和输出编码。
            data = base64.b64encode(script.encode('utf-8')) + b'\n'
            script = 'set -o pipefail; base64 -d | sh'
        if self.use_su in ('uid', 'root'):
            command = ['su', '0' if self.use_su == 'uid' else 'root', 'sh', '-c', shlex.quote(script)]
        else:
            command = ['su', '-c', shlex.quote(script)] if self.use_su else ['sh', '-c', shlex.quote(script)]
        stage = '传输写入' if writing else '执行设备命令'
        try:
            result = subprocess.run([self.adb, '-s', self.serial, 'shell', '-T', ' '.join(command)],
                                    input=data, capture_output=True, timeout=30)
            if result.returncode != 0:
                raise ApiError('ACCOUNT_DEVICE_FAILED', f'ADB {stage}失败（退出码 {result.returncode}），请检查 root、shell 工具和文件权限')
            if len(result.stdout) > 6 * 1024 * 1024:
                raise ValueError()
            output = base64.b64decode(b''.join(result.stdout.split()), validate=True)
            if len(output) > 4 * 1024 * 1024:
                raise ValueError()
            return output
        except subprocess.TimeoutExpired:
            raise ApiError('ACCOUNT_DEVICE_FAILED', f'ADB {stage}超时，请检查模拟器连接或 root 授权') from None
        except FileNotFoundError:
            raise ApiError('ACCOUNT_DEVICE_FAILED', '找不到配置的 ADB 程序，请检查 AdbExecutable') from None
        except (OSError, subprocess.SubprocessError, ValueError):
            # 不转发 stderr、命令内容或带账号数据的底层异常。
            raise ApiError('ACCOUNT_DEVICE_FAILED', f'ADB {stage}或 Base64 传输校验失败，请检查 ADB、root 和 shell 工具') from None

    def stop(self):
        self.command(f'am force-stop {PACKAGE}')
        if self.command(f'pidof {PACKAGE} || true').strip():
            raise ApiError('GAME_RUNNING', '未确认游戏停止，已拒绝访问账号文件')

    def read(self, name):
        return self.command(f'cat {self.base}/{name}')

    @staticmethod
    def users(blob):
        try:
            with closing(sqlite3.connect(':memory:')) as db:
                db.deserialize(blob)
                db.execute('PRAGMA trusted_schema=OFF')
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError()
                columns = {r[1] for r in db.execute('PRAGMA table_info(users)')}
                if not {'uid', 'uname', 'access_key', 'pwd'} <= columns:
                    raise ValueError()
                return [{'uid': str(r[0]), 'name': str(r[1] or '')}
                        for r in db.execute('SELECT uid, uname FROM users LIMIT 100')]
        except (sqlite3.Error, ValueError):
            raise ApiError('ACCOUNT_SCHEMA_CHANGED', '账号数据库结构不兼容，已拒绝操作') from None

    def capture(self):
        self.stop()
        self.base = self.resolve_base()
        # 非空 WAL/回滚日志可能含未合并事务，不能当作完整快照。
        for suffix in ('-wal', '-journal'):
            if self.command(f'if test -s {self.base}/{DATABASE}{suffix}; then printf 1; else printf 0; fi').strip() != b'0':
                raise ApiError('ACCOUNT_DATABASE_BUSY', f'users.db{suffix} 非空，账号事务尚未合并，已拒绝不完整备份；请正常退出游戏后重试')
        blobs = {DATABASE: self.read(DATABASE)}
        for name in (SDK_PREFS, PLAYER_PREFS):
            if self.command(f'if test -f {self.base}/{name} && test -r {self.base}/{name}; then printf 1; else printf 0; fi').strip() == b'1':
                blobs[name] = self.read(name)
        users = self.users(blobs[DATABASE])
        if not users:
            raise ApiError('ACCOUNT_EMPTY', '未发现已保存的登录账号')
        try:
            if SDK_PREFS in blobs:
                ET.fromstring(blobs[SDK_PREFS])
            if PLAYER_PREFS not in blobs:
                return {name: base64.b64encode(blob).decode('ascii') for name, blob in blobs.items()}, users
            player = ET.fromstring(blobs[PLAYER_PREFS])
            selected = ET.Element('map')
            for element in player:
                if account_key(element.get('name', '')):
                    selected.append(element)
            blobs[PLAYER_PREFS] = ET.tostring(selected, encoding='utf-8', xml_declaration=True)
        except ET.ParseError:
            raise ApiError('ACCOUNT_SCHEMA_CHANGED', '登录偏好文件格式不兼容') from None
        return {name: base64.b64encode(blob).decode('ascii') for name, blob in blobs.items()}, users

    def restore(self, files):
        if DATABASE not in files or not set(files) <= set(FILES):
            raise ApiError('ACCOUNT_SCHEMA_CHANGED', '账号快照文件不兼容')
        try:
            blobs = {name: base64.b64decode(files[name], validate=True) for name in files}
            self.users(blobs[DATABASE])
            saved = ET.fromstring(blobs[PLAYER_PREFS]) if PLAYER_PREFS in blobs else None
            if SDK_PREFS in blobs:
                ET.fromstring(blobs[SDK_PREFS])
            if saved is not None and any(not account_key(e.get('name', '')) for e in saved):
                raise ValueError()
        except (ValueError, ET.ParseError):
            raise ApiError('ACCOUNT_SCHEMA_CHANGED', '账号快照损坏') from None
        self.stop()
        self.base = self.resolve_base()
        if saved is not None:
            try:
                current = self.command(f'if test -f {self.base}/{PLAYER_PREFS}; then cat {self.base}/{PLAYER_PREFS}; else printf "<map/>"; fi')
                player = ET.fromstring(current)
            except ET.ParseError:
                raise ApiError('ACCOUNT_SCHEMA_CHANGED', '设备偏好文件损坏') from None
            for element in list(player):
                if account_key(element.get('name', '')):
                    player.remove(element)
            player.extend(saved)
            blobs[PLAYER_PREFS] = ET.tostring(player, encoding='utf-8', xml_declaration=True)
        names = tuple(blobs)
        owner = self.command(f'stat -c %u:%g {self.base}/{DATABASE}').strip().decode('ascii')
        if not re.fullmatch(r'\d+:\d+', owner):
            raise ApiError('ACCOUNT_DEVICE_FAILED', '无法确认账号文件所有者')
        stage = f'{self.base}/.azurpilot-account-{uuid.uuid4().hex}'
        self.command(f'umask 077; mkdir {stage}')
        cleanup = True
        try:
            for index, name in enumerate(names):
                self.command(f'cat > {stage}/new{index}', blobs[name])
                if self.command(f'cat {stage}/new{index}') != blobs[name]:
                    raise ApiError('ACCOUNT_DEVICE_FAILED', '设备写入校验失败')
            # 在设备私有目录备份所有目标及 SQLite 边文件；失败则整组回滚。
            targets = (*names, DATABASE + '-wal', DATABASE + '-shm', DATABASE + '-journal',
                       *(name + '.bak' for name in names if name.startswith('shared_prefs/')))
            backup = '\n'.join(f'if test -e {self.base}/{name}; then cp -p {self.base}/{name} {stage}/old{i}; fi'
                               for i, name in enumerate(targets))
            apply = ' &&\n'.join(f'chown {owner} {stage}/new{i} && chmod 660 {stage}/new{i} && mv {stage}/new{i} {self.base}/{name}'
                              for i, name in enumerate(names))
            remove = ' &&\n'.join(f'rm -f {self.base}/{name}' for name in targets[len(names):])
            rollback = ' &&\n'.join(f'if test -e {stage}/old{i}; then cp -p {stage}/old{i} {self.base}/{name}; else rm -f {self.base}/{name}; fi'
                                 for i, name in enumerate(targets))
            self.command(f'set -e\n{backup}')
            # 回滚失败时保留设备私有恢复目录，不能销毁最后一份原数据。
            cleanup = False
            contexts = ' '.join(f'{self.base}/{name}' for name in names)
            self.command(f'({apply} &&\n{remove} &&\nrestorecon {contexts}) || {{ ({rollback}) && rm -rf {stage}; exit 1; }}')
            cleanup = True
            for name in names:
                if self.read(name) != blobs[name]:
                    cleanup = False
                    self.command(f'({rollback}) && rm -rf {stage}')
                    raise ApiError('ACCOUNT_DEVICE_FAILED', '恢复后校验失败，已回滚')
        finally:
            if cleanup:
                self.command(f'rm -rf {stage}')

    def launch(self):
        self.command(f'monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1 >/dev/null')
