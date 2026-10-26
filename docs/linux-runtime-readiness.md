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

另有提交 `c7c0029` 的启动器实机检查：Linux 隔离临时目录 10 项通过、4 项明确跳过，
其中四项为真实合成进程验证。没有运行真实后端健康/SPA/认证，也没有安装产品服务。
源码归档、校验值、日志及具体跳过范围见 [部署说明](deployment.md)。

M5 仍须将受限执行适配器接入真实 RunService，校验明确的挂载、网络和资源配置，容器不可用时暂停无人值守任务。还需完成版本化安装、systemd 用户服务、受认证 SSH 隧道、Windows 断开后任务继续、重连恢复及文件交付。当前单次容器实测不能替代这些验收。

参考：[Podman 安装](https://podman.io/docs/installation)、[Ubuntu 24.04 Podman run 手册](https://manpages.ubuntu.com/manpages/noble/man1/podman-run.1.html)。实现与部署应使用实际安装版本支持的参数。
