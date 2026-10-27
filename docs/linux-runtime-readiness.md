# Linux 受限运行环境实测

2026-10-26 起在用户指定的 Ubuntu 24.04 执行主机上记录 rootless Podman 环境、产品适配器和分批验证。2026-10-27 的三个公开 Native 候选用例及后续四个故障用例已按文末范围验收；主分支 L1 允许集仍为空，远程任务、长驻服务及 M5 整体未验收。

## 安装与主机状态

安装前确认 Python 3.12.3、cgroup v2、用户 subuid/subgid 映射及 systemd 用户管理器可用。用户已配置 linger；用户管理器原有 degraded 状态未修复或改动。

使用主机原有 Ubuntu 软件源安装 Podman 4.9.3+ds1-1ubuntu0.2，以及 uidmap、fuse-overlayfs、slirp4netns、netavark 和 catatonit。APT 先模拟并确认无升级、无删除；实际新增 13 个包，没有进行系统升级或重启。

软件包自动启用了新的系统级 Podman 服务、socket 和定时器。本次仅停用这些新安装的 Podman 单元；最终 API service、socket 与自动更新 timer 均为 disabled/inactive。安装日志记录了此前新建的 enable 链接，但停用前 active 状态没有采集，保持未知。没有启用用户级 Podman socket，也没有修改宿主机 AppArmor 或 user namespace 限制。

实际运行使用 uid 1000、gid 1001，rootless、systemd/cgroup v2、netavark 和默认 seccomp。Podman 报告容器 AppArmor 不可用；该层防护不计入本次验证。

## 实际验证结果

镜像来自完整的 `docker.io/library/python:3.12-alpine` 引用；拉取后记录 digest，并按本地 image ID 执行。只挂载独立临时合成工作目录，不挂载真实项目、凭据或容器 socket。

| 项目 | 实测 |
|---|---|
| 内存与 swap | `memory.max=134217728`，`memory.swap.max=0` |
| CPU | `cpu.max=50000 100000`，即半核配额 |
| 进程 | `pids.max=64`；创建 62 个额外 sleep 子进程后返回 EAGAIN |
| 权限 | 非 root，`CapEff=0`、`NoNewPrivs=1`、`Seccomp=2` |
| 文件系统 | 根文件系统写入返回 EROFS；明确挂载的工作目录可生成并校验文件 |
| 临时文件 | 单独的 `/tmp` tmpfs 为 32MiB |
| 网络 | `network=none`；仅有 lo，网络 namespace 与宿主不同，外部连接返回 ENETUNREACH |
| 主机边界 | 外部合成 canary 不可见且内容保持原样；容器内没有主机 runtime socket |
| 清理 | 停止并删除测试容器后，真实孙进程文件心跳停止；临时工作区删除 |

资源项通过实际 cgroup 读取和受限进程创建验证；没有额外进行 OOM 或 CPU 饱和压力测试。允许联网的执行配置尚未实测。

本地原始证据位于 `.verification/linux-container-runtime-2026-10-26.json`，对应实际执行的验证源为 `.verification/verify_linux_podman.py`。这些是当前工作区的验证附件，不随普通源码克隆提供。没有进行付费模型调用。

## 后续接入

后续已提交 `a775619` 在独立 `/tmp/minimax-product-l1-*` 完整依赖环境追加六模块
后端生命周期回归，**106 项通过、无跳过**（19.28 秒），包含实际 stdio CLI 和
HTTP/ASGI lifespan；归档 SHA、固定 uv/lock、隔离配置及原始日志已核对。
环境保留用于 L1，所跟踪安装/测试阶段进程已退出。细节见
[服务生命周期](service-lifetime.md)。这比早期单独 lease 模块验证覆盖更广，但仍未运行
产品 Podman 适配器、systemd 长驻或完整 M7 安装恢复。

另有提交 `c7c0029` 的启动器实机检查：Linux 隔离临时目录 10 项通过、4 项明确跳过，
其中四项为真实合成进程验证。没有运行真实后端健康/SPA/认证，也没有安装产品服务。
源码归档、校验值、日志及具体跳过范围见 [部署说明](deployment.md)。

完整后端准备后，`a775619` 内容的启动脚本和既有预构建前端又通过 **12 项检查、
2 项 Windows 专用跳过**（8.69 秒）；实际 supervisor/CLI 均完成健康、SPA HTML 与
RPC 认证验证。上传脚本为 CRLF 副本，和提交归一换行后相同，413 文件的上传原字节
manifest 及日志 SHA 均匹配。暖到健康样本 2010.7 / 1959.0 毫秒，不作为冷启动验收；
未执行浏览器渲染、容器或长驻部署，证据同见部署说明。

对同一 `a775619` 基线另跑执行策略、终端与工作区六模块：首轮 101 通过、2 项 Windows
专用跳过、1 失败，原因是 CWD 测试硬编码 `python`，Ubuntu 没有该系统别名。夹具改用
`sys.executable`，保留安全 PATH 和工作目录断言；Windows 对应模块 16 项通过，Linux
六模块 **102 项通过、2 项平台跳过**（4.80 秒）。远端先核对 1028 个原始源码文件，
仅覆盖该测试文件运行，结束恢复原基线；覆盖文件 SHA 与日志 SHA 保存在
`.verification/linux-execution-baseline-fixed.json`，首轮失败日志同样保留。
这些验证现有 Native 进程/工作区行为，不是新 Podman 适配器验收。

## 固定 PID1 helper 的实际故障验证

在同一 Linux 6.8.0-101-generic / rootless Podman 4.9.3 环境，固定本地镜像
`sha256:f4b7b8044a873fcc0f348549a19b9075cd77bd56ead7177192cfec38d85bce4a`
运行产品 `pid1_guard.py` 中的固定源码，**18 项通过，exit 0**。该批只验证 helper，
不覆盖公开 Native/子任务接线；后续候选用例见文末，主分支公共 L1 profile 仍不可用。

覆盖正常/非零及 payload124/125、缺执行文件、信号退出、二进制独立双流、
已过期及迟到启动零 canary、boot 不符、FD 关闭、leader 退出清理孙进程、
沿用原绝对期限、堵塞 stderr 并持续 SIGINT、CPU 饱和、杀 attach 客户端、
pids 耗尽和真实 memcg OOM。18 只容器均核对完整 CID/nonce 后清理并确认 absent，
本轮临时目录已删除；未拉取镜像、改 systemd 或调用模型。

OOM 使用存活的固定 payload 父进程，在宿主保存 exact cgroup 的基线与同 inode
后测计数，再放行父进程结束：container 的 `memory.events.local.oom_kill` 增加 1，
scope 的 local `oom` 增加 1、`max` 增加 18；实读内存上限 128 MiB、swap 0、
`memory.oom.group=0`，分配子进程 wait 结果为 SIGKILL。由此证明子进程遭 memcg OOM，
不能据此声称 guard 本身曾被 OOM 杀死，也不追认先前单凭 137 的未知原因。
计数口径见 [Linux cgroup v2 文档](https://docs.kernel.org/admin-guide/cgroup-v2.html)。

冻结输入与原始证据：

- 实际 guard UTF-8 源码 SHA-256：`1fc6aeeaab3ed10c37fbee7606f5cc5859212baff908a37c8740063abceccb12`。
- helper 模块 SHA-256：`ce1c0d038086648ac2dfd3a93d3ec3bf11845b6185a8188e96e0b9a921bd1637`。
- 完整 seccomp 策略 SHA-256：`41353e75c367b32482d0367ba5632ea6f27fba0b390b68418401549ab8cd545f`。
- 实际验证脚本 `verify_pid1_guard_remote.py` SHA-256：`48e030718f5a1cd1c332e177e0b9c8cde24dcc385add62a8d504922ccbc19366`。
- `.verification/pid1-guard-linux-faults.{json,stdout,stderr,exit}`，stdout SHA-256：`216ca7c5c6297ef7159b9ec9e6db0f89b666d4301476d2e68ec3b7aa60010beb`。
- Windows 纯单元验证 38 项通过；Ruff 通过，日志 `.verification/pid1-guard-windows.log`。

原始失败保留：`pid1-guard-linux-initial.*` 发现固定 musl 镜像没有 libc close_range
符号，改为显式受支持 x86_64 syscall 并在 fork 前探测；`pid1-guard-linux-backpressure.*`
暴露验证脚本在释放故意堵塞的 attach 前等待清理的问题；`pid1-guard-linux-created-fixture-failure.*`
记录尚未启动就被夹具当成退出，现要求实际终结证据；`pid1-guard-linux-tail-oom-unattributed.*`
保留 137 但 OOMKilled=false 的失败，后续才增加上述内核事件取证。失败次数不与最终
通过数相加。完整公开入口、服务崩溃窗口、重启核对及剩余故障矩阵仍须单独验收。

## 截止治理候选的攻击探测

独立临时容器在固定本地 image ID、私有 PID namespace、无网络、只读根、cap-drop ALL、
no-new-privileges 和默认 seccomp 下运行真正 PID 1（Python `-I -S`，UID 1000，
dumpable=0）。这项研究尚未运行产品适配器；它发现普通 Python PID1 包装不足以保证截止：

| 探测 | 实际结果 |
|---|---|
| 同 namespace 子进程发 SIGSTOP/SIGKILL 到 PID 1 | 调用返回成功，但 PID 1 继续运行 |
| 修改 PID 1 的 nice/CPU affinity | 成功，nice 读回 19、affinity 读回单核 |
| 直接 sched_setattr syscall 设置 SCHED_IDLE | 成功；相同镜像的 Python sched API 返回 ENOSYS，不能据此认定攻击已被阻止 |
| ptrace、process_vm_writev、`/proc/1/mem` 与 fd 访问 | EPERM/EACCES，guard buffer 未改变；未将所有拒绝均归因于 dumpable |
| CLOCK_BOOTTIME 绝对 POSIX timer 发 SIGKILL 到 PID 1 | 创建/设定成功，到期后 PID 1 仍运行 |
| 容器与宿主 clock/boot identity | 本机 boot_id 一致，容器 BOOTTIME 样本位于宿主前后采样之间 |

Linux 6.8.0-101-generic、Podman 4.9.3 的上述事实不能自动推广到其他安装。
证据为 `.verification/linux-pid1-attack-probe.{json,stdout,stderr}`，实际研究源码为
`probe_pid1_attacks_remote.py`，SHA-256：
`a1d447aeeb3f914848eb0cb3c248b72e5a90cb6597e7e7091333040f58d385bb`。
两只随机名称容器均按完整 CID 和 nonce 标签核对后清理，exists 返回 1，临时目录删除。
未修改 systemd、拉取新镜像或调用模型；不据此计入 L1 产品通过项。
候选需补充可信治理执行流及调度防护，具体约束见
[profile 契约](linux-execution-profile-contract.md)；验收前 profile 保持不可用。

上述环境与攻击探测本身不覆盖产品 RunService；后续私有及公开候选接线见下文。
M5 仍需完成无人值守全部准入、版本化安装、systemd 用户服务、受认证 SSH 隧道、
Windows 断开后任务继续、重连恢复及文件交付，单次容器或三个候选用例不能替代这些验收。

参考：[Podman 安装](https://podman.io/docs/installation)、[Ubuntu 24.04 Podman run 手册](https://manpages.ubuntu.com/manpages/noble/man1/podman-run.1.html)。实现与部署应使用实际安装版本支持的参数。

## 产品适配器的固定私有 schema preflight

2026-10-27，提交 `428652a` 对应的冻结 schema 与宿主会话准入修复，在同一
Linux 6.8.0-101-generic / amd64 / systemd-cgroup v2 / rootless Podman 4.9.3 /
crun 1.14.1 环境通过了实际固定私有 preflight，零模型调用。归档 1065 个后端输入
文件逐项核对，源码归档 SHA-256 为
`9d6088861c6a3de0305d9b0e891086f0047355607e4b0fe74add765b7100077b`。

运行 `run_5a986686b8544d269a12ada4835ba417` 为 completed；持久 process 为
removed、resource_held=0，关闭后 active 为空。实际 UID 1000/GID 1001、自身内核
uid_map/gid_map、inspect 中间映射与 info 外层映射复合一致，固定工作区写入读回、
PID/父 PID、boot identity、time namespace 和 BOOTTIME 两侧采样均通过。
runtime 与映射 fingerprint 和原输出随 durable receipt 保留。

原始证据 `.verification/linux-l1-private-preflight-schema-pass.json` 的 SHA-256 为
`9506e7469fd515073f3c183da8d5e2a42221c35c8194b2e4688440f115eafa6a`。
本地较宽进程回归为 **404 通过、1 项 POSIX 目录模式检查在 Windows 跳过**；后续
失败取证补强后的受影响 preflight/runtime 回归为 **104 通过**，Ruff 通过。两批有
重叠，分别记账，不相加；Linux 私有 probe 也不折算为这些单元测试数量。

同一 1065 文件归档随后在真实 Linux 环境再次逐项核对，离线十一模块回归为
**430 通过、无跳过**（40.83 秒），exit 0、stderr 为空。覆盖 runtime/preflight/
supervisor、安装、profiles、宿主会话 gate、terminal/runner handlers、terminal
hardening、binding 和 runtime shutdown；包含受控 runtime、真实 SQLite 与进程，
不等于公共 L1 故障验收，也不与 Windows 两批相加。原证据
`.verification/linux-schema-regression.{json,stdout,stderr,exit}`，JSON SHA-256 为
`e56719d5699b45b3bf079def9f573bfeb2768d129071e9d0d9fdd6793e9a523f`。

首次归档在到达 schema 检查前被固定 policy SHA 拒绝：Windows Git 的
`core.autocrlf=true` 将归档中该 JSON 的 LF 转成 CRLF，工作区及 HEAD blob 的原始
policy hash 均正确。保留失败归档和
`.verification/linux-l1-private-preflight-archive-eol-failure.*`；归档器显式使用
`git -c core.autocrlf=false archive` 后才得到上述通过结果。随后添加的精确
`.gitattributes` 路径规则对该 policy 设置 `-text`，防止 fresh checkout 或 archive
再次改写字节；专用真实 Git 回归在首次 commit 前启用 autocrlf=true，验证提交 blob、
新 checkout 与二进制 tar 内容。policy 原字节及 SHA
`41353e75c367b32482d0367ba5632ea6f27fba0b390b68418401549ab8cd545f` 不变。

以上只接受产品适配器的固定私有 schema/时钟/映射核验；后续 copy/mount 专项和公开
Native 候选用例分别见下节及文末。主分支公共 guard/policy 允许集仍为空，
`linux-podman-l1` 仍 unavailable。公开子任务链、剩余服务故障矩阵、L2/L3、正式
40 项三轮及安装恢复仍须各自验收。

## 受限副本的 mountinfo 与真实 bind 验证

2026-10-27，在同一 Linux 主机对 `cfaf895` 基线加冻结 mount 修复的 **1070 文件**
快照逐项核验后，完成以下三组独立验证。源码归档 SHA-256 为
`375f0511144250ac0260a7d86f72c7ffebeef3898ed1ce610175f6dfe234fc54`，
manifest SHA-256 为
`0fdba0ab72add694605e18c82f004e2715d2237da425a3d974c87855c9409e50`。
该快照不包含之后的 restricted child/legacy overlay 和 chat 会话准入改动，不将这些
后续改动计入本轮实机通过范围。

| 验证 | 结果与范围 |
|---|---|
| Linux 八模块回归 | **184 通过、无跳过**，7.90 秒，exit 0、stderr 为空；覆盖真实 `/proc/self/mountinfo` 解析、受限副本、既有 child/workspace、preflight、profiles 与 policy 打包 |
| 固定私有产品 preflight | `run_9141a245e35a4298adebdb2342c377e5` completed，process removed、resource_held=0、容器 absent，关闭后 active 为空；工作区读回、映射和时钟证明保留，零模型调用 |
| 真实合成 bind 专项 | **7 项通过**，exit 0；同设备 included 目录和文件拒绝、omitted 路径排除且零 canary 打开尝试、相似前缀兄弟路径允许、合法 project 根挂载允许、合成保护目录别名和合成 HOME 别名拒绝 |

bind 验证使用 `podman unshare` 后新建私有 mount namespace，先证明与宿主 namespace
不同，再执行 `mount --make-rprivate /` 并核对无 shared/master/propagate_from，才创建
挂载。所有 source/target 都在 nonce 自有合成目录内；未绑定真实 HOME、凭据或服务
数据，没有 sudo、镜像拉取或 systemd 变更。七个目标逐个逆序精确 umount；退出后
`host_cleanup_verified=true`、宿主无 nonce 挂载，源 canary 的内容和物理身份保持原样。
合成目录保留为证据。omitted 项使用真实 restricted `_scan`、未初始化的实际 catalog
和仅提供合成保护根的 fixture manager；HOME 项显式传入合成 HOME。它们验证纯副本
validator，不经过 RunService/公开进程准入，也不把 user namespace 内 UID 0 当作
产品 rootless 准入已通过。

本批 mount 规则对语法、唯一 ID 和所有可见 parent 链保持严格校验；读取失败、空表、
截断、超限、未知 escape、非法 parent 或环均拒绝。合法同点叠挂保留为歧义点，不猜
topmost；工作区、HOME 或保护路径自身及任一祖先涉及歧义就拒绝，唯一更深挂载也不能
绕过。与这些路径无关的系统叠挂不再全局阻断；included 严格后代的文件/目录挂载仍
拒绝，明确 omitted 的子树不读取内容。敏感别名按 device、filesystem 和 mount root
推导 backing 坐标，不能仅凭相同 st_dev 或 mount source 文本放行。详细规则见
[实施规格](linux-process-adapter-design.md#受限副本的-mountinfo-准入)。

首次私有 preflight 因 `/proc/sys/fs/binfmt_misc` 的合法 autofs/binfmt 同点叠挂被全局
拒绝，止于工作区校验阶段，关闭后无 active 责任；原失败及 diagnostic 日志保留。修复为
上述相关路径歧义拒绝后才取得本节通过结果。bind driver 首次也因输入文件可被组写而
拒绝；仅将自有归档、manifest 和 stage owner 文件收紧为 0600，字节 hash 不变后重跑，
未放宽 driver 的输入检查。失败不计入通过数。

原始证据（均为当前工作区附件，不随普通克隆提供）：

- `.verification/linux-mount-regression.{json,stdout,stderr,exit}`；JSON SHA-256：`e9a713d8c9b6916a6755ba7cee26636b709fb03d959f840fd2d7f71fee906f38`。
- `.verification/linux-l1-private-preflight-mount-pass.{json,stdout,stderr,exit}`；JSON SHA-256：`fd7cda6df2ebf3cb3a22158b3ae7d51d06f6f3085c7c3ce2e8381332c8113405`。
- `.verification/linux-mount-bind.{json,stdout,stderr,exit}`；JSON SHA-256：`d1821c02ff10102327e32a0440bb2cb71a324cb7c9ae1170e901f1a82a9bb433`；实际 driver `verify_mounts_remote.py` SHA-256：`f70691106a4fdfacb5c00c023664d29fb27b58be02d10bf236c8b905d6bdda58`。
- 原失败：`.verification/linux-l1-private-preflight-mount-{initial,diagnostic}-failure.*`、`.verification/linux-mount-bind-input-mode-failure.*`；修复冻结清单：`.verification/restricted-copy-mounts-source-checkpoint-20261027-scoped-ambiguity.json`。

184 项、7 个 bind case 和私有 probe 分别记账，不与早期 430 项等有重叠的批次相加。
这些证据接受上述冻结版本的 mount/copy 专项及私有 preflight；主分支公开允许集仍空，
`linux-podman-l1` 仍 unavailable。分阶段重新读取 mount 表保留原 device/nlink/symlink
限制，但不能消除全部宿主文件或挂载 TOCTOU；本节单独不证明完整 Native OS 沙盒、
公开 Native/子任务整链、服务故障矩阵或正式 40 项三轮就绪。

## 公开 Native 候选的三个实际用例

2026-10-27，冻结提交 `70870b4` 的 **1072 个源码文件**在同一受支持 Linux 平台逐项
核对后，以真实 `python -m minimax_code --stdio` 启动完整服务，经过租约、迁移、
安装核对和私有 preflight，再通过公开 JSON-RPC 接单。该候选只在独立 staging 的
`process_preflight.py` 将空允许集替换为上述唯一 guard/policy 精确配对，保存补丁前后
SHA 和完整源码 manifest；没有修改主分支允许集，也没有注入 readiness、admission、
runtime、模型客户端或 transport。候选不包含后续 `196d84b` 的 M6 文本成果功能。

模型链使用实际 OpenAI SDK、`mock=false` 和只绑定 `127.0.0.1` 随机端口的固定 SSE
回放，凭据仅为专用 dummy keyring 的假密钥。共 **7 次 SDK 请求、0 次付费调用**，
不评价真实模型质量。三个用例 exit 0，并经过独立证据复核：

| 用例 | 实际通过范围 |
|---|---|
| 正常完成与幂等 | 公开 `agent.invoke`，固定 read/write/exec 工具实际写、读和容器执行，再返回最终答案；4 次 SDK 请求，v3 非 mock profile、预算事件和持久回执一致；payload 使用自有副本，合成源文件不变，重提交不增加 SDK 或进程 |
| 运行中取消 | 精确 `wait.py` argv hash、实际副本 nonce 和完整 CID Running 共同证明 payload 已启动；公开 `run.cancel` 后持久 cancelled/partial 回执成立，容器 absent、resource_held=0，无额外 SDK 请求 |
| 服务死亡、重启和审查恢复 | 仅杀死本轮服务进程，重开相同数据/源目录后旧容器核对清除，旧 run 为 interrupted，无自动公开 payload/SDK 重放；仅新增一个固定零模型私有 preflight。公开 `run.resume(reviewed=true)` 保留原 spec/session/target/预算家族，新增 1 次 SDK 请求，未重新执行旧 wait.py/process.py |

审查恢复会按产品既有逻辑新建一次固定 Git advisory probe：
`git --no-pager -c core.fsmonitor=false rev-parse --git-dir`。仅接受新 resumed run 下
这一条精确 argv/hash，且家族/root、工作区 inode、target/install/location 均匹配，
原 process 行不变。该镜像返回 **125、outcome unknown，advisory unavailable**，
不记作 Git 校验通过；新增行的配置/删除回执、实际 CID absent 和资源释放均核对。

最后正常关闭服务 exit 0，无 shutdown RPC 错误、无强杀；最终 **9 个 process 行均
removed、resource_held=0，9 个 CID 均 absent**。它们包括初次及重启私有 probe、
advisory 和三个用例进程，不能当作九项独立验收或与既有测试数相加。

冻结与证据（当前工作区附件，不随普通源码克隆提供）：

- 源码归档 SHA-256：`0ecf77ac46ba3c199b43795c9fc0eef664e9720345bb19b0a770006905f47d0c`；manifest：`7972518aad4d8450c5fedf68168674702148d42f1880ced31a1a49819ad52ebe`。
- 实际 harness `verify_l1_public_native_remote.py`：56,681 字节，SHA-256 `82cf68caef835897d94d0527b3291a648de78cb4480435846193ef69e0c92366`。
- `.verification/linux-l1-public-native-result.json`：SHA-256 `96e3ac67a363c6af35821e5e841875a17927b7c16bd1126c4dcb8e691b434b63`；外层 `.verification/linux-l1-public-native.json`：`f96ca781941d595efdf9a53a744ed6b05fe054f30177122a6385adc0206840f6`。
- `.verification/linux-l1-public-native-success-raw/`：13 份原始 RPC、SSE、服务 stdout/stderr 和数据库快照，共 2,184,602 字节；逐份 hash 与结果中的清单一致。

失败证据保留在 `linux-l1-public-native-startup-wait-failure*` 和
`linux-l1-public-native-resume-count-failure*`：前者误把短时 baseline 进程当等待 payload，
后者漏计审查恢复合法 advisory；修正的是 harness 断言，未据此声称修复产品缺陷。
另有 upload 文件模式拒绝及结果 fetch 的 SFTP nlink 元数据不支持，分别保留
`linux-l1-public-native-upload-mode-failure*`、`linux-l1-public-native-result-fetch-failure.json`；
收紧自有上传权限、改用远端只读核验后取回证据，没有放宽产品边界。

本节仅接受这三个固定候选用例。主分支 `ACCEPTED_GOVERNORS` 仍为空，公共 L1 未发布；
公开 restricted 子任务的实际工具类/委派归属仍待审计。完整故障矩阵、L2 文件边界、
L3 长驻/隧道、正式 40 项三轮、安装/升级/完整备份恢复及 14 天真实使用均未完成。

## 公开 Native 候选的四个实际故障用例

2026-10-27，同一 `70870b4` 候选及上述唯一 guard/policy 配对，在独立 stage 中经过
完整 stdio CLI、公开 RPC 和实际 SDK 的 loopback SSE 链路，**四个故障用例通过并经
独立复核接受**。源码仍为上述 1072 文件归档，不包含后续 M6 或当前文档改动；运行
前后完整 source manifest 一致，没有替换 runtime、生命周期或准入逻辑。

两项 deadline 用例使用持久化的原始 **60 秒 CLOCK_BOOTTIME 绝对期限**；payload 的
独立 180 秒上限只作清理保护，不能代替期限通过条件。预先固定停止容差 5 秒、观测
容差 10 秒，服务离线期间保留原 PID/startticks、完整 CID、心跳和时钟原始采样。

| 用例 | 实际通过范围 |
|---|---|
| 服务死亡后的心跳后代 | 确认 payload/后代运行后仅 SIGKILL 自有服务；重启前容器已停止且心跳稳定停止，首次观察原 PID 均停止于原 deadline 后 0.201 秒；重启前后 SDK 请求数 1→1，无旧 payload 重放 |
| 双流 flood、服务及 attach 丢失 | 服务 SIGKILL 后，已记录精确身份的 attach CLI 自行消失，未再向 attach 发 SIGKILL；其消失后容器仍 Running、心跳继续。首次观察原 PID 均停止于原 deadline 后 0.211 秒，重启前后 SDK 请求数 2→2。非阻塞 stdout/stderr 各写入 9,486,336 字节，实际 runtime log 为 82,450 字节，低于预定单文件及总量限制 |
| leader 退出后的后代清理 | leader 启动有界心跳孙进程后正常退出；PID1 teardown 停止后代，容器删除、资源释放，持久 payload exit 为 0 |
| 重复公开取消 | 后代已运行后调用三次 `run.cancel`，包含终态后的重试；终态 cancelled，未新增执行，后代停止、容器删除、资源释放 |

0.201/0.211 秒是轮询得到的**首次停止观测上界**，不是精确死亡时刻；完整心跳、
BOOTTIME 和 inspect 记录保留。两项 deadline 的 raw controller exit 为 124，持久
outcome 保持 unknown，不当作 payload 成功。全批共 **5 次受控 SDK 请求、0 次付费
调用**；前两个服务为预定 SIGKILL（-9），最后服务正常关闭 exit 0、无关闭错误，
无外部 rescue cleanup，已核对清理责任和资源释放。本批与此前三个用例分别记账。

原始证据与取回记录（当前工作区附件，不随普通克隆提供）：

- 实际 v2 harness `verify_l1_fault_matrix_remote_v2.py` SHA-256：`00591c6cae9fb66b234b891479dfeac28341527b83585920ac60948462b07161`；冻结清单 `.verification/l1-fault-matrix-v2-frozen-manifest.json`：`7e56acabc4a86e340c919520f3d8e77d15eee8f60f74968d2257272a40dfd6b8`。
- `.verification/linux-l1-fault-matrix-fetch-20261027T050057Z-d1b5e90f/result.json` SHA-256：`7ff0f23447528cacd8862ddf6bc357beec673d779f1f82873c8446e8e11e8d65`；对应 `receipt.json`：`75c3257fea2bc7f592769aac3a5ab3708f71918be6f6c6b2c14d9e3b3178fc22`。35 份原始附件共 11,832,710 字节逐项核对；只读取回复用 9 份已核验副本、取回其余 26 份，零重连，前后重新核验 1072 个源码文件及原始执行身份。
- v1 `.verification/linux-l1-fault-matrix-20261027T042746Z-571a0a7e.json` SHA-256：`c6631ba00b8d16b8f26cdabf9724d98bf09901513363f5d6ee67cd467f103900`，保留 runtime log 的 PermissionError 和 rescue 事实，仍是失败。v2 将证据读取改为严格校验 UID/GID 映射、精确已停止 CID/安装/store 和文件身份的固定 `podman unshare` 只读 reader，没有放宽期限或日志限制。
- v2 外层 `.verification/linux-l1-fault-matrix-20261027T044414Z-7b6b2773.json` SHA-256：`18ce434c9462f1b21c98d4f41ed2eb82ab006ff826bec894a16e689e31d576fa`。远端 harness 已 exit 0（186.508 秒），SSH 仅在随后下载附件时断开；该外层记录仍保留 exit 1，由上述独立只读 fetch receipt 绑定同一次执行，没有重跑或追改失败记录。

本批 flood 使用非阻塞写，未证明阻塞写或 D-state IO 下的截止；attach 自行消失也不
记作本轮额外注入 SIGKILL 已执行。完成后的附件下载断线不构成运行中 SSH 断开、
任务续跑或 L3 重连证明。剩余矩阵包括迟到/过期启动、create/start 登记窗口服务死亡、
产品整链 OOM/pids/CPU、网络/secret/socket 边界、runtime 消失、foreign marker/
安装身份及租约丢失、持续 fork 与真正阻塞输出故障。公开子任务、无人值守全部准入、
公共允许集发布、正式 40 项三轮及 L2/L3 仍各自待验收；不据四个 case 宣称 M1 或
公共 L1 整体完成。

## 三个启动窗口候选用例的失败收口

2026-10-27，另一次真实 Linux 执行尝试覆盖迟到启动、create 返回后服务死亡、start
返回后服务死亡三个窗口，**本批未通过，三个用例均未验收**。输入以 `70870b4` 的
1072 文件候选为基底，另在独立 staging 的 `podman_execution.py` 加入明确冻结的
三个 observer 调用点和有界暂停；这是带观测补丁的候选，不能视为此前接受的相同
源码字节，也没有改动主分支产品或发布允许集。

实际执行 44.973 秒、远端 exit1，只进入 `late-start`。`after-create` 记录真实 create
已返回、持久行仍为 intent；随后 `before-start` 记录 starting/revision2、资源占用1，
并进入暂停。harness 的 `exact_created` 随即因 CID 文件未满足其预期 `0600` 模式
而拒绝；证据未保存该文件的实际模式，不能据此推断为 `0644` 或认定产品权限缺陷。
尚未等待并释放原期限后的暂停点，没有 release/after-start 记录；注入信号日志为空，
另两个死亡窗口未执行。没有形成迟到启动、原期限执行或重启恢复的通过证据。

失败清理先尝试正常关闭，未正常完成；服务最终被清理强杀（exit -9），不计作预定
故障注入。救援 inspect 确认精确归属 CID
`e6468e8e4ff55cd8a731ca4b1e40e4bbe6763f6afc172ceaa6ccc66991470c35`
仍为 created、未启动；精确 `rm --force` exit0，随后 exists1 证实容器不存在。
`rescue_cleanup_used=true`，正常关闭错误保留，外层 `cleanup_failure=null` 只表示
救援未再报错。前后数据库快照相同，目标行仍 starting/resource_held=1、所属 run
仍 running；不能把容器删除写成持久责任已核销或资源占用归零。

原始证据保留在当前工作区，不随普通克隆提供：

- 外层 `.verification/linux-l1-faultwindows-20261027T053547Z-1ca3fb09.json` SHA-256：`e9a44ac1dfc36b38c38015e432987731bd4b192a7c8d31b1bacac5e2b2b13ba5`；同名目录 `result.json`：`44a9ea35d2d5a655e555805b7631a08482f7070a7a506afc1800561fe62c3268`。22 份下载附件共 605,611 字节逐项核验，无传输错误；取回前后核验全部 1072 个候选源码文件。
- 本批归档 `.verification/l1-faultwindows-source.tar`：`edabb1f2549a7af56c28dd59b157a3d0af3de5f7f1a701bfade3f3f0ede5fd24`；source manifest：`5558a29fa749cae3066d3c4c2628f7e670159579b006e36f25181e1c94cfc43f`。
- checkpoint patch：`5dd10b334e7167586c2b7d75c632e16387cf3ab7e7b33dbfb70372438222e5c9`；checkpoint proof：`951dfdd1a69119fa68bfdaaa1a5e68343c7e83ead769436625a4beab76c47617`；harness：`68f0939945b72d2851d107c45b6c83558833d37ee5846d182940db4f11fb3bb6`；完整冻结清单 `.verification/l1-faultwindows-frozen-manifest.json`：`e84faa916d1caec6dff8c12c953d1137e877cb5b83db961b7e3ed560261a46de`。

本次按收口要求保留失败，不修复或重跑。此前三个正常/取消/恢复候选用例及四个
故障用例的独立接受范围不变。迟到/过期启动与 create/start 死亡窗口仍在剩余矩阵中；
主分支允许集仍为空，公开子任务、无人值守、公共 L1、M1 整体及 L2/L3 均未因此验收。
