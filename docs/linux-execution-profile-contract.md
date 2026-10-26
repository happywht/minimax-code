# Linux L1 profile、安装与接线契约

这是经架构裁定的实施约束，不是已实现能力；与
[L1 规格](linux-process-adapter-design.md) 一起使用。新增模块测试通过、手工运行容器
或能力卡出现均不能单独证明 L1 完成。严格绝对截止仍待服务外治理实现和实机验证。

## 固定 profile 与 v3 接单

`execution_profiles.py` 在服务目录租约内启动时读取服务自有
`execution-profiles.json`，不热更新，不接受 RPC 任意 image、资源、env 或运行参数。
首批仅 `native-host` 和 `linux-podman-l1`；后者绑定本地完整 image ID，缺配置、
runtime、私有目录或 reconcile 失败时显示不可用及原因，不回退 host。

L1 固定 policy_version=1、network=none；默认资源为 memory_bytes=134217728、
cpus_millis=500、pids_limit=64、timeout_seconds=300、tmpfs_bytes=33554432、
output_limit=5242880（每个流），家族并发 1、安装总并发 2。它仍需人工监督；
`unattended_supported=false`，不得称完整 Native OS 沙盒。

Schema040（实施前复核编号）增加 `agents.execution_profile_id TEXT DEFAULT NULL`：

- create 缺省/null 使用默认；update 缺省保留，null 重置默认。
- 新公共根任务 null 解析为 native-host；可信子任务缺省/null 继承父级已冻结 target。
- 子任务显式 ID 必须与父级相同，不能通过显式 host 削弱 Podman。
- 旧 v1/v2 恢复明确使用 legacy host，不从新配置自动升级。
- 新 v3 总是保存解析后的 target；缺失、null、损坏均拒绝。

AgentExecutionSpec v3 保留全部 v2 技能、模型、工具、权限、工作区字段和外层 digest，
另加三个字段。Host target：

```json
{"version":3,"execution_target":{"version":1,"policy_version":1,"mode":"host","profile_id":"native-host"},"execution_target_hash":"<canonical SHA-256>","execution_installation_id":null}
```

Podman target：

```json
{"version":3,"execution_target":{"version":1,"policy_version":1,"mode":"podman","profile_id":"linux-podman-l1","image_id":"sha256:<64 lowercase hex>","memory_bytes":134217728,"cpus_millis":500,"pids_limit":64,"timeout_seconds":300,"tmpfs_bytes":33554432,"output_limit":5242880},"execution_target_hash":"<canonical SHA-256>","execution_installation_id":"<installation UUID>"}
```

Host envelope 不传给仅适用于 Podman 的 ExecutionTargetSpec。Hash 统一 UTF-8、
sort_keys、紧凑分隔、allow_nan=False；资源为整数。profile 停用、移除或执行/策略字段
改变，拒绝原排队/恢复请求，不能重解释 target；仅显示名称变化不撤销。
幂等 receipt 回放先于读取当前配置。既有 invoke/spawn 严格参数不增加临时覆盖。

## 唯一权威与工作区准备

公共根任务仅由 `metadata.agent_execution` v3 授权，不复制一份 process owner。
没有公共 spec 的可信子任务/终端使用 `metadata.process_execution_owner` v1：包含
target/hash/install、parent_run_id、准备状态及自身 copy binding。
`load_execution_binding(run)` 只接受一种合法来源；两种同时出现即拒绝。
孩子使用自己的持久 run、同一预算家族和独立副本，不复用父目录身份。

DAO 核验 run → budget family → family root，显式 RESTRICT 外键，不修改 Schema037。
目录 dev/ino 使用精确十进制 TEXT，不能假定 Windows 身份适合 SQLite int64。
准备完成的 copy binding 必须事务性写入 run metadata：kind、workspace_id、source
identity、owned root identity。内存 independent_copy 标记或单独 manifest 不足以授权。

源码、copy、mount 与 dataDir、凭据、runtime store/socket 根目录双向重叠均拒绝。
拒绝 HOME 自身和包含 HOME 的祖先；允许正常 HOME/project，但另行排除 `.ssh`、
`.aws`、`.config` 等敏感根。不能悄悄忽略 source 内 dataDir 再称复制安全。
检查 canonical 与物理身份、跨 mount/alias、symlink、special file、多个 hardlink，
准备后再次核验。这是 L1 copy 准入，尚非 L2 宿主文件 TOCTOU 或磁盘 quota 保证。

## 安装身份与恢复

复用 `service_lease.assert_owned(data_dir, db.path)`。L1 使用同一 SQLite connection
在后台任务启动前设置并读回 synchronous=FULL，连接重开回到 NORMAL 时不可启动受限任务。
不在并发命令间切换同步级别。

整个 canonical dataDir 须属于当前 UID 且 mode=0700。既有目录不自动 chmod；能力卡
说明实际权限和需操作员处理的原因，host 默认行为保持兼容。仅新目录可明确创建为
0700。execution 子目录 0700、marker 0600，校验 owner、symlink、regular、nlink。
私有父目录足以阻断其他用户访问内部已有 0644 DB，不静默修改其权限。

Schema039 installation 至少保存 singleton=1、install_id、bootstrap_nonce、
phase（bootstrap_pending/active）、host/UID/runtime fingerprint、location fingerprint、
dataDir dev/ino TEXT、marker_hash、created_at/activated_at。
runtime 指纹绑定持久 graphRoot canonical path/dev/ino、driver、UID、主机；临时
runRoot inode 不入指纹。location fingerprint 是主机、UID、graphRoot、canonical
dataDir path 的 hash，排除 dataDir inode，以识别同路径删除重建后的旧容器。

marker 位于挂载/副本之外的私有服务目录，保存 version/install/nonce/fingerprint/
目录身份；无 phase、不可变。安装顺序：

1. 租约、runtime/local store、私有权限、只读 inventory。
2. 仅 DB/marker/process 都为空且无同 location 产品容器时初建。
3. FULL 事务持久化随机身份与 pending。
4. 私有独占临时文件完整写入、fsync；Linux 同目录 hardlink 非覆盖发布，再 fsync parent。
5. 回读校验；FULL CAS pending → active；reconcile 成功后才能 intent/create。

pending/无 marker 仅在指纹/物理身份一致、无 process/同 location 容器时发布原身份；
pending/匹配 marker 完成激活。active/匹配则完整 reconcile。
active/marker 丢失、DB 丢失但 marker 存在、损坏/截断/权限/身份不符均阻止，不自动
重建或认领。复制 DB、换主机/UID/store/dataDir 不自动获取所有权。

允许只读 `podman container list --all --no-trunc --format json` 后逐个完整 CID inspect；
产品/location 标签用于诊断，不构成删除权限。inventory 错误、超限、storage 错误不能
当空列表。同安装无 DB 行阻止准入；foreign 不动；旧备份/丢失 DB 不按标签批量清理。

## 公开接线与 UI

`agent.list` 附 execution_profiles 能力卡：ID、label、mode、available、reason、
unattended_supported=false、boundary summary，不暴露 marker/control 路径，不增加 RPC。
Agents 设置显示默认（根宿主/子继承）、宿主 Native、Linux 容器 L1（需人工监督）。
未知/不可用的已保存 ID 保留并显示原因，receipt 从保存 snapshot 展示实际 target。
同步 Python protocol/handlers/DAO、TS types/typed/mock/store/组件、文档与契约测试。

入口 origin 是服务可信信息，客户端 bool 不提供授权。首批仅公共人工 Agent 和可信
后代；scheduler/webhook/team 的准入和派发均拒绝 L1，包括 host 定时任务间接创建
L1 孩子。profile 检查先于 SkillBindings.capture/provider.install。仅支持无可执行
provider 的文本技能、经过审计的内置工具实际类；MCP、provider、未验证 analyzer
显式拒绝，不能静默丢工具。Hooks 保持禁用，孩子和派发阶段同样检查。

## 截止保证与验收缺口

Podman `--timeout` 从实际启动起计时，不是 intent 的绝对截止。当前可实现保证为
conmon 最大运行时长，以及服务存活时按绝对 deadline 停止。服务被 SIGKILL、start
延迟落地时，预留 30 秒或 start 前检查不能证明严格截止，因此完整 L1 尚不得通过。
需要服务外受信 governor 同时管理 start、deadline、清理及到期 late-start 栅栏。
容器内同 UID wrapper 可被 SIGSTOP；一次性 stop timer 有迟到启动竞争，均不替代。
治理方案须单独审定、真实故障验证，不能通过改名义范围规避验收门槛。

追加确定性测试覆盖 CRUD 缺省/null、v1/v2、v3 损坏、冻结 receipt/幂等、父子继承与
独立 owner、禁止 provider 安装计数为零、无人值守间接入口、所有 bootstrap 窗口、
publish 冲突、active 缺 marker、备份/移动/orphan/inventory 故障、FULL-before-create、
source 敏感重叠/alias/HOME 正常子项目及 128bit 身份。真实 Linux 产品链与治理故障
证据仍必需；基础模块通过不能替代公开入口和最终交付回执。
