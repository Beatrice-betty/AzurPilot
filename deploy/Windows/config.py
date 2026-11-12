import os
import subprocess
import sys
from typing import Optional, Union

from deploy.config_transaction import DeployConfigTransaction
from deploy.Windows.logger import logger
from deploy.Windows.utils import DEPLOY_CONFIG, DEPLOY_TEMPLATE, cached_property, poor_yaml_read


GIT_OVER_CDN_REPOSITORY = 'git://git.pull/AzurPilot'
GITHUB_REPOSITORY = 'https://github.com/Beatrice-betty/AzurPilot'
GITCODE_REPOSITORY = 'https://gitcode.com/Beatrice-betty/AzurPilot'
GIT_OVER_CDN_FALLBACK_REPOSITORIES = [GITHUB_REPOSITORY, GITCODE_REPOSITORY]
GIT_OVER_CDN_FALLBACK_REPOSITORY = GITCODE_REPOSITORY


class ExecutionError(Exception):
    """部署流程执行失败；code 取 deploy.config.ErrorCodes，供上游结构化识别。"""

    def __init__(self, code: int = 1, message: str = ''):
        super().__init__(message or f'deploy failed with error code {code}')
        self.code = code


class ConfigModel:
    """Windows 部署配置模型，定义所有配置项及其默认值。"""
    # Git 配置
    Repository: str = GITCODE_REPOSITORY
    Branch: str = "dev"
    GitExecutable: str = "./.venv/Scripts/git/cmd/git.exe"
    GitProxy: Optional[str] = None
    SSLVerify: bool = True

    # Python 配置
    PythonExecutable: str = "./.venv/Scripts/python.exe"
    PypiMirror: Optional[str] = None
    InstallDependencies: bool = True

    # ADB 配置
    AdbExecutable: str = "./.venv/Scripts/adb.exe"
    ReplaceAdb: bool = True
    AutoConnect: bool = True
    InstallUiautomator2: bool = True

    # OCR 配置
    UseOcrServer: bool = False
    StartOcrServer: bool = False
    OcrServerPort: int = 22268
    OcrClientAddress: str = "127.0.0.1:22268"

    # 更新配置
    EnableReload: bool = True
    CheckUpdateInterval: int = 5
    AutoRestartTime: str = "03:50"

    # 杂项
    DiscordRichPresence: bool = False

    # 远程访问
    EnableRemoteAccess: bool = False
    RemoteAccessMode: str = "auto"
    CloudflareRegistry: str = ""
    CloudflareDeviceId: str = ""
    SSHUser: Optional[str] = None
    SSHServer: Optional[str] = None
    SSHExecutable: Optional[str] = None
    AllowedRedirectHosts: Optional[str] = None
    MaxRedirects: int = 2
    SignalingServer: Optional[str] = None
    StunServers: Optional[str] = '["stun:stun.l.google.com:19302"]'
    TurnServers: Optional[str] = None
    TurnCredentialMode: str = "static"

    # WebUI 配置
    WebuiHost: str = "0.0.0.0"
    WebuiPort: int = 25548
    Language: str = "en-US"
    Theme: str = "default"
    DpiScaling: bool = True
    Password: Optional[str] = None
    CDN: Union[str, bool] = False
    # --watermark. 关闭未经验证版本的水印。默认 False，即默认显示水印；
    # 需与 deploy/config.py 保持一致，否则 Windows 启动器读不到该开关。
    DisableBranchWatermark: bool = False
    Run: Optional[str] = None
    AppAsarUpdate: bool = True
    NoSandbox: bool = True

    # 动态配置
    GitOverCdn: bool = False


class DeployConfig(DeployConfigTransaction, ConfigModel):
    """Windows 环境部署配置管理器，维护配置加载、事务同步与工具路径解析。"""

    def __init__(self, file=DEPLOY_CONFIG):
        """初始化部署配置。

        Args:
            file (str): 用户部署配置文件路径。
        """
        self.file = file
        self.template_file = DEPLOY_TEMPLATE
        self.config = {}
        self.config_template = {}
        self.read()

        self.show_config()

    def show_config(self):
        """展示非默认的部署配置项。"""
        logger.hr("Show deploy config", 1)
        for k, v in self.config.items():
            if k in ("Password", "SSHUser"):
                continue
            if self.config_template.get(k) == v:
                continue
            logger.info(f"{k}: {v}")

        logger.info(f"Rest of the configs are the same as default")

    def config_redirect(self):
        """部署配置重定向，处理旧配置到新配置的迁移。

        每次 `read()` 之后必须调用。
        """
        self.config.pop('AutoUpdate', None)
        repository = str(self.Repository).strip().rstrip('/').removesuffix('.git')
        if repository in ('cn', 'cnb', 'gitcode', GIT_OVER_CDN_REPOSITORY,
                          'https://github.com/wess09/AzurPilot', 'https://cnb.cool/AzurPilot/AzurPilot',
                          'https://gitcode.com/ddl2/AzurPilot', 'https://git.nanoda.work/git/AzurPilot',
                          'https://git.nanoda.work/git/AzurLaneAutoScript', 'https://git.nanoda.work'):
            object.__setattr__(self, 'Repository', GITCODE_REPOSITORY)
            self.config['Repository'] = GITCODE_REPOSITORY
        elif repository == 'global':
            object.__setattr__(self, 'Repository', GITHUB_REPOSITORY)
        object.__setattr__(self, 'GitOverCdn', False)
        if (self.CloudflareRegistry or '').rstrip('/') in (
                'https://remote.azurpilot.com', 'https://ap-remote.108-186-180-52.sslip.io:8443'):
            object.__setattr__(self, 'CloudflareRegistry', '')
            self.config['CloudflareRegistry'] = ''
        if self.SSHServer == 'remote.nanoda.work:1022':
            object.__setattr__(self, 'SSHServer', None)
            self.config['SSHServer'] = None

    def filepath(self, path):
        """获取绝对文件路径。

        Args:
            path (str): 相对或绝对路径。

        Returns:
            str: 绝对文件路径。
        """
        if os.path.isabs(path):
            return path

        return (
            os.path.abspath(os.path.join(self.root_filepath, path))
            .replace(r"\\", "/")
            .replace("\\", "/")
        )

    @cached_property
    def root_filepath(self):
        """获取项目根目录绝对路径。

        Returns:
            str: 格式化为正斜杠的绝对路径。
        """
        return (
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
            .replace(r"\\", "/")
            .replace("\\", "/")
        )

    @cached_property
    def adb(self) -> str:
        """获取 ADB 可执行文件路径。

        Returns:
            str: ADB 绝对路径或回退命令 'adb'。
        """
        exe = self.filepath(self.AdbExecutable)
        if os.path.exists(exe):
            return exe

        logger.warning(f'AdbExecutable: {exe} does not exist, use `adb` instead')
        return 'adb'

    @cached_property
    def git(self) -> str:
        """获取 Git 可执行文件路径。

        Returns:
            str: Git 绝对路径或回退命令 'git'。
        """
        exe = self.filepath(self.GitExecutable)
        if os.path.exists(exe):
            return exe

        logger.warning(f'GitExecutable: {exe} does not exist, use `git` instead')
        return 'git'

    @cached_property
    def python(self) -> str:
        """获取 Python 可执行文件路径。

        Returns:
            str: Python 绝对路径或当前解释器路径。
        """
        exe = self.filepath(self.PythonExecutable)
        if os.path.exists(exe):
            return exe

        current = sys.executable.replace("\\", "/")
        logger.warning(f'PythonExecutable: {exe} does not exist, use current python instead: {current}')
        return current

    def execute(self, command, allow_failure=False, output=True):
        """执行系统命令。

        Args:
            command (str): 要执行的命令。
            allow_failure (bool): 是否允许失败。
            output (bool): 是否显示输出。

        Returns:
            bool: 是否成功。失败且不允许失败时终止安装流程。
        """
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        if not output:
            command = command + ' >nul 2>nul'
        logger.info(command)
        error_code = os.system(command)
        if error_code:
            if allow_failure:
                logger.info(f"[ allowed failure ], error_code: {error_code}")
                return False
            else:
                logger.info(f"[ failure ], error_code: {error_code}")
                self.show_error(command)
                raise ExecutionError(
                    10, f'Command failed (exit {error_code}): {command}'
                )
        else:
            logger.info(f"[ success ]")
            return True

    def subprocess_execute(self, cmd, timeout=10):
        """在子进程中执行命令。

        Args:
            cmd (list[str]): 命令列表。
            timeout: 超时秒数，默认 10。

        Returns:
            str: 命令的标准输出。
        """
        logger.info(' '.join(cmd))
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            process.kill()
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            logger.info(f'TimeoutExpired, stdout={stdout}, stderr={stderr}')
        return stdout.decode()

    def show_error(self, command=None):
        """展示更新失败信息及排查指引。

        Args:
            command (str, optional): 触发失败的命令。
        """
        logger.hr("Update failed", 0)
        self.show_config()
        logger.info("")
        logger.info(f"Last command: {command}")
        logger.info(
            "Please check your deploy settings in config/deploy.yaml "
            "and re-open AzurPilot.exe"
        )
        logger.info("Take the screenshot of entire window if you need help")
