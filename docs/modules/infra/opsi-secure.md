# 大世界统计 V2 存储

本文依据当前实现及隔离测试编写，取代旧文档中“源码哈希变化即清空”“缺少 DPAPI 就写明文”“可以重封恢复”的说明。日志中的统计数值与既有 CL1 遥测提交按用户最新要求保留；这些允许的运行时输出不属于磁盘统计库的保密承诺。

## 入口与范围

- `module/statistics/opsi_secure.py`：格式、读取、提交、迁移和清空。
- `opsi_keys.py`：统一 `KeyProvider`、本机凭据与平台选择；`opsi_device_keys.py`：原生 CNG/TPM2 和 Secure Enclave 对象。
- `opsi_state.py`：保护范围、进程锁与原子文件发布。
- `opsi_broker.py`、`dev_tools/opsi_host_broker.py`：宿主服务及专用 mTLS 客户端。
- 启动入口仍为 `alas.py`、`gui.py`、`process_manager.enable_opsi_secure()`；存储写者也检查就绪状态，未就绪时不写普通格式。

受保护数据包括 CL1 大世界字段、`opsi_items`、资源快照的大世界三种货币、舰船经验文件、farming CSV、旧月度 JSON 与对应 `.bak`、日报战斗事件与正文，以及 `AzurPilot_Data_Backup/` 下三种统计数据库和统计描述文件的备份。其他业务字段保持现有存储方式。SQLite 表结构、实例名、记录身份、时间、文件大小及记录数量等路由元数据仍可见；不能把行级载荷保护描述为整个 SQLite 文件不可识别。

WebUI 仍使用已有统计报告接口和原来的分类、筛选、图表与表格。含大世界数据的 resources/action/opsi/ships/loot 分类不再提供 CSV 或图表文件导出，表格明细也没有导出入口。委托、科研、仓库分类保留导出。没有新增解密命令、明文导出、恢复口令、恢复密钥、设备适配或 raw dump API；宿主服务仅供受授权的运行时调用，没有 Root Key 返回方法。

## 密文与身份

Root Key 为系统随机生成的 256 位密钥；统计载荷、迁移 journal 与提交 journal 均通过现有 PyCryptodome 的 `ChaCha20_Poly1305.new(key=..., nonce=os.urandom(24))` 使用 XChaCha20-Poly1305。nonce 为独立随机的 192 位，认证标签为 128 位；HKDF-SHA256 按 dataset 派生 256 位子密钥。统计密文前缀为 `OPSIV2.XCHACHA20-POLY1305.`，安全服务状态与磁盘描述文件也明确记录 algorithm。未实际部署的 AES-GCM V2 不作为兼容或迁移目标，只保留真实历史 V1/更旧格式的合法读取。JSON 文件只保存 V2 包装与载荷，CSV 路径保存同样的受保护字符串。

AAD 包含 schema=2、algorithm=XCHACHA20-POLY1305、随机 installation_id、dataset、instance、record logical identity 和 logical period。CL1 使用实例与月份；掉落、资源和日报事件使用行 id、实例和原始时间，掉落还绑定截图、设备及类型身份；日报正文使用完整 period_key 与实际月份。跨月集合文件以相对路径作为稳定身份、逻辑周期为 all-history，其内部所有月份整体认证；整文件回放不再被检测（读出为旧内容）。修改身份或交换记录不能通过原认证标签。

V2 `config/opsi_secure/keyring.json` 只有版本、algorithm、installation_id 与 provider 名称，既没有 Root Key，也没有可供用户恢复的 wrapped key。Root Key 或其硬件包裹对象、generation、authenticated root、提交中间状态均保存在本机安全服务或宿主安全服务中。Root 仅在运行进程内存中解封；TPM/Secure Enclave 只保护 Root，不直接处理统计数据库。新硬件对象先解封回读并比较 Root，再进入 preparing 阶段。

## V1 → V2 安全迁移

升级前停止全部旧版本的 WebUI、调度器和统计写者；不能让不认识 V2 协调锁的旧进程与新版混写。开发测试只使用临时目录，真实部署作为只读来源采样，未用新版访问真实部署的统计写入路径。

1. 取得跨进程锁及宿主租约，读取本机状态和旧 keyring。V1 先由原 DPAPI CurrentUser 解封 Root Key，并验证旧 keyring MAC；不再检查旧 manifest 中的源码哈希。
2. 在安全服务中建立独立 preparing 状态，保留旧 Root、keyring 与全部原数据。SQLite 取得写锁后 serialize 一致快照，在内存数据库转换；旧 JSON/CSV 在内存转换。
3. 合法读取旧载荷，逐项直接产生 XChaCha20-Poly1305 V2，并立即回读比较完整内容。转换失败、旧密文无法验证、SQLite busy、磁盘故障均不清空 V1。
4. 将完整 before/after 映像和原 keyring 放入受保护的迁移 journal，fsync；安全服务记录 migration 阶段和 journal SHA-256 后，才按文件原子发布已经验证的映像。
5. 全部磁盘数据与预期根一致后，将安全服务状态提交为 ready/generation=1，才启用 V2 的篡改清空规则，之后清理迁移 journal。

单文件发布使用临时文件、fsync 与 os.replace；多个文件通过安全服务中的阶段和 journal 协调，不声称文件系统能一次 rename 多个数据库。发布中失败恢复 before 映像与原 keyring；异常退出后的首次访问先恢复 V1，再重试迁移。服务暂时离线时保留 journal 与数据，等待服务恢复，不自行接受部分发布状态。迁移前、迁移中、无法确认成功时均禁止 wipe；失败时不允许另建 Root 覆盖旧描述文件。V1 源码升级不会触发清空。

迁移需要容纳数据库完整映像的内存与 journal 空间。现阶段以安全与可恢复性优先，未承诺大库迁移耗时或每次写入的吞吐率。

## 平台密钥

| 环境 | 实际实现 | 不可用时 |
| --- | --- | --- |
| Windows | TBS 确认 TPM 2.0 后优先使用原生 CNG 的 Microsoft Platform Crypto Provider，创建当前账户的不可导出 RSA-2048 设备密钥，仅以 OAEP-SHA256 包裹随机 Root；对象引用、wrapped Root 与认证状态再经 DPAPI CurrentUser 写入 Credential Manager（LOCAL_MACHINE 持久化，不是 DPAPI LocalMachine） | 确认没有 TPM2 的新环境使用 DPAPI CurrentUser；TBS/CNG 暂时失败或既有 TPM 对象不可用时拒绝降级，保留原件 |
| macOS | 原生 SecItem API、Data Protection Keychain、禁止同步、AfterFirstUnlockThisDeviceOnly。适合的签名/权限环境可设置 `ALAS_STATISTICS_SECURE_ENCLAVE=1`，通过 Security.framework 创建永久、不可导出的 Secure Enclave P-256 对象，以 ECIES 包裹随机 Root；对象引用与 wrapped Root 存 Keychain | 默认 Keychain；显式启用 Enclave 或既有 Enclave 对象失败时拒绝降级，不弹交互认证框 |
| Linux TPM2 | 有 `/dev/tpmrm0` 时优先；fixedtpm/fixedparent 密封 Root，tpm2-tools/TSS 显式连接 `device:/dev/tpmrm0`，通过 stdin 接收随机 Root，只将密封对象写临时目录；密封载荷与认证状态保存在账户 Secret Service 中 | TPM/工具/DBus/集合不可用时保留数据，不退化普通文件 Root |
| Linux Secret Service | 显式选系统 SecretService 后端，拒绝自动选择普通文件后端；部署者先核实集合确实受账户凭据保护，再设置 `ALAS_STATISTICS_SECRET_SERVICE_VERIFIED=1` | 未核实、锁定或离线均停写；变量不是自动证明安全的检测器 |
| Docker | 必须连接宿主 Broker；Root 和认证状态由宿主以上 provider 保存，Root 不进入容器 | 未配置、离线、证书错误或授权失败均保留原统计，不 wipe |

Linux 的 Secret Service 规范没有保证每个实现都可靠保护落盘集合，不能仅因为 import keyring 成功就承诺安全。macOS 与 Linux/TPM 的接口测试不能替代真实系统验收。Python 运行程序通常没有满足 Enclave 的签名/权限条件，因此不自动尝试后悄悄降级；部署者只能在验证了合适运行环境后启用。Enclave 的 ECIES 是 Root 包裹层，统计载荷始终使用 XChaCha20-Poly1305。Windows 与 macOS 使用 Root 的内存缓存，但每次加载安全状态仍检查原设备对象；Linux TPM 路径也检查设备在线，不能用已有缓存越过设备服务离线。

## Docker 部署

宿主运行 `uv run --frozen python -m dev_tools.opsi_host_broker --config <宿主配置.json>`，仅提供服务启动，没有解密、恢复、dump 子命令。配置包含 provider（windows/macos/linux/linux-tpm2，其中 windows/linux 均自动优先 TPM2，linux-tpm2 强制 TPM）、bind、port、certificate、private_key、ca、grants；grants 将客户端证书 DER 的 SHA-256 指纹映射到唯一安装 slot，不允许不同证书共用同一 slot。slot 是容器内绝对安装路径的 SHA-256，仅是授权索引，不能代替设备身份。

容器配置 `ALAS_STATISTICS_BROKER=https://宿主地址:端口`，以及 `ALAS_STATISTICS_BROKER_CA/CERT/KEY` 的证书路径。客户端私钥必须从独立宿主凭据挂载提供，位于 `/run/secrets/`、仅拥有者可读（0400）；使用只读 secret 挂载，不能放入镜像、应用数据 volume 或数据库备份。服务默认绑定 localhost，Docker 需要部署者选择容器可达的受限宿主接口。证书签发与宿主账户安全属于部署责任，代码没有自动放宽 TLS 或创建可转移的通用授权。

不使用 container id、hostname 或 machine-id。仅复制镜像/容器文件系统/应用 volume 没有宿主授权凭据和 OS 状态，不能读取旧统计；复制时仍需原宿主秘密挂载才可调用该环境。具有宿主管理员权限、能复制私钥并重新授予同一环境的操作者属于下述管理员边界。

服务要求双向证书认证；宿主服务最低 TLS 1.3，校验证书指纹、安装 slot、短期独占租约及状态 CAS。客户端租约续期，崩溃租约自动过期。服务禁止记录请求正文及载荷，不返回 Root。V1 Root 可由原 Windows 宿主校验并迁移；把 Windows V1 交给另一个 macOS/Linux 宿主没有跨系统恢复途径。

## 完整性模型与写入路径

完整性由**记录级认证加密**保证：每条记录（行/文件）的 AAD 绑定 dataset、installation_id、行身份与周期/相对路径。文件被拷走、被改动一个字节、记录被替换或跨行挪动、文件被复制到其它路径，受影响的记录都无法解密——读出降级为空并计入 `dropped`，不会被静默采用。CL1 其他业务字段、资源快照其他货币、未生成正文的日报状态不参与认证。

**不再维护全量状态根，也不做写入前/读取时的全库校验**（2026-10-05 定稿，用户选择"零延时"）：写入路径只做 `BEGIN IMMEDIATE` + 行级加密 + SQLite 提交，不触碰安全服务状态，单次写入回到毫秒级。此前 V2 的"每次写入 4 次全量根校验"在 18 万行数据上带来 9~11 秒/次停顿，无法与无延时并存，已整体移除；journal/pending/generation 协议保留给 V1→V2 迁移自身的原子性使用，普通写入不再经过它们。

因此**删除、回放、整库回滚（含拿旧备份覆盖回当前库）不再被检测**：旧密文本身合法，会照常读出。运行环境复位（凭据被删、描述文件被替换成非法内容）仍会被识别并按清空处理（见下节）；清空前会先做救援备份。短猫展示在内存中兼容旧场次，只有显式 `backfill_meow_stats()` 回填才写入。写者统一先取协调锁（跨进程文件锁 + 宿主租约），再开 SQLite 写事务；延迟外键约束在提交前验证。合法保留期清理走同一事务路径。

备份是独立 archives 载荷，不能直接作为 SQLite 恢复，也不能借备份重置认证状态。系统保留备份内容以满足数据留存，但没有对外恢复或解密接口。

## 清空与普通故障

清空只保留两类入口：运行环境复位（描述文件被替换成非法内容、wiping 阶段的续跑）与显式 `wipe()` 调用。清空前先把三个数据库、全部受保护文件与凭据状态复制到 `config/opsi_secure/rescue-<时间戳>/`（`provider_state.json` 含设备绑定的 Root 令牌，换机不可用），日志记录救援目录；随后在安全服务撤销 Root 与认证状态并留下 wiping 阶段，再清理数据库保护列、事件和文件，最后删除安全服务残留。Windows/macOS 的 wiping 状态仅保留待回收设备对象的引用，不含 Root；先持久化撤销状态，再删除对应 CNG/Enclave 对象。硬件回收失败会留下引用供后续继续，不恢复 Root；内存缓存同步失效。清理文件遇到 I/O 失败会保留 wiping 标记，恢复后继续；不得重新使用旧 Root。下一次可用写入创建新的随机 Root 与 installation_id；旧舰船缓存检测到环境变更后丢弃，不能将已清空历史重新落盘。

普通故障不 wipe：SQLite busy/locked、文件占用、临时 I/O 错误、Keychain/Secret Service 暂时不可用、Broker 离线/租约或 CAS 竞争、换设备/系统账户缺少凭据、尚未完成迁移、未来不支持的格式。不可用时不写统计普通格式、不返回未认证的替换载荷。V1 验证失败保持原件与旧 keyring，不尝试清空或“认可当前数据”。

不检查代码哈希，不提供 reseal/重新认可任意数据根的维护工具。远程 dev 曾提供 `dev_tools/opsi_secure_reseal.py` 用于重封源码哈希；合并最终方案时已删除该工具及其旧测试，不保留重新认可数据状态的入口。

## 验证与边界

功能分支实现的 16 个相关 Python 测试模块合计 214 项测试、154 个子测试通过；包含只读真实 V1 副本迁移、原 keyring 不变及 Windows 原生 TPM2/CNG。基础 CI lint 与新增模块 F 类检查通过。前端此前完成类型检查、生产构建、9 项相关单元测试及 6 项浏览器展示/导出边界测试，本轮只替换格式与平台设备层，没有改动 UI。

拉取远程 dev 并解决冲突后，重新运行相关模块与保留的 `include_opsi=False` 资源读取跳过载荷解封检查；远程其他业务更新保持原样，旧 reseal 工具及其旧测试移除。

测试覆盖 V1 全载荷相等、迁移发布失败回退、迁移进程 os._exit 后恢复、重启、代码升级、设备/账户隔离、单条密文 bit flip/增删替换交换的**记录级降级（不触发清空）**、整库/文件回滚不检测（读出旧值）、所有 AAD 身份维度、普通 I/O/锁/provider 故障、Windows 原生凭据及多进程写入、平台 provider 契约与真实 mTLS 宿主传输。真实部署仅通过 SQLite 只读连接采样到临时安装，比较迁移前后及重启后完整载荷，验证原 keyring 字节不变。没有用新版对真实账号运行任务或部署新系统。

2026-10-05 复核：统计与保险库相关 17 个测试模块 219 项全绿（含"写入不解封/不扫描/不写回状态""记录级降级不触发清空"新回归）。

Windows 原生 TPM2/CNG 包裹、解封、不可导出属性、对象删除、跨对象失败、重启及异常退出已实测。macOS Keychain/Secure Enclave、Linux Secret Service/TPM2 使用接口模拟及故障测试，未在对应实体系统或 TPM 上验收；Docker 验证了宿主协议与证书授权，未在实际 Docker Engine 上验收。

这不是不可破解的 DRM。原设备管理员/root、能在 ALAS 运行账户下执行任意代码或调试进程者，或能读取已授权 WebUI 响应者，仍可能通过 Hook、内存 dump 或 API 截获运行时明文。允许的日志数值、CL1 遥测和既有日报也可能透露统计。截图属于游戏诊断材料，本模块不隐藏画面中的数字。

OS/Broker 安全存储是信任根：只回滚应用数据会被检测；把整个 OS/虚拟机连同安全存储一起回滚，或者管理员改写安全服务，超出此保证。当前没有 TPM NV 单调计数器，不能声称防御整个宿主快照回滚。未知位置的历史导出或安装目录外的备份不受扫描控制。不要删除 OS 凭据、改安装路径或把旧备份覆盖到当前数据库来“恢复”；这些操作没有正常适配路径。

XChaCha 接入另有 draft-arciszewski-xchacha-03 A.3.1 标准向量、随机 nonce/位数/密钥长度、标签与 nonce 修改测试；原有 AAD 与迁移故障测试沿用。

参考：[PyCryptodome XChaCha20-Poly1305](https://www.pycryptodome.org/src/cipher/chacha20_poly1305)、[Microsoft CNG TPM](https://learn.microsoft.com/en-us/windows/security/hardware-security/tpm/how-windows-uses-the-tpm)、[Apple Secure Enclave](https://developer.apple.com/documentation/security/protecting-keys-with-the-secure-enclave)、[Secret Service 规范](https://specifications.freedesktop.org/secret-service/latest-single/)、[Apple Keychain 可访问性](https://developer.apple.com/documentation/Security/restricting-keychain-item-accessibility)、[Linux trusted keys 设备绑定](https://www.kernel.org/doc/html/v4.20/security/keys/trusted-encrypted.html)。
