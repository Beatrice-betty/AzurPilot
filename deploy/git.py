import random
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests

from deploy.config import GIT_OVER_CDN_FALLBACK_REPOSITORIES, DeployConfig, ExecutionError
from deploy.git_over_cdn.client import GitOverCdnClient
from deploy.git_over_cdn.endpoints import CLOUDFLARE_UPDATE_URLS, FALLBACK_UPDATE_URLS
from deploy.logger import logger
from deploy.utils import *


CLOUD_UPDATE_CONTROL_URL = 'https://alas-apiv2.nanoda.work/api/updata'
CLOUD_FORCE_UPDATE_CONTROL_URL = 'https://alas-apiv2.nanoda.work/api/force_update'


def peer_fallback_repository(url: str) -> Optional[str]:
    """若当前 URL 为备选国内镜像，获取列表中的同级备用镜像 URL 用于分流容灾。"""
    if not url:
        return None
    cleaned = url.strip().rstrip('/').removesuffix('.git')
    for index, repo in enumerate(GIT_OVER_CDN_FALLBACK_REPOSITORIES):
        if cleaned == repo.rstrip('/'):
            return GIT_OVER_CDN_FALLBACK_REPOSITORIES[(index + 1) % len(GIT_OVER_CDN_FALLBACK_REPOSITORIES)]
    return None


class GitManager(DeployConfig):
    """Git 仓库与更新管理类，负责源码拉取、分支同步及 GitOverCDN 更新。"""

    @cached_property
    def git(self):
        """获取 Git 可执行文件路径。

        Returns:
            str: Git 可执行文件绝对路径或回退命令 'git'。
        """
        exe = self.filepath('GitExecutable')
        if os.path.exists(exe):
            return exe

        logger.warning(f'GitExecutable: {exe} does not exist, use `git` instead')
        return 'git'

    @staticmethod
    def remove(file):
        """安全删除指定文件。

        Args:
            file (str): 待删除的文件路径。
        """
        try:
            os.remove(file)
            logger.info(f'Removed file: {file}')
        except FileNotFoundError:
            logger.info(f'File not found: {file}')

    @staticmethod
    def git_user_agent():
        """生成随机的 git User-Agent，绕开镜像仓库对固定/异常 git UA 的封禁。

        gitcode 等仓库会对命中黑名单的 UA 返回 418。官方 git 主版本只有 2.x，
        Windows 构建形如 2.x.y.windows.z，不带多余尾段；这里只产出形态真实的
        版本号（避免一眼假的 git/3.x 或五段式 UA 被按特征封禁），同时把采样
        空间撑到约 4×10³ 种（minor 24~63、patch 0~9、build 1~9 的笛卡尔积），
        每次取值都不重复，任何单一 UA 都难以累积成可封禁的固定指纹。

        Returns:
            str: 伪装的 Git User-Agent 字符串。
        """
        while True:
            minor = random.randint(24, 63)
            patch = random.randint(0, 9)
            # 少部分用官方跨平台版（大量 CI/服务器请求即此形态），多数用 Git for Windows
            if random.random() < 0.2:
                ua = f'git/2.{minor}.{patch}'
            else:
                build = random.randint(1, 9)
                ua = f'git/2.{minor}.{patch}.windows.{build}'
            if ua != 'git/2.51.0.windows.2':
                break
        return ua

    def get_remote_url(self, source: str) -> str:
        """获取指定远程源的 URL。"""
        try:
            res = subprocess.run(
                [self.git, 'remote', 'get-url', source],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return res.stdout.strip() if res.returncode == 0 else ''
        except Exception:
            return ''

    def _fetch_with_retry(self, source, branch, max_retry=5, delay=2):
        """带 UA 重试与同级镜像容灾的 git fetch。

        gitcode 等仓库会返回 418 拦截特定 UA，此时自动更换 UA 重试；
        若多次重试仍失败且当前 remote 为国内镜像源（如 GitCode/CNB），
        自动切换至同级备用分流镜像重试，避免单一仓库受风控导致更新中断。

        Args:
            source: 远程源名称。
            branch: 分支名称。
            max_retry: 最大尝试次数（含首次）。
            delay: 重试间隔秒数。

        Raises:
            ExecutionError: 所有尝试均失败时抛出。
        """
        ua = self.git_user_agent()
        for i in range(max_retry):
            git = f'"{self.git}" -c http.userAgent={ua}'
            logger.info(f'Use git User-Agent: {ua}')
            if self.execute(f'{git} fetch {source} {branch}'):
                return
            logger.warning(f'git fetch failed with UA {ua}, attempt {i + 1}/{max_retry}')
            if i < max_retry - 1:
                # 重试间隔加随机抖动，避免暴露固定的失败-重试节奏
                time.sleep(delay * random.uniform(0.5, 1.8))
                ua = self.git_user_agent()

        current_url = self.get_remote_url(source)
        peer_url = peer_fallback_repository(current_url)
        if peer_url:
            logger.warning(
                f'当前镜像源 {current_url} 获取失败（可能触发限流或风控），自动切换至同级分流镜像 {peer_url} 重试'
            )
            git = f'"{self.git}" -c http.userAgent={self.git_user_agent()}'
            if self.execute(f'{git} remote set-url {source} {peer_url}', allow_failure=True):
                if hasattr(self, 'Repository'):
                    self.Repository = peer_url
                ua = self.git_user_agent()
                for i in range(max_retry):
                    git = f'"{self.git}" -c http.userAgent={ua}'
                    logger.info(f'Use git User-Agent: {ua}')
                    if self.execute(f'{git} fetch {source} {branch}'):
                        return
                    logger.warning(f'备用镜像 git fetch 失败 (UA: {ua}), 尝试 {i + 1}/{max_retry}')
                    if i < max_retry - 1:
                        time.sleep(delay * random.uniform(0.5, 1.8))
                        ua = self.git_user_agent()
        raise ExecutionError

    def git_repository_init(
            self, repo, source='origin', branch='master', proxy='', ssl_verify=True
    ):
        """初始化或更新本地 Git 仓库并拉取指定分支。

        Args:
            repo (str): 远端仓库地址。
            source (str): 远端源名称，默认为 'origin'。
            branch (str): 分支名称，默认为 'master'。
            proxy (str): HTTP/HTTPS 代理地址。
            ssl_verify (bool): 是否校验 SSL 证书。
        """
        # 所有 git 命令统一带随机 UA，绕过 gitcode 等仓库对特定 UA 的 418 封禁
        git = f'"{self.git}" -c http.userAgent={self.git_user_agent()}'

        logger.hr('Git Init', 1)
        self.execute(f'{git} init')

        logger.hr('Set Git Proxy', 1)
        if proxy:
            self.execute(f'{git} config --local http.proxy {proxy}')
            self.execute(f'{git} config --local https.proxy {proxy}')
        else:
            self.execute(f'{git} config --local --unset http.proxy', allow_failure=True)
            self.execute(f'{git} config --local --unset https.proxy', allow_failure=True)

        if ssl_verify:
            self.execute(f'{git} config --local http.sslVerify true', allow_failure=True)
        else:
            self.execute(f'{git} config --local http.sslVerify false', allow_failure=True)

        logger.hr('Set Git Repository', 1)
        if not self.execute(f'{git} remote set-url {source} {repo}', allow_failure=True):
            self.execute(f'{git} remote add {source} {repo}')

        logger.hr('Fetch Repository Branch', 1)
        self._fetch_with_retry(source, branch)

        logger.hr('Pull Repository Branch', 1)
        # 尚无 HEAD 的首次安装交给 merge 处理；已有提交必须被目标分支包含。
        if self.execute(f'{git} rev-parse --verify HEAD', allow_failure=True):
            if not self.execute(f'{git} merge-base --is-ancestor HEAD {source}/{branch}', allow_failure=True):
                logger.warning('本地存在目标分支未包含的提交，保留本地分支并跳过更新')
                return self.use_local_version()
        # 只快进到刚获取的版本；分叉、未提交修改或锁冲突交由 Git 拒绝，不强制覆盖。
        self.execute(f'{git} merge --ff-only {source}/{branch}')

        logger.hr('Show Version', 1)
        self.execute(f'{git} --no-pager log --no-merges -1')

    @property
    def goc_client(self):
        """获取 GitOverCDN 客户端实例。

        Returns:
            GitOverCdnClient: 已配置好的客户端实例。
        """
        client = GitOverCdnClient(
            url=CLOUDFLARE_UPDATE_URLS,
            fallback_urls=FALLBACK_UPDATE_URLS,
            folder=self.root_filepath,
            source='origin',
            branch=self.Branch,
            git=self.git,
        )
        client.logger = logger
        return client

    @staticmethod
    def _cloud_update_enabled(url, name):
        """单次读取三态控制值，拒绝重定向，避免故障端点拖延启动。"""
        logger.info(f'检查云端{name}开关: {url}')
        try:
            resp = requests.get(
                url, timeout=5, headers={'User-Agent': 'alas AzurPilot'}, allow_redirects=False,
            )
            resp.raise_for_status()
            if resp.status_code != 200:
                logger.warning(
                    f'云端{name}状态未知: HTTP {resp.status_code}, '
                    f'Location={resp.headers.get("Location", "")}; 不跟随重定向'
                )
                return None
            text = resp.text.strip()
            try:
                data = resp.json()
            except ValueError:
                data = text
        except Exception as exc:
            logger.warning(f'云端{name}状态未知: {type(exc).__name__}: {exc}')
            return None

        if data is True or (isinstance(data, str) and data.lower() in ('true', 'ture')):
            logger.info(f'云端{name}开关已开启')
            return True
        if data is False or (isinstance(data, str) and data.lower() in ('false', 'fales')):
            logger.info(f'云端{name}开关已关闭')
            return False
        logger.warning(f'云端{name}状态未知: 返回内容格式无效 {text[:200]!r}')
        return None

    @staticmethod
    def cloud_auto_update_enabled():
        """检查云端自动更新开关是否启用。

        Returns:
            bool | None: True 启用，False 禁用，网络异常返回 None。
        """
        return GitManager._cloud_update_enabled(CLOUD_UPDATE_CONTROL_URL, '自动更新')

    @staticmethod
    def cloud_force_update_enabled():
        """检查云端强制更新开关是否启用。

        Returns:
            bool | None: True 启用，False 禁用，网络异常返回 None。
        """
        return GitManager._cloud_update_enabled(CLOUD_FORCE_UPDATE_CONTROL_URL, '强制更新')

    def use_local_version(self):
        """校验本地启动基础条件；前端构建和依赖同步仍由后续启动流程负责。"""
        from deploy.uv import venv_python

        root = Path(self.root_filepath)
        required = (
            'gui.py', 'alas.py', 'pyproject.toml', 'uv.lock', 'module/api/app.py',
            'module/runtime/process_manager.py', 'module/config/argument/args.json',
            'frontend/package.json',
        )
        missing = [name for name in required if not (root / name).is_file()]
        if missing:
            reason = f'本地安装不完整，缺少关键文件: {", ".join(missing)}'
            logger.critical(reason)
            raise ExecutionError(reason)

        python = venv_python(root)
        if not python.is_file():
            reason = f'本地 Python 环境缺失: {python}，请先修复安装环境'
            logger.critical(reason)
            raise ExecutionError(reason)
        try:
            result = subprocess.run(
                [str(python), '-c', 'import starlette, uvicorn, pydantic, yaml, numpy, cv2, requests'],
                cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20,
            )
            if result.returncode:
                raise ExecutionError(f'本地 Python 依赖不可用: {result.stderr.strip()}')
        except (OSError, subprocess.TimeoutExpired, ExecutionError) as exc:
            logger.critical(f'本地启动环境校验失败: {exc}')
            raise ExecutionError(f'本地启动环境校验失败: {exc}') from exc
        logger.info('本地启动基础校验通过，使用当前本地版本继续启动；后续依赖和前端检查仍须通过')
        return False

    def local_changes_present(self):
        """更新前保护已跟踪文件的修改；未跟踪文件由 Git 的合并冲突检查保护。"""
        if not (Path(self.root_filepath) / '.git').exists():
            return False
        try:
            result = subprocess.run(
                [self.git, 'status', '--porcelain', '--untracked-files=no'], cwd=self.root_filepath,
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExecutionError(f'无法检查本地 Git 修改: {exc}') from exc
        if result.returncode:
            raise ExecutionError(f'无法检查本地 Git 修改: {result.stderr.strip()}')
        return bool(result.stdout.strip())

    def cloud_update_access_failed(self, fatal=True):
        """处理云端更新控制接口访问失败的情形。

        Args:
            fatal (bool): 兼容旧调用；True 时校验本地安装，False 时仅跳过运行期检查。

        Raises:
            ExecutionError: 本地安装不满足启动基础条件时抛出。
        """
        logger.hr('Cloud Update Control Failed', 0)
        if fatal:
            logger.warning('云端更新状态未知，跳过 Git/CDN 更新，尝试使用本地版本')
            return self.use_local_version()
        else:
            logger.warning('Failed to access cloud update control, skip update check')

    def git_install(self, manual=False):
        """自动更新遵循云端三态；手动更新绕过云端控制，均保留本地工作。

        Returns:
            bool: True 已完成更新，False 使用本地版本。
        """
        logger.hr('Update AzurPilot', 0)

        if not manual:
            cloud_update = self.cloud_auto_update_enabled()
            if cloud_update is None:
                return self.cloud_update_access_failed()
            if cloud_update is False:
                logger.info('云端明确关闭自动更新，使用本地版本')
                return self.use_local_version()
        else:
            logger.info('用户手动更新，跳过云端自动更新控制')

        if self.local_changes_present():
            logger.warning('本地存在未提交修改，保留全部修改并跳过更新；请自行提交或备份后重试')
            return self.use_local_version()

        if self.GitOverCdn:
            if self.goc_client.update():
                logger.info('GitOverCDN 更新完成，使用更新后的版本')
                return True

        updated = self.git_repository_init(
            repo=self.Repository,
            source='origin',
            branch=self.Branch,
            proxy=self.GitProxy,
            ssl_verify=self.SSLVerify,
        )
        if updated is False:
            return False
        logger.info('Git 更新完成，使用当前版本（仅允许快进）')
        return True


if __name__ == '__main__':
    self = GitManager()
    self.goc_client.get_status()
