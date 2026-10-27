# Linux L1 绝对截止治理实施规格

状态：架构方向已批准进入实现，**未完成 guard、绝对截止或 L1 产品验收**。
`linux-podman-l1` 保持 unavailable，直到真实产品链验收。前置约束见
[profile 与安装契约](linux-execution-profile-contract.md)，不增加第二个调度器、预算
或运行授权来源，也不改变安装 bootstrap 的所有权规则。

## 固定策略与来源

采用完整的 Ubuntu `golang-github-containers-common 0.57.4+ds1-2ubuntu0.2` 包内
`/usr/share/containers/seccomp.json`，原字节 SHA-256：
`cc374cf23846ce1f62f4dc807a8e2b8673c783c6f56cb475467621035d281e6c`。
随产品保存原文件、来源包/版本、适用许可证与 notice，以及可复现的变换。
不能在服务启动时读取最新系统默认文件并静默重算策略。

研究变换 `.verification/astra_build_pid1_seccomp_candidate.py` 产生候选 SHA-256：
`41353e75c367b32482d0367ba5632ea6f27fba0b390b68418401549ab8cd545f`。
保留默认 ERRNO/ENOSYS、archMap 及其余规则语义；从所有条件分支移除待替换 syscall，
再显式 EPERM 拒绝调度/priority 修改及跨进程检查/操控。`prlimit64` 仅 pid=0 保留原
allow 条件，其他目标 EPERM。禁止用 default-ALLOW 黑名单替代完整默认策略。

首批仅支持已验证 Linux x86_64 栈；未知架构、runtime 或策略解析失败均拒绝。
原生 syscall 验证必需，不能把某个 libc wrapper 的 ENOSYS 当作防护成功。
固定 JSON 作为版本化产品资产，写入挂载外的私有 execution 目录；精确 hash 校验、
create/start 期间保持不可变，Podman 使用该绝对路径。payload 不获得该目录的 mount/fd。
服务账户和 runtime store 属于受信控制面，不修改系统原策略。

## 冻结 target 与持久期限

Podman target version 仍为 1，policy_version 升为 2；增加必填字段并纳入既有 target hash：

```json
{"governor":{"kind":"pid1-boottime-v1","guard_sha256":"<64 lowercase hex>","seccomp_sha256":"<64 lowercase hex>"}}
```

kind 固定算法与时钟；hash 分别来自实际传给 `-c` 的 UTF-8 guard 源码和完整策略字节。
代码拒绝占位、未知字段/kind；任意 mutable verified 标记不能单独授予执行权。
Host 保持 policy_version=1、无 governor，AgentExecutionSpec 仍为 v3，继续唯一 metadata
authority。旧 Podman policy1/缺 governor 只能查询历史或恢复清理，不能静默升级启动。
cleanup 使用只读历史身份解析，不能因新启动构造器拒绝 policy1 而跳过已登记所有权
核对和清理；该路径仅允许 stop/rm，不允许 create/start。
排队启动/可信后代重新核对 profile 与资产；变更任一资产即撤销旧排队执行，历史幂等
receipt 仍先于当前配置回放。

尚未提交的 Schema039 process_executions 增加 `deadline_boot_id TEXT` 和
`deadline_boottime_ns INTEGER`（正 signed int64）。原 intent/资源事务一次写入，原
Unix REAL deadline 仅审计/显示；start、guard、watch 不以 Unix 时钟作治理判断。
watch 需检查 BOOTTIME，不能仅依赖跨 suspend 的长 monotonic sleep。boot 变化或期限
缺失禁止启动/重放，仅核对清理；期限不从实际 start 重算。

固定镜像内经过验证的绝对 Python 路径，`-I -S -c` 加固定源码，后接单个有界 JSON
envelope（exec_id、boot_id、绝对期限、payload argv）。不插值命令到源码，不使用
shell/eval、可写 guard 文件、PYTHONPATH/LD_* 或镜像原 ENTRYPOINT，保留 env 白名单。

## 启动与 inspect

比较完整 CID、owner 标签、image ID、精确 Entrypoint/Cmd、WorkingDir、完整 env、全部
mount、user/keep-id、private PID、无 init helper、cap-drop ALL、no-new-privileges、
restart=no、固定 seccomp path 和原有资源限制，保存相关原始 inspect 证据。
`Seccomp=2` 本身不证明文件内容，需资产 SHA、实际参数、inspect 与 syscall 探测共同
支持；若 runtime 提供 OCI config 路径，再核对展开规则，不猜路径或读 payload 控制文件。

当前真实平台的 schema 支持固定为 Podman 4.9.3：保存 info 中版本、kernel、arch、
cgroup/runtime 和 rootless 映射证据，未知版本不能套用本节的省略或显示规则。
本批兼容表仅包含实际已测组合：Linux 6.8.0-101-generic、amd64、systemd/cgroup v2、
rootless Podman 4.9.3、`/usr/bin/crun`，OCI version 第一行精确为
`crun version 1.14.1`；完整 OCI version 仍保存并逐生命周期核对。未知组合拒绝，
后续平台须显式扩展版本化兼容表并实测。这是兼容性证据，不增加 installation/store
身份或认领依据，也不能替代产品 guard/policy 允许集验收。
Entrypoint 只接受精确字符串或精确单元素字符串数组，完整 Path/Args/Cmd 继续逐项
核对，不进行 shell split 或类型强转。

该版本的 `HostConfig.Init` 是官方 bool/omitempty，只允许键缺失或严格 false，
显式 null、数字、字符串和 true 均拒绝；还须核对无 init=true annotation、private PID、
完整固定 guard，并由其 fork 前 PID1 自检及私有 preflight 的父 PID=1 失败关闭。
官方定义见 [4.9.3 inspect schema](https://raw.githubusercontent.com/containers/podman/v4.9.3/libpod/define/container_inspect.go)。

`UsernsMode` 的真实 inspect 值为 private，不能凭该字符串推断 keep-id。将
`HostConfig.IDMappings` 的 container→rootless 中间映射与 info 的中间→host 映射复合，
核实目标 UID/GID、container root 不映射 host root。区间须严格解析、无重叠/溢出，
覆盖需要核验的 ID；缺失或不兼容即拒绝。固定 preflight 输出自身内核 uid_map/gid_map、
实际 UID/GID，与 inspect/info 及 owned 工作区可访问性交叉核验并固定映射 fingerprint。
同 namespace 读取 `/proc/self/*_map` 时第二列是 parent namespace ID，先与 inspect
中间映射比较，再与 info 外层映射复合；不能直接将 self-map 当成 initial-host 映射。
固定 probe 对 owned 工作区独占创建文件、写入并读回固定内容后删除，保存实际文件及
目录 UID/GID；该可写性证据不能替代映射核验。失败 probe 的有界原输出、截断状态、
退出码和失败类型同样持久保存，失败运行不授予 readiness。
读取宿主 `/proc/<PID>` 时另需核对精确活跃 PID/starttime，不能读取复用 PID 补造证据。

该版本 `LogConfig.Size` 来自 `HumanSize`，是四位有效数字 SI 显示，不能解析为精确
配置字节。允许把实际请求 cap 降为 `min(5000000,max(1,floor(profile_limit*0.95)))`，
不扩大 profile 上限。仅在已验证不超过 1 GiB 的正整数字节域和 B/kB/MB/GB 格式内，
以保守显示上界核验：非字节单位可取 `ceil(display_bytes*1.001)+1`；小于 1000 B 的
整数显示为精确字节。默认 5 MiB profile 按上述公式请求 4980736 B，显示 `4.981MB`
的上界 4985982 B
仍在限额内。未知格式/版本/范围或上界超限均拒绝。保存原显示、算法版本、请求 cap
和推导上界；CreateCommand 仅作审计，不能证明实际限额。格式实现见
[固定版本依赖源码](https://raw.githubusercontent.com/containers/podman/v4.9.3/vendor/github.com/docker/go-units/size.go)。

## 真 PID1 算法

1. fork 前校验有界输入、getpid()==1、boot_id、支持的时钟；设置并读回 dumpable=0。
   任意错误拒绝。不能加 Podman --init 或将 PID1 exec 为 payload。能力 preflight
   必须核对宿主/容器 BOOTTIME 与 boot identity 映射，不能推广单台机器的探测结果。
2. fork 前建立 CLOEXEC|NONBLOCK 的 CLOCK_BOOTTIME timerfd，以 TFD_TIMER_ABSTIME
   设原期限，再检查时钟；过期不 fork。POSIX timer 的 SIGKILL 不能替代该流程。
3. 仅 fork 一个 payload leader；child 关闭 timerfd 和全部非 stdio FD，exec 前再检查
   同一期限，直接 exec argv。父进程永不 exec，不做 payload 文件操作或阻塞输出写入。
4. parent 优先检查时钟/timer，以有限批次 waitpid(-1,WNOHANG) 回收，使用有界唤醒
   等待 leader。不能以无界 reap、stdout 排空、join、信号回调或文件操作延后截止判断。
5. 到期立即 os._exit，以 PID1 退出触发内核 namespace teardown，不追加宽限期，退出
   路径不依赖 payload IO。不声称截止纳秒瞬间 D-state 等全部物理消失。
6. leader 提前退出则内存保存 wait status，迅速退出 PID1，杀剩余后代；孤儿不延长任务。
7. guard 异常直接非零退出，不能尝试可能阻塞的错误日志。OOM/guard 死亡通过 PID1
   teardown 失败关闭。Podman --timeout 仅作为另一重相对期限。

## 私有启动能力核验

运行能力核验也必须经过同一 RunService、持久家族、已准备的独立工作区、process intent
与资源事务。它是固定 argv 的零模型调用，不是绕过注册表的临时容器，也不是可由
RPC、配置布尔值或用户参数授权的执行入口。

`process_execution_owner` v2 使用 `kind=runtime_preflight`、`parent_run_id=null`、
固定 `probe_kind=pid1-clock-v1` 与 `probe_argv_hash`。私有 factory 固定 payload 和
target；DAO 核对 family root 与 run_id、可信来源、当前 probe hash、copy binding、
target 与 installation。普通公开任务和可信子任务均不能选择该 owner。

使用数据目录之外的两只私有空临时目录分别作为 source 与 owned copy；不增加数据目录
挂载例外。只有精确 CID 确认不存在、目录物理身份未变时才清理；失败保留清理责任。
catalog 在核验期间不接受外部 L1 执行，只豁免「尚未取得本次 preflight」这个循环依赖，
其余所有权、资源、扩展盘点和重启核对限制照常生效。

固定探针核对镜像内绝对 Python 路径和受支持的 3.12 版本、payload 的 PID/父 PID、
boot identity、time namespace，并要求容器 BOOTTIME 样本处于宿主前后采样之间。
持久保存原输出、两侧时钟及清理证据。核验成功只证明当前安装与启动环境满足这些条件；
仍需发布代码中精确 guard/policy hash 对的验收允许集。该集合在产品实机验收前为空，
不能用运行探针成功替代发布验收或放行任意 argv。
发布允许集约束公共 L1 准入；私有 factory 可在允许集为空时执行上述固定 probe，
但不能扩展为任意 argv 的豁免。

## 结果与清理

v1 stdout/stderr 直通，由现有宿主层和 runtime log 分别限额，不新增私有 mount/IPC。
container ExitCode 是 guard code，不能使用 attach CLI 状态。正常 payload 退出传播，
信号映射 128+signal；保留 124 用于截止、125 用于 guard 内部失败，承认与 payload 冲突。

保存 raw container_exit_code 和证据来源；仅凭 124/125 不能推断 timeout、payload 原因
或 guard 故障。服务死亡后证据缺失/歧义保持 interrupted/unknown，不能重放。
guard 仅实际观察 leader 正常退出 0 后可返回 0；所有初始化/内部失败非零。
成功还须核实 namespace/container 清理和正常 durable receipt 条件；未知责任继续占额度。

Podman `created/configured` 即使带 `Running=false`、`Pid=0` 和 `ExitCode=0`，也不构成
已启动或已退出证据。当前受支持版本的正常终结至少要求完整身份和配置匹配、
`Status=exited`、非运行、PID 为零，以及有效非零的 StartedAt/FinishedAt，之后才读取
raw exit code。日期只证明发生过启动与终结，不用于期限计算，也不要求墙钟单调。
缺字段、dead/unknown 或矛盾状态保持结果未知。

attach 提前死亡或 start 响应丢失不能引发重发 start。继续核对同一 CID 与原期限；
合法 exited 按上述取证，running 继续治理，created 仍未证实启动。精确确认 absent
后可以释放容量，但不能将未知运行改写为成功或自动重放；已确定取消、截止或故障
同样不能被随后读到的零退出码覆盖。

以后如需精确区分，由 guard 独占外层 stdout，child 双流只进内部管道，guard 框化转发
并发 final status；需非阻塞有界缓存、timer 优先与 final 缺失处理。混入 stderr 的标记
或 workspace 文件不是可信控制通道，本版不悄悄增加该复杂度。

## 同一服务的全局扩展

保留 host 默认行为。configured/loaded/pending 的未审计可执行扩展使 L1 unavailable，
不静默禁用或剪掉 host 工具。只读启动盘点先于 MCP autoattach、provider install、
analyzer scheduling。动态启用先撤销 L1 准入；仍有 L1 清理责任则拒绝该启用。
未知扩展代码运行后污染 latch 直到进程重启；disconnect 或启动失败不清除它。
纯文本技能、明确审计且无宿主执行副作用的内置工具可豁免，未经审计不算豁免。
capture、child、dispatch 均重新检查此服务级状态。

## 必须通过的实机集合

公共准入测试采用隔离的发行候选，避免增加运行时豁免：helper 与基础批次验收后，
冻结源码，在独立 staging 副本中仅将精确 guard/policy hash 对编入允许集，记录唯一
补丁、全树 manifest、固定镜像与平台。使用独立 dataDir、installation、受控 runtime
责任空间与 owned 工作区，实际运行未 monkeypatch 准入的公开 Native 入口、私有
preflight、继承/撤销、故障/恢复与持久回执。受控 SDK 可替代付费模型响应，不能替代
真实准入或容器执行。失败候选保留证据与清理责任，不发布；主工作区允许集继续为空。
全部验收后才合入同一被测源码及精确允许集补丁，核对最终树字节；实质改动重验相关项。

- 最终策略下的同 UID 调度/priority/affinity/ioprio/prlimit、ptrace/process_vm/proc1fd
  攻击；self prlimit 查询及正常工作负载仍可用。
- 正常 0/非零、payload124/125、缺执行文件、signal、stdin EOF、二进制分离双流、
  持续大输出和 reader 断开。
- leader 退出但 daemon 孙进程仍活、重归属、有限 fork storm、pids 耗尽、OOM、CPU
  饱和及 SIGCHLD/SIGINT 干扰。
- 过期启动零 payload/canary；迟到 start；create、登记、start issuance 前后及运行中
  服务 SIGKILL，期限不重算。
- 只停止 attach client、guard 故障、runtime 消失、取消/重复取消/恢复；核实 exact CID
  不存在前保留责任。
- guard/policy SHA/path、envelope、boot、namespace/init/image 不符时零 payload，无宿主回退。
- 真实公开 Native 与子任务调用产品代码，完成持久 receipt。standalone 研究不能替代。

强化 seccomp 研究已在当前平台阻止原攻击，记录于
`.verification/linux-pid1-seccomp-probe.{json,stdout,stderr}`；probe SHA-256 为
`0de1c8dc89fdc21a79bec9f1c3dc317ee80aea81e8763c821ef2981f3f98fad0`，policy 如上。
另已完成固定产品 helper 的 18 项真实容器故障验证，范围与冻结输入见
[实际证据](linux-runtime-readiness.md#固定-pid1-helper-的实际故障验证)。这两项均不能
替代本文件要求的公开产品入口与完整恢复故障集合；公共 profile 仍未通过发行验收。
