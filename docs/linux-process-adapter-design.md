# Linux L1 实施规格

本文件细化 [Linux 执行链](linux-execution-plan.md) 的 L1，是实现与验收约束，
不是 L1 已通过的声明。基础模块及默认 host 接线已实现并做局部验证；真实
`agent.invoke` / `agent.spawn_subagent` 容器整链仍待验收。
复用同一个 RunService、持久 run/预算家族和服务目录租约，不增加第二个调度循环。

profile/v3、安装 bootstrap、私有目录与截止保证的细化裁定见
[接线契约](linux-execution-profile-contract.md)。其中严格绝对截止尚缺产品整链验收；
加固 PID1 的实现与 helper 验证由单独批次接受，不能将 conmon 相对运行期限或 helper
成功视为 L1 门槛已满足。

## 当前实现与未完成范围

已实现的基础包括：Schema039/040、私有安装 pending/marker/active 协议、FULL-before-
intent、唯一 run snapshot 归属、完整 CID/config 核验、CAS 与 event 同事务、保留取消/
清理责任、历史 policy1 只清理不启动、policy2 boot/BOOTTIME 固定期限、固定完整 seccomp
资产及打包，以及同一 RunService 内的私有零模型 preflight。preflight 生成自己的真实
run/family 与 v2 internal owner，保存解释器/boot/time namespace/时钟区间及原始 stdout，
确认容器 absent、释放责任和持久回执后才产生运行时证明。该证明仍须匹配已接受资产；
当前 `ACCEPTED_GOVERNORS` 为空，Linux L1 能力卡始终 unavailable。

公共 Agent 新接单保存 v3 的实际 target/hash/install，默认 null 配置继续 native-host；
v1/v2 恢复维持 legacy-host，不自动升级。Python/TS/typed/mock/设置选择器与结果展示已有
字段接线，host Native/技能/副本兼容测试已通过。配置 ID 仅作选择，RPC 不接受镜像、
资源、env 或任意 controller 命令。

这些是局部基础，尚未完成：可信 restricted child 支持链及其 legacy overlay 分离、
terminal/runner 会话绑定与私有子归属、无人值守全部准入/派发门槛、included 同设备 bind
mount/alias 复制准入的专测和收口、UI/IPC 最终组合，以及真实公开 Native → 容器修改 →
容器验证 → durable receipt 和服务死亡/截止故障验收。未审计工具实际类仍拒绝，不能用
这些未完成入口获取 host fallback。基础 review 和受控 controller 测试不构成 L1 验收。

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
- `process_workspace.py`：source/copy/mount 的敏感路径和物理身份准入；完整支持链仍按上文收口。

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

## 生命周期与真实入口接线

1. 校验 Linux/rootless、镜像、工作区身份及资源，提交 intent。
2. 确定名称带安装身份和随机 exec_id；私有 0700 CID 文件在挂载之外。
   create 响应失败也按该精确名称/CID 核对，不能假定没有创建。
3. inspect 对照完整 CID、owner 标签、image、mount/env、security 与资源；证据持久
   成功才进入 created。先提交 starting，再 start attach，不能 CAS 失败后仍启动。
4. stdin 在 create/start 一致启用，默认关闭；禁 TTY/detach keys，分离 stdout/stderr。
   输出和运行时日志都限额；固定 PID1/BOOTTIME 治理负责绝对期限，conmon timeout
   仅作相对运行上限。服务 SIGKILL、迟到启动与阻塞 IO 仍需产品故障验收。
   已超过绝对 deadline 不再启动，控制命令也有有限超时。
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
