"""大世界统计加密清单重封工具。

在**有意**修改 `module/statistics/opsi_secure.py` 之后、启动新版程序之前运行一次：
把 keyring 里记录的受保护文件哈希更新到当前内容并重新签名，避免防篡改机制
按设计清空全部大世界统计数据。需要与建立密钥时相同的 Windows 用户与主机。

注意：这是维护工具，不是绕过手段——只有拿到本机用户密钥（DPAPI）才能重封；
不要在未确认改动来源的情况下重封。

用法（在仓库根目录）:
    uv run python -m dev_tools.opsi_secure_reseal          # 只预览差异
    uv run python -m dev_tools.opsi_secure_reseal --confirm  # 写入新清单与签名
"""

import argparse
import time
from pathlib import Path

from deploy.atomic import atomic_write
from module.statistics import opsi_secure


def reseal(root: Path, confirm: bool, protected_files=None) -> int:
    """把受保护文件清单更新到当前内容并重新签名。

    Args:
        root: 仓库根目录（keyring 所在项目的根）。
        confirm: False 只预览差异；True 才写入。
        protected_files: 覆盖受保护清单（测试用）；None 用加密核心的默认清单。

    Returns:
        int: 0 = 完成或无需重封；1 = 环境/校验不满足，拒绝重封。
    """
    vault = opsi_secure.Vault(root=root, protected_files=protected_files)
    if not vault.keyring_present():
        print("未启用加密（没有 keyring），无需重封。")
        return 0
    keyring = vault._keyring()
    if keyring is None:
        print("keyring 无法解析，请检查 config/opsi_secure/keyring.json。")
        return 1
    try:
        dek = vault._dpapi(opsi_secure._b64d(keyring["wrapped_local"]), decrypt=True)
    except Exception as exc:
        print(f"无法用当前用户/主机解封密钥（{exc}），拒绝重封。")
        return 1
    if not vault._verify_keyring_mac(keyring, dek):
        print("keyring 的 MAC 校验失败（内容被改动或损坏），拒绝重封。")
        return 1

    files = keyring.setdefault("manifest", {}).setdefault("files", {})
    changed = []
    for path in vault._protected_files:
        new_hash = vault._file_hash(path)
        old_hash = files.get(str(path))
        if new_hash != old_hash:
            changed.append((str(path), old_hash, new_hash))

    if not changed:
        print("受保护清单已与当前代码一致，无需重封。")
        return 0

    for path, old_hash, new_hash in changed:
        print(f"受保护文件: {path}\n  记录: {old_hash}\n  当前: {new_hash}")
    if not confirm:
        print("（预览模式）如需写入，请追加 --confirm。注意：重封后旧哈希不再具有防护效果。")
        return 0

    for path, _, new_hash in changed:
        files[path] = new_hash
    keyring["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    keyring["mac"] = vault._keyring_mac(keyring, dek)
    atomic_write(str(vault.keyring_path), opsi_secure._canonical(keyring))
    print(f"已重封 {len(changed)} 个受保护文件的哈希并更新签名。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="确认写入新的清单与签名")
    args = parser.parse_args()

    root = Path.cwd().resolve()
    print(f"仓库根目录: {root}")
    return reseal(root, confirm=args.confirm)


if __name__ == "__main__":
    raise SystemExit(main())
