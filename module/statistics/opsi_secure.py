"""大世界统计数据的文件级加密。

把「资源统计」中与大世界相关的数据在落盘时加密，ALAS / WebUI 读取时代码内部
自动解密、照常展示，无需任何口令或解锁操作。保护的是文件本身：

- 数据文件被拷走、被备份带走、被 sqlite / 文本工具直接打开时只能看到密文；
- 加密密钥由 Windows DPAPI 绑定当前用户与主机，换机器 / 换账户后无法解开；
- 每条密文带认证标签（AES-GCM），文件被改动后读不出内容、不会静默采用。

加密范围：

- cl1_data.db 中的大世界战斗字段（战斗、明石、行动力、凭证、耄耋相接、塞壬装置）
  存放在 secure_json 列；委托、科研等非大世界字段保持明文。
- azurstats_local.db 的 opsi_items 明细（物品与数量等列进 secure_payload）；
  resource_snapshots 的行动力 / 作战补给凭证 / 特别兑换凭证三列进 opsi_payload。
- log/cl1/<实例>/ship_exp_data.json（舰船经验战斗数据）整文件加密。
- log/azurstat_meowofficer_farming*.csv（短猫收益缓存）整文件加密。

密钥生命周期：首次写入大世界数据时自动生成随机根密钥，用 DPAPI 封装后保存到
config/opsi_secure/keyring.json（随每日备份一起走，恢复后仍可在本机解开），并
在后台把已有明文数据迁移为密文。各数据集的子密钥由 HKDF 从根密钥分离。

防篡改：受保护清单里的文件（加密核心自身）校验到变化、或 keyring 内容被改动时，
清空全部受保护数据并删除密钥（2026-10-04 用户定：修改代码就把数据全部清空）。
DPAPI 不可用的平台（非 Windows）保持旧行为：不启用加密并记日志。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from Crypto.Cipher import AES

from deploy.atomic import atomic_write
from module.logger import logger

# 每类数据一个子密钥；kind 同时作为 AEAD 的附加认证数据。
KINDS = ('cl1', 'ships', 'loot', 'res')

# cl1_data.db 月度快照中属于大世界战斗数据的字段。
# 其余字段（委托、科研、钻石委托）与非大世界数据保持明文；未知字段一律按
# 明文处理，新增大世界字段时必须同步加入本集合。
CL1_SECURE_FIELDS = frozenset({
    'battle_count',
    'akashi_encounters',
    'akashi_ap',
    'akashi_ap_entries',
    'ap_snapshots',
    'last_ap_notification',
    'yellow_coin_snapshots',
    'coins_snapshots',
    'coins_history_version',
    'coins_cleanup_version',
    'meow_battle_raw_count',
    'meow_battle_count',
    'meow_round_times',
    'meow_battle_times',
    'meow_hazard_stats',
    'siren_research_devices',
    'siren_research_device_entries',
})

# opsi_items 中并入密文载荷的列；其余列保留明文用于筛选与去重。
LOOT_SECURE_FIELDS = ('server', 'zone', 'zone_type', 'zone_id', 'item', 'amount', 'tag')

# resource_snapshots 中并入密文载荷的三列（都是大世界货币）。
RES_SECURE_FIELDS = ('action_point', 'yellow_coin', 'purple_coin')

BLOB_PREFIX = 'OPSIV1.'
WRAPPER_KEY = '__opsi_secure_v1__'
# 读取路径在密文暂不可解密时写入数据字典的临时标记；写回路径据此保留原密文。
MISSING_MARKER = '__opsi_secure_missing__'

# resource_snapshots 迁移时的分片大小：单次事务锁表时间保持毫秒级。
MIGRATION_CHUNK = 5000


class VaultError(RuntimeError):
    """加密保险库的内部错误：密文损坏或状态不一致。"""


class VaultLocked(VaultError):
    """已启用加密但当前环境拿不到根密钥（DPAPI 不可用或换了机器 / 账户）。"""


def partition_cl1(data: dict) -> tuple[dict, dict]:
    """按字段清单把 cl1 月度快照拆为（明文部分, 加密部分）。"""
    secure = {key: value for key, value in data.items() if key in CL1_SECURE_FIELDS}
    public = {key: value for key, value in data.items() if key not in CL1_SECURE_FIELDS}
    return public, secure


def _canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _json_text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def _b64d(text: str) -> bytes:
    return base64.b64decode(text, validate=True)


def _aes_encrypt(key: bytes, data: bytes, aad: str) -> bytes:
    cipher = AES.new(key, AES.MODE_GCM, nonce=os.urandom(12))
    cipher.update(aad.encode('utf-8'))
    ciphertext, tag = cipher.encrypt_and_digest(data)
    return cipher.nonce + ciphertext + tag


def _aes_decrypt(key: bytes, blob: bytes, aad: str) -> bytes:
    if len(blob) < 12 + 16:
        raise VaultError('密文长度不正确')
    nonce, payload, tag = blob[:12], blob[12:-16], blob[-16:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(aad.encode('utf-8'))
    try:
        return cipher.decrypt_and_verify(payload, tag)
    except ValueError as exc:
        # 认证失败 = 口令不符（不存在口令场景）或文件被改动。
        raise VaultError('密文校验失败（数据被修改或不完整）') from exc


def _secure_unlink(path: Path) -> None:
    """尽量覆写后删除文件，避免密钥或受保护数据残留在空闲块里。"""
    try:
        size = path.stat().st_size
        with open(path, 'r+b') as file:
            file.write(b'\0' * size)
            file.flush()
            os.fsync(file.fileno())
    except OSError:
        pass
    try:
        path.unlink()
    except OSError:
        pass


class Vault:
    """大世界统计的密钥与密文保险库（进程内单例使用，可按根目录注入测试）。

    根密钥只在内存中保留明文；落盘的是 DPAPI 封装后的 blob。首次写入时自动
    建立密钥并后台迁移旧数据，读取与写入对调用方透明。
    """

    def __init__(self, root=None, protected_files=None, clock=time.time, background_migration=True):
        """初始化保险库。

        Args:
            root: 项目根目录；缺省取本文件所在仓库根目录。
            protected_files: 防篡改清单；缺省只包含加密核心文件自身。
            clock: 时间源，测试可注入。
            background_migration: 是否在首次就绪后自动后台迁移旧明文数据（测试可关）。
        """
        self.root = Path(root).resolve() if root else Path(__file__).resolve().parents[2]
        self._clock = clock
        self._protected_files = [Path(p) for p in (protected_files or [Path(__file__).resolve()])]
        self.directory = self.root / 'config' / 'opsi_secure'
        self.keyring_path = self.directory / 'keyring.json'
        self.wipe_path = self.directory / 'wipe.json'
        self.cl1_db = self.root / 'config' / 'cl1_data.db'
        self.azurstats_db = self.root / 'config' / 'azurstats_local.db'
        self._background_migration = bool(background_migration)
        self._lock = threading.RLock()
        self._dek: bytes | None = None
        self._integrity_checked = False
        self._init_failed = False
        self._migration_kicked = False
        self._read_warned: set[str] = set()
        self._dropped: dict[str, int] = {}
        self._keyring_cache: tuple | None = None

    # ---- 状态与密钥 ----

    def _keyring(self) -> dict | None:
        """读取并解析 keyring；文件不存在或损坏返回 None。带指纹缓存。"""
        try:
            stat = self.keyring_path.stat()
        except OSError:
            self._keyring_cache = None
            return None
        signature = (stat.st_mtime_ns, stat.st_size)
        if self._keyring_cache and self._keyring_cache[0] == signature:
            return self._keyring_cache[1]
        try:
            data = json.loads(self.keyring_path.read_bytes())
            if not isinstance(data, dict) or data.get('version') != 1:
                raise ValueError('keyring 版本不正确')
        except (OSError, ValueError) as exc:
            logger.error(f'[统计-加密] 读取密钥文件失败: {exc}')
            self._keyring_cache = (signature, None)
            return None
        self._keyring_cache = (signature, data)
        return data

    def keyring_present(self) -> bool:
        """keyring 文件是否存在（含损坏的情况）。"""
        return self.keyring_path.exists()

    def is_configured(self) -> bool:
        """是否已启用文件加密（keyring 可解析）。"""
        self.ensure_integrity()
        return self._keyring() is not None

    def writer_ready(self) -> bool:
        """当前进程能否加密读写：已启用则取回根密钥，未启用则先自动建立。"""
        return self.ensure_ready()

    def ensure_ready(self) -> bool:
        """确保根密钥可用；首次调用会建立密钥并启动后台迁移。"""
        with self._lock:
            self.ensure_integrity()
            if self._dek is not None:
                return True
            keyring = self._keyring()
            if keyring is None:
                if self.keyring_present():
                    # 文件在但解析不了：可能被改动或写坏，不自动重建。
                    return False
                if self._init_failed:
                    return False
                if not self._init_keyring():
                    return False
                keyring = self._keyring()
            if not self._unwrap_keyring(keyring):
                return False
            self._kick_migration()
            return True

    def _dpapi(self, data: bytes, decrypt: bool = False) -> bytes:
        """调用 Windows DPAPI 封装 / 解封（用户 + 主机绑定）。"""
        from module.runtime.account_local import dpapi
        return dpapi(data, decrypt=decrypt)

    def _init_keyring(self) -> bool:
        """生成根密钥并写出 keyring；DPAPI 不可用时保持旧行为（不加密）。"""
        dek = os.urandom(32)
        try:
            wrapped = self._dpapi(dek)
        except Exception as exc:
            self._init_failed = True
            logger.warning(f'[统计-加密] 本机密钥保护不可用（{exc}），大世界统计暂不加密')
            return False
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        keyring = {
            'version': 1,
            'wrapped_local': _b64e(wrapped),
            'manifest': {'files': {str(path): self._file_hash(path) for path in self._protected_files}},
            'created': now,
            'updated': now,
        }
        keyring['mac'] = self._keyring_mac(keyring, dek)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            atomic_write(str(self.keyring_path), _canonical(keyring))
            try:
                os.chmod(self.keyring_path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            self._init_failed = True
            logger.error(f'[统计-加密] 写入密钥文件失败，暂不加密: {exc}')
            return False
        self._keyring_cache = None
        self._dek = dek
        logger.info('[统计-加密] 已启用大世界统计数据文件加密（密钥绑定当前 Windows 用户）')
        return True

    def _unwrap_keyring(self, keyring: dict) -> bool:
        """用 DPAPI 取回根密钥并校验 keyring 完整性。"""
        try:
            dek = self._dpapi(_b64d(keyring['wrapped_local']), decrypt=True)
            if len(dek) != 32:
                raise VaultError('解封后的密钥长度不正确')
        except Exception as exc:
            if not self._read_warned:
                self._read_warned.add('unavailable')
                logger.warning(f'[统计-加密] 当前环境无法解开数据密钥（{exc}），大世界统计暂不可读')
            return False
        if not self._verify_keyring_mac(keyring, dek):
            self._wipe('密钥文件校验失败')
            return False
        self._dek = dek
        return True

    @staticmethod
    def _file_hash(path: Path) -> str | None:
        """受保护文件的内容哈希；统一换行后计算，避免跨平台检出差异。"""
        try:
            raw = Path(path).read_bytes().replace(b'\r\n', b'\n')
        except OSError:
            return None
        return hashlib.sha256(raw).hexdigest()

    def ensure_integrity(self) -> None:
        """校验受保护文件；发现变化立即清空受保护数据。每进程只检查一次。"""
        with self._lock:
            if self._integrity_checked:
                return
            self._integrity_checked = True
            keyring = self._keyring()
            if keyring is None:
                return
            recorded = (keyring.get('manifest') or {}).get('files') or {}
            for path in self._protected_files:
                expected = recorded.get(str(path))
                if expected is None:
                    continue
                actual = self._file_hash(path)
                if actual is None or not hmac.compare_digest(str(expected), actual):
                    self._wipe(f'受保护代码已修改（{Path(path).name}）')
                    return

    def status(self) -> dict:
        """返回对外的加密状态（调试与接口查询用）。"""
        self.ensure_integrity()
        keyring = self._keyring()
        last_wipe = None
        try:
            if self.wipe_path.exists():
                last_wipe = json.loads(self.wipe_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            last_wipe = None
        return {
            'configured': keyring is not None,
            'keyringPresent': self.keyring_present(),
            'lastWipe': last_wipe,
            'dropped': dict(self._dropped),
        }

    def record_dropped(self, channel: str) -> None:
        """记录一条因密钥不可用而放弃写入的数据，供状态页提示。"""
        with self._lock:
            self._dropped[channel] = self._dropped.get(channel, 0) + 1

    # ---- 密文读写 ----

    def _subkey(self, kind: str) -> bytes:
        if kind not in KINDS:
            raise VaultError(f'未知的数据种类: {kind}')
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                    info=f'opsi-stats/v1/{kind}'.encode('utf-8')).derive(self._dek)

    def _keyring_mac(self, keyring: dict, dek: bytes) -> str:
        payload = {key: value for key, value in keyring.items() if key != 'mac'}
        mac_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                       info=b'opsi-stats/v1/keyring-mac').derive(dek)
        return hmac.new(mac_key, _canonical(payload), hashlib.sha256).hexdigest()

    def _verify_keyring_mac(self, keyring: dict, dek: bytes) -> bool:
        expected = str(keyring.get('mac') or '')
        return bool(expected) and hmac.compare_digest(expected, self._keyring_mac(keyring, dek))

    def seal(self, kind: str, obj) -> str:
        """把对象加密为可入库的文本密文。

        Raises:
            VaultError: 尚未启用加密（调用方应检查 is_configured）。
            VaultLocked: 已启用但当前环境拿不到根密钥。
        """
        with self._lock:
            if not self.is_configured():
                raise VaultError('尚未启用加密')
            if not self.ensure_ready():
                raise VaultLocked('数据密钥不可用')
            blob = _aes_encrypt(self._subkey(kind), _canonical(obj), aad=f'opsi-stats/v1/{kind}')
            return BLOB_PREFIX + _b64e(blob)

    def open_(self, kind: str, blob: str):
        """解开 seal() 生成的密文，返回原始对象。"""
        with self._lock:
            if not self.is_configured():
                raise VaultError('尚未启用加密')
            if not self.ensure_ready():
                raise VaultLocked('数据密钥不可用')
            if not isinstance(blob, str) or not blob.startswith(BLOB_PREFIX):
                raise VaultError('不是本保险库的密文')
            raw = _aes_decrypt(self._subkey(kind), _b64d(blob[len(BLOB_PREFIX):]), aad=f'opsi-stats/v1/{kind}')
            return json.loads(raw.decode('utf-8'))

    def open_or_none(self, kind: str, blob):
        """读取密文；密钥不可用或被改动时返回 None（告警每类只记一次，读取方自行降级）。"""
        try:
            return self.open_(kind, blob)
        except (VaultLocked, VaultError) as exc:
            if kind not in self._read_warned:
                self._read_warned.add(kind)
                logger.warning(f'[统计-加密] {kind} 数据暂不可解密，读取降级: {exc}')
            return None

    # ---- 清空 ----

    def wipe(self, reason: str) -> None:
        """清空受保护数据并删除密钥（防篡改触发与测试使用）。"""
        self._wipe(reason)

    def _wipe(self, reason: str) -> None:
        with self._lock:
            logger.error(f'[统计-加密] 清空全部受保护的大世界统计数据：{reason}')
            try:
                self._clear_stores()
            except Exception:
                logger.exception('[统计-加密] 清空受保护数据时出错（继续删除密钥）')
            self._dek = None
            self._keyring_cache = None
            _secure_unlink(self.keyring_path)
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                atomic_write(str(self.wipe_path), _canonical(
                    {'reason': reason, 'at': time.strftime('%Y-%m-%d %H:%M:%S')}))
            except OSError:
                pass
            self._integrity_checked = True

    def _clear_stores(self) -> None:
        """清空各存储中的受保护数据；保留非大世界的明文部分。"""
        # cl1：只清大世界字段的密文，委托 / 科研等明文保留。
        if self.cl1_db.exists():
            try:
                with closing(sqlite3.connect(self.cl1_db, timeout=30)) as conn:
                    columns = {row[1] for row in conn.execute('PRAGMA table_info(cl1_data)')}
                    if 'secure_json' in columns:
                        conn.execute('UPDATE cl1_data SET secure_json = NULL WHERE secure_json IS NOT NULL')
                    conn.commit()
            except sqlite3.Error:
                logger.exception('[统计-加密] 清空 cl1 大世界字段失败')
        # 掉落明细整表都是大世界数据；资源快照只清三个大世界列。
        if self.azurstats_db.exists():
            try:
                with closing(sqlite3.connect(self.azurstats_db, timeout=30)) as conn:
                    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'opsi_items' in tables:
                        conn.execute('DELETE FROM opsi_items')
                    if 'resource_snapshots' in tables:
                        columns = {row[1] for row in conn.execute('PRAGMA table_info(resource_snapshots)')}
                        if 'opsi_payload' in columns:
                            conn.execute('UPDATE resource_snapshots SET opsi_payload = NULL, '
                                         'action_point = NULL, yellow_coin = NULL, purple_coin = NULL')
                        else:
                            conn.execute('UPDATE resource_snapshots SET '
                                         'action_point = NULL, yellow_coin = NULL, purple_coin = NULL')
                    conn.commit()
            except sqlite3.Error:
                logger.exception('[统计-加密] 清空掉落明细 / 资源快照密文失败')
        for path in self._ship_files() + self._farming_files():
            _secure_unlink(path)

    # ---- 旧数据迁移 ----

    def _ship_files(self) -> list[Path]:
        return sorted((self.root / 'log' / 'cl1').glob('*/ship_exp_data.json'))

    def _farming_files(self) -> list[Path]:
        return sorted((self.root / 'log').glob('azurstat_meowofficer_farming*.csv'))

    def _kick_migration(self) -> None:
        """首次就绪后后台迁移旧明文数据；每进程只启动一次。"""
        if not self._background_migration or self._migration_kicked:
            return
        self._migration_kicked = True

        def run():
            try:
                counts = self.ensure_migrated()
                if any(counts.get(key) for key in ('cl1', 'loot', 'res', 'ships', 'files')):
                    logger.info(f'[统计-加密] 旧数据加密迁移完成: {counts}')
            except Exception:
                logger.exception('[统计-加密] 后台加密迁移失败（下次启动重试）')

        threading.Thread(target=run, name='opsi-secure-migrate', daemon=True).start()

    def ensure_migrated(self) -> dict:
        """把旧格式的明文数据迁移为密文；幂等，可在任一进程重复调用。"""
        self.ensure_integrity()
        counts = {'cl1': 0, 'loot': 0, 'res': 0, 'ships': 0, 'files': 0, 'skipped': False}
        if not self.is_configured() or not self.ensure_ready():
            counts['skipped'] = True
            return counts
        counts['cl1'] = self._migrate_cl1()
        totals = self._migrate_azurstats()
        counts['loot'], counts['res'] = totals
        counts['ships'] = self._migrate_ship_files()
        counts['files'] = self._migrate_farming_csvs()
        return counts

    def _migrate_cl1(self) -> int:
        if not self.cl1_db.exists():
            return 0
        migrated = 0
        with closing(sqlite3.connect(self.cl1_db, timeout=30)) as conn:
            try:
                conn.execute('BEGIN IMMEDIATE')
                columns = {row[1] for row in conn.execute('PRAGMA table_info(cl1_data)')}
                if not columns:
                    return 0
                if 'secure_json' not in columns:
                    conn.execute('ALTER TABLE cl1_data ADD COLUMN secure_json TEXT')
                rows = conn.execute(
                    'SELECT instance, month, data_json FROM cl1_data '
                    'WHERE secure_json IS NULL AND data_json IS NOT NULL'
                ).fetchall()
                for instance, month, data_json in rows:
                    try:
                        data = json.loads(data_json)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(data, dict):
                        continue
                    public, secure = partition_cl1(data)
                    blob = self.seal('cl1', secure)
                    if self.open_('cl1', blob) != secure:
                        raise VaultError(f'迁移校验失败: {instance} {month}')
                    conn.execute(
                        'UPDATE cl1_data SET data_json = ?, secure_json = ? WHERE instance = ? AND month = ?',
                        (_json_text(public), blob, instance, month),
                    )
                    migrated += 1
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        if migrated:
            self._vacuum(self.cl1_db)
        return migrated

    def _migrate_azurstats(self) -> tuple[int, int]:
        if not self.azurstats_db.exists():
            return 0, 0
        loot = res = 0
        with closing(sqlite3.connect(self.azurstats_db, timeout=30)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'opsi_items' in tables:
                columns = {row[1] for row in conn.execute('PRAGMA table_info(opsi_items)')}
                if 'secure_payload' not in columns:
                    conn.execute('ALTER TABLE opsi_items ADD COLUMN secure_payload TEXT')
                    conn.commit()
                try:
                    conn.execute('BEGIN IMMEDIATE')
                    rows = conn.execute(
                        'SELECT id, server, zone, zone_type, zone_id, item, amount, tag FROM opsi_items '
                        'WHERE secure_payload IS NULL AND (item IS NOT NULL OR amount IS NOT NULL '
                        'OR zone IS NOT NULL OR server IS NOT NULL OR tag IS NOT NULL)'
                    ).fetchall()
                    for row in rows:
                        row_id = row[0]
                        payload = dict(zip(LOOT_SECURE_FIELDS, row[1:]))
                        blob = self.seal('loot', payload)
                        if self.open_('loot', blob) != payload:
                            raise VaultError(f'掉落明细迁移校验失败: id={row_id}')
                        conn.execute(
                            'UPDATE opsi_items SET secure_payload = ?, server = NULL, zone = NULL, '
                            'zone_type = NULL, zone_id = NULL, item = NULL, amount = NULL, tag = NULL '
                            'WHERE id = ?',
                            (blob, row_id),
                        )
                        loot += 1
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
            if 'resource_snapshots' in tables:
                columns = {row[1] for row in conn.execute('PRAGMA table_info(resource_snapshots)')}
                if 'opsi_payload' not in columns:
                    conn.execute('ALTER TABLE resource_snapshots ADD COLUMN opsi_payload TEXT')
                    conn.commit()
                # 资源快照可能很多，分片提交，避免长时间持锁影响快照写入。
                while True:
                    try:
                        conn.execute('BEGIN IMMEDIATE')
                        rows = conn.execute(
                            'SELECT id, action_point, yellow_coin, purple_coin FROM resource_snapshots '
                            'WHERE opsi_payload IS NULL AND (action_point IS NOT NULL '
                            'OR yellow_coin IS NOT NULL OR purple_coin IS NOT NULL) LIMIT ?',
                            (MIGRATION_CHUNK,),
                        ).fetchall()
                        for row in rows:
                            row_id = row[0]
                            payload = dict(zip(RES_SECURE_FIELDS, row[1:]))
                            blob = self.seal('res', payload)
                            if self.open_('res', blob) != payload:
                                raise VaultError(f'资源快照迁移校验失败: id={row_id}')
                            conn.execute(
                                'UPDATE resource_snapshots SET opsi_payload = ?, action_point = NULL, '
                                'yellow_coin = NULL, purple_coin = NULL WHERE id = ?',
                                (blob, row_id),
                            )
                            res += 1
                        conn.commit()
                    except BaseException:
                        conn.rollback()
                        raise
                    if len(rows) < MIGRATION_CHUNK:
                        break
        if loot or res:
            self._vacuum(self.azurstats_db)
        return loot, res

    def _migrate_ship_files(self) -> int:
        migrated = 0
        for path in self._ship_files():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get(WRAPPER_KEY):
                continue
            blob = self.seal('ships', data)
            if self.open_('ships', blob) != data:
                raise VaultError(f'舰船经验迁移校验失败: {path}')
            atomic_write(str(path), _json_text({WRAPPER_KEY: True, 'payload': blob}))
            migrated += 1
        return migrated

    def _migrate_farming_csvs(self) -> int:
        migrated = 0
        for path in self._farming_files():
            text = path.read_text(encoding='utf-8')
            if text.startswith(BLOB_PREFIX):
                continue
            lines = [line for line in text.splitlines() if line.strip()]
            if not lines:
                continue
            header, rows = lines[0].split(','), [line.split(',') for line in lines[1:]]
            payload = {'header': header, 'rows': rows}
            blob = self.seal('loot', payload)
            if self.open_('loot', blob) != payload:
                raise VaultError(f'farming 汇总迁移校验失败: {path}')
            atomic_write(str(path), blob)
            migrated += 1
        return migrated

    @staticmethod
    def _vacuum(path: Path) -> None:
        """迁移后重建数据库文件，清掉仍带明文数据的历史页。"""
        try:
            with closing(sqlite3.connect(path, timeout=30)) as conn:
                conn.execute('VACUUM')
        except sqlite3.Error as exc:
            logger.warning(f'[统计-加密] VACUUM 未完成（旧页面可能残留）: {exc}')


# —— 进程单例与便捷入口 ——

_VAULT: Vault | None = None


def get_vault() -> Vault:
    """获取当前进程的保险库单例。"""
    global _VAULT
    if _VAULT is None:
        _VAULT = Vault()
    return _VAULT


def set_vault(vault: Vault | None) -> None:
    """替换进程保险库（测试注入或无密钥环境复位用）。"""
    global _VAULT
    _VAULT = vault


def is_configured() -> bool:
    return get_vault().is_configured()


def writer_ready() -> bool:
    return get_vault().writer_ready()


def seal(kind: str, obj) -> str:
    return get_vault().seal(kind, obj)


def open_(kind: str, blob):
    return get_vault().open_(kind, blob)


def open_or_none(kind: str, blob):
    return get_vault().open_or_none(kind, blob)


def ensure_migrated() -> dict:
    return get_vault().ensure_migrated()


def status() -> dict:
    return get_vault().status()


def record_dropped(channel: str) -> None:
    get_vault().record_dropped(channel)
