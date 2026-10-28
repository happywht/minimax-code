# Linux L1 实施规格

本文件细化 [Linux 执行链](linux-execution-plan.md) 的 L1，是实现与验收约束，
不是公共 L1 已发布的声明。基础模块及默认 host 接线已实现；真实 `agent.invoke`
的正常完成、取消和服务死亡后审查恢复三个隔离候选用例，以及后续四个故障用例
已于 2026-10-27 验收；另一个以 `f953531` 为基底的带观测补丁候选于 2026-10-28
通过三个启动窗口用例。各批源码与接受范围分别记账；`agent.spawn_subagent` 及
剩余故障矩阵仍待验收。
主分支允许集保持为空。
复用同一个 RunService、持久 run/预算家族和服务目录租约，不增加第二个调度循环。

profile/v3、安装 bootstrap、私有目录与截止保证的细化裁定见
[接线契约](linux-execution-profile-contract.md)。产品整链已接受服务死亡后的原绝对期限
及非阻塞双流 flood 用例，另已接受下文三个迟到启动/create/start 窗口候选用例；
整链 OOM/pids/CPU、阻塞 IO 等仍待验收。加固 PID1 的实现
与 helper 验证单独记账，不能将 conmon 相对期限或 helper 成功视为 L1 门槛已满足。

## 当前实现与未完成范围

已实现的基础包括：Schema039/040、私有安装 pending/marker/active 协议、FULL-before-
intent、唯一 run snapshot 归属、完整 CID/config 核验、CAS 与 event 同事务、保留取消/
清理责任、历史 policy1 只清理不启动、policy2 boot/BOOTTIME 固定期限、固定完整 seccomp
资产及打包，以及同一 RunService 内的私有零模型 preflight。preflight 生成自己的真实
run/family 与 v2 internal owner，保存解释器/boot/time namespace/时钟区间及原始 stdout，
确认容器 absent、释放责任和持久回执后才产生运行时证明。该证明仍须匹配已接受资产；
主分支 `ACCEPTED_GOVERNORS` 为空，Linux L1 能力卡保持 unavailable。

公共 Agent 新接单保存 v3 的实际 target/hash/install，默认 null 配置继续 native-host；
v1/v2 恢复维持 legacy-host，不自动升级。Python/TS/typed/mock/设置选择器与结果展示已有
字段接线，host Native/技能/副本兼容测试已通过。配置 ID 仅作选择，RPC 不接受镜像、
资源、env 或任意 controller 命令。

terminal/runner 已按执行 session 的持久历史限制宿主执行；受限 copy 的同设备
bind/mount alias 准入已有下文定向实机证据。restricted child 与 legacy overlay 已分离：
显式 overlay 请求在副作用前拒绝，受限内部子链使用 owned directory copy，并清空、
恢复继承的 overlay context。该内部链测试不扩大公开工具允许类，公开
`SpawnSubagentTool` 仍拒绝，实际类/委派归属审计、无人值守全部门槛和 UI/IPC 组合待验收。
文末三个公开 Native 候选用例、四个故障用例及三个启动窗口用例不覆盖完整故障矩阵；未审计入口不能
获取 host fallback。基础 review、受控 controller 测试或纯 validator bind probe
单独均不构成公共 L1 验收。

本批源清单与命令/原始输出记录在 `.verification/linux-l1-source-checkpoint-20261026-home-fixed.json`
及关联日志；这份清单记录局部冻结范围，不替代后续实际 Linux 产品证据。迁移和回退须
同时遵守 [存储契约](storage-schema.md) 的 Schema039/040 说明。

## 模块与接口

- `process_types.py`：纯类型 `ProcessIO`、`OwnedProcessHandle`、不可变
  `ExecutionTargetSpec`、`ExecutionOwner`。
- `process_context.py`：required-target ContextVar，独立于可重建的 ExecutionPolicy。
  内层 scope 不得改回 host、换镜像或扩大资源；派生绑定须引用实际登记的子 run。
- `podman_execution.py`：一个 supervisor 管理准入、创建、核验、启动、清理和恢复。
- `storage/dao/process_executions.py` 与 Schema039；Schema040 保存 nullable Agent profile ID。
- `process_binding.py`、`process_installation.py`：唯一 admitted authority 和私有安装身份。
- `execution_profiles.py`、`process_preflight.py`：启动冻结 catalog、扩展污染 latch 与私有证明。
- `process_deadline.py`、`process_assets.py`、`pid1_guard.py` 及固定 Linux assets：期限与加固治理。
- `process_mounts.py`、`process_workspace.py`：有界 mountinfo 快照、敏感 backing alias
  与 source/copy/mount 物理身份准入；完整支持链仍按上文收口。

保留 `execution.py` 的 spawn_exec/spawn_shell、communicate_bounded、capture_output、
terminate_tree 调用形状。容器 handle 提供流、returncode、wait、execution_id 与
terminate_owned_tree；raw container/guard 退出码来自容器 inspect，不能用 attach CLI
退出码代替。正常 leader 码经 guard 映射；124/125 仍保留 guard/payload 歧义。
容器不借用 CLI PID 冒充身份；controller_pid 仅作控制进程诊断。

## 持久身份与事务

增加三张表，不重建旧数据：

| 表 | 最小内容 |
|---|---|
| execution_installation | 单例 install_id、UID、主机/runtime-store 指纹及创建时间 |
| process_executions | exec_id、真实 run 和 family root 外键、安装身份、唯一名称/CID、状态和 revision、固定 profile/hash、工作区路径/物理身份、argv hash、cwd、绝对 deadline、资源保留、exit/OOM/error 证据及时间 |
| process_execution_events | exec_id、递增 ordinal、前后状态、最小证据 JSON 和时间 |

run/family 外键使用 RESTRICT；状态与事件同事务 CAS 提交。先在 intent 事务核验身份、
策略与家族/安装资源总额度并保留额度，再执行 create。未知清理责任继续占额度，不能
因 Python task 被取消释放。首版家族并发可为 1，安装有总上限；超额明确拒绝，避免
父子循环等待。生命周期状态与 outcome 分离：

```text
intent → created → starting → running → stopped → removing → removed
                    ↘ cleanup_pending / needs_review
```

取消、超时、OOM、正常退出和未知结果分别保存。安装身份还须匹配私有文件 marker、
当前 UID/主机/runtime store；复制数据库或恢复到别的主机不自动取得容器所有权。
启动 reconcile 位于服务租约之内；embedding 初始化须显式断言同一租约。

更新 JSON 导入保护覆盖这些表；SQLite 一致备份包含全部状态。恢复旧备份发现标签
属于同安装却没有 DB 行的容器，诊断并阻止受限准入，不按标签全局删除。
升级前使用完整 SQLite online backup，并单独保全私有 marker 和安装身份；JSON export
不是 marker 备份。039 新增三表，040 仅追加默认 NULL 字段，默认 host 不变。回退须先
停止接单并等待已确认清理，保全升级后 DB/marker/copy/CID 证据，再恢复匹配旧程序的
完整升级前数据库；未知责任仍保留，不删表或 marker 来释放资源。无逆向迁移或旧程序
直接读取升级后 DB 的承诺；跨安装/旧备份恢复须另行核对，不自动认领或重放。

## 控制面与容器边界

私有 Podman controller 仅使用固定绝对可执行路径和结构化 argv，经已有私有受管
进程原语启动，避免公共 spawn 递归进入容器。只允许 info、image inspect、create、
container inspect/exists、start attach、stop/kill 和 rm；不提供通用 bypass、shell、
remote、exec、run、all/latest、批量 filter、replace 或 prune。

控制面环境与容器环境分别白名单。前者只含 rootless 必需 HOME/XDG/runtime/path；
后者不接收 HOME、凭据、服务数据路径或完整服务环境。使用服务自有空 OCI hooks 目录。
固定本地完整 image ID，pull=never；缺镜像、Podman 或治理服务即暂停，无宿主回退。

Podman 4.9.3 目标包括：unsetenv-all、timeout、health-cmd=none、restart=no、
image-volume=ignore、read-only-tmpfs=false、有限日志、keep-id/非 root user、
network none、cap-drop ALL、no-new-privileges、只读 rootfs、有限 tmpfs、CPU、
memory/swap/pids 和唯一工作区挂载。具体参数按官方对应版本复核，并 inspect 实际值。
不得新增 AppArmor 已生效的声明。

仅挂载已证明归属的独立目录副本；source/copy/mount 与服务 dataDir、凭据及 runtime
store/socket 根双向重叠均拒绝。HOME 自身及包含 HOME 的祖先拒绝，正常 HOME/project
可用；敏感树物理别名仍须拒绝。不能先复制秘密再声称没有直接挂载。Git worktree 外部 `.git`
不可通过额外挂载源仓库或服务数据修复。容器 cwd 从 owned root 相对映射到 /workspace，
拒绝越界或身份变化，不无声重写任意宿主绝对参数。

## 受限副本的 mountinfo 准入

Linux restricted copy 在首次 source 扫描之前、开始复制时的 source/owned root，以及
最终 source/owned 重扫分别读取新 `/proc/self/mountinfo` 快照。保持原 device、nlink、
symlink 和保护根检查；host/Windows 默认行为保持兼容。Linux 读取失败、空表、截断或
超限均拒绝，不降级为仅依赖 st_dev。快照最多 4 MiB、16384 条、每条
65536 字节，严格解析唯一 ` - ` 分隔、字段、正 ID、major:minor、绝对规范 POSIX 路径；
kernel `\040`/`\011`/`\012`/`\134` 仅单遍解码，未知或截断 escape 拒绝，不二次解码。
mountpoint 按路径组件比较，不 resolve 跟随别名，也不从 display-only source 字段或
`bind` option 猜测 bind 来源。

ID 必须全局唯一，所有可见 parent 链无环。非 `/` 记录的 parent 必须存在，且位于
同点或最近严格祖先挂载点，不能跳过更近挂载；最近祖先叠挂时允许其中任一合法 ID。
`/` 可引用 namespace 外 parent。同点叠挂全部标为歧义，不根据记录顺序推断 topmost。
对 workspace、HOME、data、凭据和 runtime 保护路径，只要自身或任一祖先是歧义点就
拒绝，即使其下还有唯一更深挂载。无关系统叠挂可以保留；省略子树内的叠挂也不应
全局阻断正常项目。畸形 parent、环和重复 ID 仍使整个表失败。

对唯一可解释路径，取最深 covering mount，得到 backing 坐标
`(major:minor, filesystem, mount.root + relative(path, mountpoint))`。在相同 device/
filesystem 下，workspace 包含或等于 HOME 的 backing 拒绝，HOME 内正常 project
可以使用；data、凭据与 runtime 保护根任一方向 backing 重叠均拒绝。合法 project
根挂载可以作为基座，但不能绕过敏感 alias 检查。根的严格后代中，所有 included 文件
或目录挂载均拒绝，包含同设备 bind；只有 `_included` 明确排除的路径及其后代可跳过，
且不读取内容。相似前缀兄弟路径不构成后代。上述分阶段快照缩小检查窗口，不能承诺
消除全部宿主文件/挂载 TOCTOU，也不提供完整 Native OS 沙盒或磁盘 quota。

## 生命周期与真实入口接线

1. 校验 Linux/rootless、镜像、工作区身份及资源，提交 intent。
2. 确定名称带安装身份和随机 exec_id；CID 文件位于挂载之外的私有 0700 目录内。
   create 响应失败也按该精确名称/CID 核对，不能假定没有创建。
3. inspect 对照完整 CID、owner 标签、image、mount/env、security 与资源；证据持久
   成功才进入 created。先提交 starting，再 start attach，不能 CAS 失败后仍启动。
4. stdin 在 create/start 一致启用，默认关闭；禁 TTY/detach keys，分离 stdout/stderr。
   输出和运行时日志都限额；固定 PID1/BOOTTIME 治理负责绝对期限，conmon timeout
   仅作相对运行上限。服务 SIGKILL 后的原期限及迟到启动/create/start 窗口已有文末
   固定候选用例；整链资源故障与阻塞 IO 仍需分别验收。宿主在启动前检查原期限，
   固定 PID1 再核验绝对 deadline，过期 payload 不得执行；控制命令也有有限超时。
5. stop 后核实 Running=false/Pid0；只有 exited 和有效非零 StartedAt/FinishedAt 才读
   raw exit，created 的 0 不算成功。未知结果不重放。提交 stopped/removing，按完整 CID
   rm，再核实不存在。exists 返回 1 才是不存在；125 是故障。
6. 取消及重复取消等待同一个保留 cleanup；清理中新后代仍须停止。
   DB/runtime/identity 错误保留责任并停止新启动，不能用 attach CLI 被杀证明容器停止。

app 初始化 supervisor、reconcile 与就绪状态；RunJob/接单规格/恢复保存 execution_target。
builtins 在工作区准备前固定 target，准备后绑定实际 owner；内部孩子使用自己的 run。
终端若无持久 owner 或 owned-copy 必须拒绝受限请求，不能 fail-open。
HTTP 和 stdio 停止接单后 drain RunService/terminal/supervisor，再关闭 client/DB，最后
释放服务租约。未确认容器清理不能投影为完整成功。

L1 标为 supervised process isolation。scheduler/webhook/team 及其他无人值守入口的
受限请求同时在准入和派发拒绝，直到 L2 验收。Hooks、MCP、任意 Python provider、
未验证分析器不属于首批受支持进程链；不得把宿主文件工具 TOCTOU 或缺少磁盘 quota
隐去，不能声称完整 Native OS 沙盒。

## 验收与实施顺序

Windows 用真实 SQLite 与可编排 controller：覆盖每个 await 的 cancel/double cancel、
两连接资源竞争、CAS、create/start/rm 已生效但响应/持久化失败、身份不符不 stop/rm、
exists125、无 host fallback、内层策略不能削弱 target、备份/导入/FK 和恢复不重放。

Linux 必须运行产品代码与真实公开 Native 入口，覆盖正常/非零/缺命令、stdin/双流/
大输出、OOM/pids/CPU、无网、canary/secret/socket 不可见、leader 退出后孙进程及持续
fork、重复取消、各登记窗口服务死亡、SIGKILL 后硬期限、runtime 消失后责任保留与
恢复仅处理匹配对象、外国容器/标签无 DB 行/安装 marker 错配/双服务租约。
最终链路为 Native → 容器修改 → 容器验证 → durable receipt → 查询；可用受控 SDK，
不用付费模型结果代替确定性故障证据。

先实现新类型/context、DAO/迁移与 supervisor，再由一个执行者顺序修改共享入口，
避免与 Agent 技能批次交叉编辑。新增未调用模块或手写 Podman 命令均不足以验收 L1。
每组保存源码 SHA、命令、原始日志、exit 与 DB/inspect 证据；L2/L3、正式 40 项三轮、
安装恢复及 14 天试用仍分别验收。

官方参数依据：[create](https://docs.podman.io/en/v4.9.3/markdown/podman-create.1.html)、
[inspect](https://docs.podman.io/en/v4.9.3/markdown/podman-container-inspect.1.html)、
[start](https://docs.podman.io/en/v4.9.3/markdown/podman-start.1.html)、
[stop](https://docs.podman.io/en/v4.9.3/markdown/podman-stop.1.html)、
[rm](https://docs.podman.io/en/v4.9.3/markdown/podman-rm.1.html)、
[exists](https://docs.podman.io/en/v4.9.3/markdown/podman-container-exists.1.html)。

## 固定私有 schema 核验状态

2026-10-27 的 `428652a` 批次已在实际受支持平台经过产品 RunService、持久家族和
process intent 执行固定私有 probe：run completed、process removed、资源释放，
关闭后无 active 责任；UID/GID、内核与 inspect/info 复合映射、工作区读回和时钟轴
均核对。冻结 1065 文件归档及原证据 SHA、404/104 两批分别记账的本地回归范围，见
[运行环境实测](linux-runtime-readiness.md#产品适配器的固定私有-schema-preflight)。

首次失败来自 Windows Git 归档的 LF→CRLF 改写，固定策略 hash 检查正确拒绝；
修复归档器并保留原失败证据后，私有 probe 才通过。精确 `.gitattributes -text`
规则与真实 Git commit/fresh checkout/archive 回归保护 policy 原始字节，不改变策略
或授权。后续 mount/copy 专项和三个公开 Native 候选用例分别见下节及文末；主分支
公共允许集仍为空，公开子任务及剩余故障矩阵未验收，该私有通过不能宣称公共 L1
或完整 Native OS 沙盒已可用。

## mount/copy 冻结版本的实机证据

2026-10-27，`cfaf895` 基线加冻结 mount 修复的 1070 文件快照，在同一真实 Linux /
rootless Podman 环境逐项核对。归档 SHA-256 为
`375f0511144250ac0260a7d86f72c7ffebeef3898ed1ce610175f6dfe234fc54`；该快照排除
后续 restricted child/legacy overlay 与 chat 会话准入改动。

八模块回归 **184 通过、无跳过**（7.90 秒，exit 0、stderr 为空），含真实 procfs 解析、
受限 copy、既有 child/workspace、preflight、profiles 与 policy 打包。产品固定私有
preflight 再次通过：run completed、process removed、resource_held=0、容器 absent，
关闭后 active 为空，保留映射、时钟与工作区读回回执，零模型调用。

另在 rootless user namespace 内建立与宿主不同且 propagation private 的 mount
namespace，对真实产品 mount validator 做 **7 个实际同设备 bind case**：included
目录/文件拒绝，omitted restricted `_scan` 零 canary 打开，相似前缀兄弟与合法 project
根挂载允许，合成保护目录和合成 HOME 根别名拒绝。所有挂载源与目标均为自有合成
canary；精确逆序 umount 后核实宿主无 nonce 挂载、源内容和物理身份不变，
`host_cleanup_verified=true`。omitted 项使用未初始化的实际 catalog 和仅提供合成保护根
的 fixture manager，HOME 项显式传入合成 HOME；没有启动产品进程准入或改变公共允许集。
这接受纯 validator/scan 的 bind 专项，不能替代公开 Native 或 RunService 整链验收。

首次 private preflight 暴露无关 `/proc/sys/fs/binfmt_misc` 合法 autofs 叠挂被全局
拒绝；保留失败并改为上述相关路径歧义拒绝后才通过。bind driver 初次因自有输入可被
组写而拒绝，收紧相应文件模式、保持 hash 不变后重跑；没有放宽输入检查。原始证据、
失败记录和校验值见 [运行环境实测](linux-runtime-readiness.md#受限副本的-mountinfo-与真实-bind-验证)，
包括 `.verification/linux-mount-regression.*`、`linux-l1-private-preflight-mount-pass.*`
和 `linux-mount-bind.*`。184 项、7 case 与私有 probe 分别记账，不与既有重叠回归相加。
公共允许集仍为空，`linux-podman-l1` 仍 unavailable；完整故障矩阵、正式 40 项三轮、
安装恢复与完整 Native 进程支持仍各自待验收。

## 三个公开 Native 候选用例的接受范围

冻结 `70870b4` 的 1072 个输入文件在真实 Linux 受支持组合逐项核对，仅在独立 staging
将 `process_preflight.py` 空允许集替换为当前唯一精确 guard/policy 配对。完整服务经
真实 stdio CLI、租约、迁移、安装核对及私有 preflight，以公开 RPC 和实际 SDK 执行
固定 loopback SSE，profile 为 `mock=false`；没有 monkeypatch readiness/admission/
runtime/client/transport。主分支允许集没有改变，候选排除后续 M6 文本成果提交。

独立复核接受：正常写→读→容器执行→最终回执及幂等重提；实际 wait.py nonce/CID
Running 后公开取消并清理；杀死自有服务进程、重启核对旧责任且不自动重放公开 payload，
再经 `run.resume(reviewed=true)` 保留原 spec/session/target/预算家族完成恢复。
共 7 次实际 SDK 请求、0 次付费模型调用；不评价真实模型质量。

重启仅允许新增固定零模型 preflight。审查恢复仅另允许新 resumed run 的一次精确
Git advisory，匹配原家族/root、工作区 inode、target/install/location，旧 process
行保持不变；其 exit125/outcome unknown 明确记录为 advisory unavailable，不记为
Git 校验通过。无旧 wait.py/process.py 重放。最终正常关闭 exit0、无强杀或关闭错误，
9 个 process 行均 removed/资源零、9 个 CID 实际 absent。

源码归档 SHA `0ecf77ac46ba3c199b43795c9fc0eef664e9720345bb19b0a770006905f47d0c`；
实际 harness SHA `82cf68caef835897d94d0527b3291a648de78cb4480435846193ef69e0c92366`。
结果 `.verification/linux-l1-public-native-result.json` SHA
`96e3ac67a363c6af35821e5e841875a17927b7c16bd1126c4dcb8e691b434b63`，13 份原始附件
共 2,184,602 字节。原 harness 的 baseline 误判/恢复计数失败及上传/fetch 元数据问题
均保留，范围与完整校验值见 [运行实测](linux-runtime-readiness.md#公开-native-候选的三个实际用例)。

接受范围仅限上述三个候选 case；公共允许集发布、公开 restricted 子任务、剩余故障
矩阵、L2/L3、正式 40 项三轮、安装恢复和 14 天试用仍是独立门槛。

## 四个公开故障候选用例的接受范围

同一 `70870b4`/1072 文件、唯一 guard/policy 配对的独立候选，经过真实完整 stdio CLI、
公开 RPC、实际 SDK 和固定 loopback SSE，于 2026-10-27 完成并独立接受四个 case：
服务死亡后的心跳后代绝对截止；非阻塞双流 flood 下服务与 attach 丢失后的同一期限；
leader 正常退出后 PID1 停止孙进程；运行中及终态后三次公开取消的幂等清理。
候选不包含后续 M6 或当前文档，源码前后核验一致；没有替换产品 runtime/准入链。

deadline 两项固定原持久 BOOTTIME 期限 60 秒、停止容差 5 秒和观测容差 10 秒，
payload 的独立 180 秒界限不计为截止证据。服务离线且重启前，首次观察原 PID 均停止
分别在 deadline 后 0.201/0.211 秒，这是观测上界而非精确死亡时刻。容器已停止、
心跳稳定停止，重启 SDK 计数分别保持 1→1/2→2；raw controller exit124 的 outcome
保持 unknown，不记 payload 成功。flood 的 attach CLI 实际自行消失，未额外发
SIGKILL；已核对其消失后容器仍 Running、心跳继续。两路各写 9,486,336 字节，
runtime log 实读 82,450 字节，在预定限额内。

全批 5 次受控 SDK 请求、零付费调用；预定杀死前两个服务后，最后服务正常 exit0，
无关闭错误、无 rescue，后代及容器清理和资源释放已核对。35 份附件共 11,832,710
字节逐项核验。结果 `linux-l1-fault-matrix-fetch-20261027T050057Z-d1b5e90f/result.json`
SHA-256 `7ff0f23447528cacd8862ddf6bc357beec673d779f1f82873c8446e8e11e8d65`；
同目录只读取回 `receipt.json` SHA-256
`75c3257fea2bc7f592769aac3a5ab3708f71918be6f6c6b2c14d9e3b3178fc22`。
v1 日志 PermissionError 失败及 v2 远端成功后 SSH 下载失败的外层 exit1 均原样保留，
没有重跑来替代原执行；完整源/harness hash、失败与取回证据见
[运行实测](linux-runtime-readiness.md#公开-native-候选的四个实际故障用例)。

非阻塞 flood 不证明阻塞写/D-state IO；附件下载断线不证明运行中 SSH 断开或 L3
任务续跑/重连。迟到/过期启动、create/start 服务死亡窗口、整链 OOM/pids/CPU、
网络/secret/socket、runtime 消失、foreign marker/安装身份/租约丢失、持续 fork
及阻塞输出等仍须分别验收。主分支允许集为空，公开 child、无人值守和公共 L1 尚未
开放；不将本批当作 M1 完成，也不与其他重叠测试批次相加。

## 启动窗口候选的未通过记录

2026-10-27 的三个新窗口候选（迟到启动、create 返回后服务死亡、start 返回后服务
死亡）真实 Linux 执行 exit1，**均未验收**。该批在 `70870b4`/1072 文件候选的独立
staging 上显式增加三个 observer 调用点及有界暂停，属于带观测补丁的源码；不将其
称为此前接受候选的相同字节。归档 SHA-256 为
`edabb1f2549a7af56c28dd59b157a3d0af3de5f7f1a701bfade3f3f0ede5fd24`，
manifest 为 `5558a29fa749cae3066d3c4c2628f7e670159579b006e36f25181e1c94cfc43f`，
checkpoint patch 为 `5dd10b334e7167586c2b7d75c632e16387cf3ab7e7b33dbfb70372438222e5c9`。

仅首项到达真实 create 返回后的 intent 和 start 前的 starting 暂停点。harness 随后
因 CID 文件不满足其预期 `0600` 模式失败，实际模式未记录，不能推断具体权限或
产品缺陷。没有期限后的 release/after-start 记录，注入信号日志为空，另两项未执行。
失败后正常关闭未完成，服务被清理强杀；精确归属的未启动 created CID 经救援删除
并核实不存在。数据库前后仍为 starting/resource_held=1、run running；外层
`cleanup_failure=null` 不等于正常关闭、持久核销或资源释放成功。救援使用使本批
保持失败，未修复或重跑，也不改变此前三个及后续四个候选用例的既有接受范围。

外层 `.verification/linux-l1-faultwindows-20261027T053547Z-1ca3fb09.json` SHA-256
`e9a44ac1dfc36b38c38015e432987731bd4b192a7c8d31b1bacac5e2b2b13ba5`；同名目录
`result.json` 为 `44a9ea35d2d5a655e555805b7631a08482f7070a7a506afc1800561fe62c3268`。
22 份附件共 605,611 字节已核验，取回前后全部 1072 个源码文件一致，无传输错误。
完整输入/补丁/清理证据见[运行实测](linux-runtime-readiness.md#三个启动窗口候选用例的失败收口)。
迟到/过期启动、create/start 死亡窗口及上述其他剩余故障仍待独立验收；主分支允许
集为空，公开 child、无人值守、公共 L1 和 M1 整体的未验收状态不变。

## 三个启动窗口候选的修复与接受范围（2026-10-28）

保留上一节失败后，以 `f953531` 的 1103 个原始 Git blob 构建独立 v2 staging，
仅修改唯一 guard/policy 允许配对及三个 observer 调用点/有界暂停；去掉 observer
可恢复原产品 AST，运行前后源码一致，主分支产品及空允许集不变。本批经真实
完整 stdio CLI、公开 RPC、实际 SDK 和固定 loopback SSE，三个 case 通过并经
独立复核接受；它不同于此前 `70870b4` 的 1072 文件候选，不沿用旧源码身份。

harness 在验证前保存本次 CID 实际元数据：0644、64 字节、单链接、UID1000/GID1001，
位于物理、服务所有、精确 0700 的目录内。只允许 0600/0644，固定父目录/文件身份，
校验 regular/owner/nlink，以 no-follow/nonblocking 句柄有界读取、前后核验身份及
原始完整 CID，再沿用 inspect、名称、标签、安装/store 归属检查。父目录阻止外部
遍历，文件不授予外部写入；没有 chmod 或削弱身份。旧失败 CID 模式仍未知，不能
据新观察反推。失败清理先释放与持久行和安装匹配的已发布 observer 暂停，再正常
关闭；失败仍是 failed/rescue，不能据清理成功改成用例通过。这是验证 harness 修复，
没有据此修改产品生命周期代码。

- 迟到启动：starting 暂停在原 60 秒 BOOTTIME deadline 后 2.244 秒释放，固定 PID1
  实际启动后拒绝过期执行，exit124/outcome unknown，payload canary 未执行。
- create 返回后死亡：持久 intent、实际 created 未启动，观测 CID 不写数据库；
  仅杀死自有服务，重启 reconcile 后 removed/资源零、exit null/outcome unknown，
  无 payload 或 SDK 重放，请求数 3→3。
- start 返回后死亡：持久 starting、实际 payload/后代 Running，服务离线后原 PID
  首次观察均停止于原 deadline 后约 0.232 秒，容器约 0.303 秒；两者均为观测上界。
  重启保持原 deadline，removed/资源零、exit124/outcome unknown，无重放，请求数 4→4。

全批 172.754 秒、exit0，4 次受控 SDK 请求、0 次付费调用。只有两次预定 pidfd
SIGKILL；最后服务正常 exit0，无强制清理杀进程、关闭错误或 rescue。最终 9 行
process 均 removed/resource_held=0，9 个精确 CID absent；其中 3 个固定私有 probe
exit0、3 个 Git advisory exit125/outcome unknown，以及上述 3 个 payload。
advisory unavailable、过期 guard 和未启动结果均不能写成 Git/payload 成功。

外层 `.verification/linux-l1-faultwindows-v2-20261028T075557Z-c85714b7.json` SHA-256
`3b26ea1f86d538f82f59c164870a88363ccc4af97c1b65cb2640fafda2f18835`；同名目录
`result.json` 为 `5916f2acfc18459054e4b505b58eb2c3a60676d90fed87fc473d32cd49345484`。
51 份附件共 8,273,152 字节逐项核验，1103 个源码文件前后保持一致。完整归档、
manifest、harness/driver hash 及本地检查边界见
[运行实测](linux-runtime-readiness.md#三个启动窗口候选的修复与实机验收)。

本批仅接受三个带观测补丁的固定用例，旧失败和此前七个候选用例保留各自证据。
下一批为整链 OOM/pids/CPU；网络/secret/socket、runtime/安装身份/租约、持续 fork、
阻塞输出及公开 child/无人值守仍待验收。公共允许集仍空，公共 L1、M1 整体及 L2/L3
未完成，不把各批重叠回归或九个 process 行累加为验收数量。
