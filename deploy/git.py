import random
import re
import subprocess
import time

from deploy.config import DeployConfig, ErrorCodes, ExecutionError
from deploy.git_over_cdn.client import GitOverCdnClient
from deploy.git_over_cdn.endpoints import CLOUDFLARE_UPDATE_URLS, FALLBACK_UPDATE_URLS
from deploy.logger import logger
from deploy.utils import *


def fetch_verified_branch(git, source, branch, repositories, *, user_agent, max_retry, delay, log):
    """先确认 GitHub 分支，再向本地发布已取到的同一提交。"""
    ref = f'refs/heads/{branch}'
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')

    def run(*args, timeout):
        return subprocess.run([git, '-c', f'http.userAgent={user_agent()}', *args],
                              env=env, capture_output=True, timeout=timeout, check=True)

    def remote_head(repository):
        result = run('ls-remote', '--refs', '--exit-code', repository, ref, timeout=15)
        revision, _, name = result.stdout.strip().partition(b'\t')
        if name == ref.encode() and re.fullmatch(rb'[0-9a-f]{40}', revision):
            return revision.decode('ascii')

    try:
        expected = remote_head(GITHUB_REPOSITORY)
    except (OSError, subprocess.SubprocessError):
        expected = None
    if expected is None:
        log.warning(f'无法确认 GitHub 分支 {branch} 的版本，保留本地版本')
        return False

    for repository in repositories:
        if repository != GITHUB_REPOSITORY:
            try:
                mirror_head = remote_head(repository)
            except (OSError, subprocess.SubprocessError):
                mirror_head = None
            if mirror_head != expected:
                log.warning(f'镜像无法提供与 GitHub 一致的分支版本，跳过 {repository}')
                continue
        for attempt in range(max_retry):
            try:
                run('fetch', '--no-tags', repository, expected, timeout=120)
            except (OSError, subprocess.SubprocessError):
                if attempt + 1 < max_retry:
                    time.sleep(delay * random.uniform(0.5, 1.8))
                continue
            try:
                run('update-ref', f'refs/remotes/{source}/{branch}', expected, timeout=10)
            except (OSError, subprocess.SubprocessError):
                log.warning(f'无法更新本地分支引用 {source}/{branch}')
                return False
            log.info(f'已从 {repository} 取得 {branch} 的 GitHub 提交 {expected}')
            return True
        log.warning(f'无法从 {repository} 下载提交 {expected}，尝试下一更新源')
    return False


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
        """官方镜像按优先级拉取 GitHub 已确认的提交，自定义仓库走原始 Git 流程。

        gitcode 等仓库会返回 418 拦截特定 UA，此时自动更换 UA 重试；

        Args:
            source: 远程源名称。
            branch: 分支名称。
            max_retry: 最大尝试次数（含首次）。
            delay: 重试间隔秒数。

        Raises:
            ExecutionError: 所有尝试均失败时抛出。
        """
        repositories = repository_candidates(self.get_remote_url(source))
        if len(repositories) > 1:
            if fetch_verified_branch(self.git, source, branch, repositories, user_agent=self.git_user_agent,
                                     max_retry=max_retry, delay=delay, log=logger):
                return True
            raise ExecutionError(ErrorCodes.GIT_FETCH_FAILED, f'无法获取 GitHub 已确认的分支 {branch}')

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

        raise ExecutionError(
            ErrorCodes.GIT_FETCH_FAILED,
            f'git fetch {source} {branch}: 主源与全部镜像重试穷尽',
        )

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
        if not self.execute(f'{git} init', allow_failure=True):
            self.remove('./.git/config')
            self.remove('./.git/index')
            self.remove('./.git/HEAD')
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
        verified = self._fetch_with_retry(source, branch)

        logger.hr('Pull Repository Branch', 1)
        # 移除 git 锁文件
        for lock_file in [
            './.git/index.lock',
            './.git/HEAD.lock',
            './.git/refs/heads/master.lock',
        ]:
            if os.path.exists(lock_file):
                logger.info(f'Lock file {lock_file} exists, removing')
                os.remove(lock_file)
        self.execute(f'{git} reset --hard {source}/{branch}')
        if not verified:
            git = f'"{self.git}" -c http.userAgent={self.git_user_agent()}'
            self.execute(f'{git} pull --ff-only {source} {branch}')

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

    def git_install(self):
        """根据本地配置执行 Git 源码拉取与更新。"""
        logger.hr('Update AzurPilot', 0)

        if self.GitOverCdn:
            if self.goc_client.update():
                return

        self.git_repository_init(
            repo=self.Repository,
            source='origin',
            branch=self.Branch,
            proxy=self.GitProxy,
            ssl_verify=self.SSLVerify,
        )


if __name__ == '__main__':
    self = GitManager()
    self.goc_client.get_status()
