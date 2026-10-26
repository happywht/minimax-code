# Linux 受限运行环境实测

2026-10-26 在用户指定的 Ubuntu 24.04 执行主机上完成了 rootless Podman 安装与合成任务验证。该记录只证明运行环境就绪，不代表 MiniMax Code 的远程任务、长驻服务或 M5 整体已验收。

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
运行产品 `pid1_guard.py` 中的固定源码，**18 项通过，exit 0**。这验证 helper，
尚未验证公开 Native/子任务产品接线，公共 L1 profile 仍不可用。

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

M5 仍须将受限执行适配器接入真实 RunService，校验明确的挂载、网络和资源配置，容器不可用时暂停无人值守任务。还需完成版本化安装、systemd 用户服务、受认证 SSH 隧道、Windows 断开后任务继续、重连恢复及文件交付。当前单次容器实测不能替代这些验收。

参考：[Podman 安装](https://podman.io/docs/installation)、[Ubuntu 24.04 Podman run 手册](https://manpages.ubuntu.com/manpages/noble/man1/podman-run.1.html)。实现与部署应使用实际安装版本支持的参数。
