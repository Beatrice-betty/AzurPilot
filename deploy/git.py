import os
import random
import re
import signal
import subprocess
import time
from typing import Optional
from uuid import uuid4

import requests

from deploy.config import GIT_OVER_CDN_FALLBACK_REPOSITORIES, DeployConfig, ExecutionError
from deploy.git_over_cdn.client import GitOverCdnClient
from deploy.git_over_cdn.endpoints import CLOUDFLARE_UPDATE_URLS, FALLBACK_UPDATE_URLS
from deploy.logger import logger
from deploy.utils import cached_property


CLOUD_FORCE_UPDATE_CONTROL_URL = 'https://alas-apiv2.nanoda.work/api/force_update'
GIT_UPDATE_SOURCES = (
    ('GitCode', 'https://gitcode.com/ddl2/AzurPilot.git'),
    ('GitHub', 'https://github.com/wess09/AzurPilot.git'),
)
GIT_NETWORK_RETRIES = 2
GIT_CONNECT_TIMEOUT = 15
GIT_FETCH_TIMEOUT = 180


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
            res = self._run_git('remote', 'get-url', source, timeout=5, allow_failure=True)
            return res.stdout.strip() if res.returncode == 0 else ''
        except ExecutionError:
            return ''

    def _run_git(self, *args, timeout=30, allow_failure=False, options=()):
        """执行有总时限的 Git 命令；超时连同传输子进程一起终止。"""
        command = [self.git]
        for option in options:
            command.extend(('-c', option))
        command.extend(args)
        logger.info(subprocess.list2cmdline(command))
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'}
        process = None
        try:
            process = subprocess.Popen(
                command, cwd=self.root_filepath, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                start_new_session=os.name != 'nt',
            )
            output, error = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if os.name == 'nt':
                try:
                    terminated = subprocess.run(
                        ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                        capture_output=True, timeout=10,
                    )
                    if terminated.returncode:
                        process.kill()
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                output, error = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                # 不关闭仍被 communicate 读取线程持有的管道，避免关闭操作再次阻塞。
                output = error = ''
            logger.warning((output + error).strip())
            raise ExecutionError(f'Git 操作超时（{timeout} 秒）：{args[0]}') from exc
        except OSError as exc:
            raise ExecutionError(f'无法执行 Git：{exc}') from exc
        if output.strip():
            logger.info(output.strip())
        if error.strip():
            logger.info(error.strip())
        if process.returncode and not allow_failure:
            raise ExecutionError(f'Git {args[0]} 失败（{process.returncode}）：{(output + error).strip()}')
        return subprocess.CompletedProcess(command, process.returncode, output)

    @staticmethod
    def _update_sources(repo):
        """官方部署按固定顺序容灾，自定义仓库只使用原地址。"""
        normalized = repo.strip().rstrip('/').removesuffix('.git').lower()
        official = [url for _, url in GIT_UPDATE_SOURCES] + GIT_OVER_CDN_FALLBACK_REPOSITORIES
        if normalized in [url.rstrip('/').removesuffix('.git').lower() for url in official]:
            return GIT_UPDATE_SOURCES
        return (('Repository', repo),)

    def _validate_update(self, commit, branch):
        """只接受目标本地分支可快进的历史，不将镜像 SHA 当作等价历史。"""
        self._run_git('cat-file', '-e', f'{commit}^{{commit}}')
        local = self._run_git('rev-parse', '--verify', f'refs/heads/{branch}', allow_failure=True)
        if local.returncode == 0:
            if self._run_git('merge-base', '--is-ancestor', local.stdout.strip(), commit,
                             allow_failure=True).returncode:
                raise ExecutionError(f'更新源的 {branch} 落后或历史分叉，保留本地提交')

    def _fetch_with_retry(self, source, branch, max_retry=GIT_NETWORK_RETRIES, delay=1,
                          repo=None, options=()):
        """逐源有限重试；探测和 fetch 均限时，成功后不再连接其他源。"""
        repo = repo or self.Repository
        if not options:
            options = (f'http.sslVerify={str(self.SSLVerify).lower()}',)
            if self.GitProxy:
                options += (f'http.proxy={self.GitProxy}', f'https.proxy={self.GitProxy}')
        self._run_git('check-ref-format', f'refs/heads/{branch}')
        self._run_git('check-ref-format', f'refs/remotes/{source}/{branch}')
        retries = max(1, min(max_retry, GIT_NETWORK_RETRIES))
        failures = []
        for name, url in self._update_sources(repo):
            logger.info(f'Trying {name}: {url} ({branch})')
            ref = f'refs/azurpilot/update/{uuid4().hex}'
            try:
                for attempt in range(retries):
                    network_options = (*options, f'http.userAgent={self.git_user_agent()}',
                                       'http.lowSpeedLimit=1', 'http.lowSpeedTime=15',
                                       'credential.interactive=false')
                    try:
                        remote = self._run_git(
                            'ls-remote', '--exit-code', '--refs', '--', url, f'refs/heads/{branch}',
                            timeout=GIT_CONNECT_TIMEOUT, options=network_options,
                        )
                        if not re.fullmatch(rf'[0-9a-f]{{40,64}}\s+refs/heads/{re.escape(branch)}\s*',
                                            remote.stdout):
                            raise ExecutionError(f'{name} 未返回有效的 {branch} 分支引用')
                        self._run_git(
                            'fetch', '--progress', '--no-tags', '--no-recurse-submodules',
                            '--no-write-fetch-head', '--refmap=', '--', url,
                            f'+refs/heads/{branch}:{ref}', timeout=GIT_FETCH_TIMEOUT,
                            options=network_options,
                        )
                        break
                    except ExecutionError as exc:
                        logger.warning(f'{name} update failed ({attempt + 1}/{retries}): {exc}')
                        if attempt + 1 == retries:
                            raise
                        time.sleep(delay)
                commit = self._run_git('rev-parse', '--verify', f'{ref}^{{commit}}').stdout.strip()
                self._validate_update(commit, branch)
                self._run_git('update-ref', f'refs/remotes/{source}/{branch}', commit)
                logger.info(f'{name} fetch successful: {commit}')
                return commit
            except ExecutionError as exc:
                failures.append(f'{name}: {exc}')
                logger.warning(f'{name} update failed: {exc}')
            finally:
                self._run_git('update-ref', '-d', ref, allow_failure=True)
        message = '所有 Git 更新源均失败，停止网络重试；本地文件和提交已保留。' + '\n' + '\n'.join(failures)
        logger.error(message)
        raise ExecutionError(message)

    def _apply_git_update(self, commit, branch):
        """保留本地修改，安全切换目标分支并快进，不重置或自动暂存。"""
        self._validate_update(commit, branch)
        status = self._run_git('status', '--porcelain', '--untracked-files=no').stdout
        if status.strip():
            logger.warning('本地存在未提交修改，保留全部内容并跳过代码应用，继续启动')
            return True
        current = self._run_git('symbolic-ref', '--quiet', '--short', 'HEAD', allow_failure=True)
        if current.returncode:
            raise ExecutionError('当前为 detached HEAD，保留本地提交；请先切换到配置的目标分支')
        local = self._run_git('show-ref', '--verify', '--quiet', f'refs/heads/{branch}', allow_failure=True)
        if local.returncode:
            self._run_git('checkout', '--no-overwrite-ignore', '-b', branch, commit)
        else:
            if current.stdout.strip() != branch:
                self._run_git('checkout', '--no-overwrite-ignore', branch)
            self._run_git('merge', '--ff-only', '--no-edit', '--no-autostash',
                          '--no-overwrite-ignore', commit)
        self._run_git('--no-pager', 'log', '--no-merges', '-1')
        return True

    def _apply_cdn_update(self, commit):
        """CDN 仅提供对象，应用前复用直连 Git 的本地保护。"""
        self._validate_update(commit, self.Branch)
        self._run_git('update-ref', f'refs/remotes/origin/{self.Branch}', commit)
        return self._apply_git_update(commit, self.Branch)

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
        logger.hr('Git Init', 1)
        self._run_git('init')
        if not self.get_remote_url(source):
            self._run_git('remote', 'add', source, repo)
        # 无显式 GitProxy 时保留用户的 Git / 系统代理，选源不写回 origin 或部署配置。
        options = (f'http.sslVerify={str(ssl_verify).lower()}',)
        if proxy:
            options += (f'http.proxy={proxy}', f'https.proxy={proxy}')
        commit = self._fetch_with_retry(source, branch, repo=repo, options=options)
        self._apply_git_update(commit, branch)

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
    def cloud_force_update_enabled():
        """检查云端强制更新开关是否启用。

        Returns:
            bool | None: True 启用，False 禁用，网络异常返回 None。
        """
        logger.info(f'Check cloud force update control: {CLOUD_FORCE_UPDATE_CONTROL_URL}')
        try:
            resp = requests.get(
                CLOUD_FORCE_UPDATE_CONTROL_URL,
                timeout=5,
                headers={'User-Agent': 'alas AzurPilot'},
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f'Failed to check cloud force update control: {e}')
            return None

        text = resp.text.strip()
        try:
            data = resp.json()
        except ValueError:
            data = text

        if data is True or (isinstance(data, str) and data.lower() in ('true', 'ture')):
            logger.info('Cloud force update control is enabled')
            return True
        if data is False or (isinstance(data, str) and data.lower() in ('false', 'fales')):
            logger.info('Cloud force update control is disabled')
            return False

        logger.info(f'Cloud update control is inaccessible: {text}')
        return None

    def git_install(self):
        """根据本地配置执行 Git 源码拉取与更新。"""
        logger.hr('Update AzurPilot', 0)

        if self.GitOverCdn:
            try:
                if self.goc_client.update(apply=self._apply_cdn_update):
                    return
            except ExecutionError as exc:
                logger.warning(f'CDN 更新无法安全应用：{exc}')
            logger.warning('CDN update unavailable; falling back to direct Git update')

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
