# SQLite 与文本成果的离线数据包

此命令只保存 **SQLite 数据库与已登记的文本成果版本**，适用于 v1.6.0、
Schema041。它生成 data-only 数据副本，不代表 M7 完整应用备份或可直接启动的迁移。
现有设置页 `data.*` 导出、导入和立即备份的语义不变；本批未增加 RPC、迁移或 UI。

## 操作

先正常停止使用源数据目录的 Agent，等待关闭完成。在已安装依赖的 `agent/` 目录执行：

```bash
uv run python -m minimax_code.bundle create --data-dir /absolute/stopped-data --output /absolute/backups/data-vault.zip
uv run python -m minimax_code.bundle verify --bundle /absolute/backups/data-vault.zip
uv run python -m minimax_code.bundle restore --bundle /absolute/backups/data-vault.zip --new-data-dir /absolute/fresh-data
```

Windows 同样可运行这些命令，将参数换为实际 Windows 路径；含空格的路径加引号。
也可使用已经激活的虚拟环境中的 `python`。输出目录和恢复目标的父目录须事先存在；
输出包必须在源数据目录之外。包文件及恢复目标均不得已存在，包括空目录；不覆盖、
不合并，也不把当前目录作为恢复目标。路径中的链接、重解析点和父级穿越会被拒绝。

成功时 stdout 输出含 `ok: true`、`scope: data-only`、版本数、排除项计数及操作边界的
JSON。校验或 I/O 失败时返回非零退出码，不将失败当作备份成功。校验与创建需要私有
临时空间；较大的数据集应预留数 GiB 空间。

## 数据范围

v1 数据包只允许版本化 `manifest.json`、SQLite backup API 产生的 `data.db`，以及
数据库中所有历史登记记录引用的 `text-artifact-vault/<snapshot_id>` 文件。
它不受成果列表最新 200 条的显示限制。清单记录应用/格式/schema 版本、服务身份、
schema 哈希及各文件的字节数、SHA-256、成果 ID 和版本。

创建复用服务的排他数据目录租约；正在使用该目录的服务会使创建立即失败。源 DB、
现存 WAL/SHM 先检查 owner、普通文件、链接/重解析点、大小与身份；固定句柄下将 DB
和 WAL 复制到私有暂存并二次校验。SQLite 仅打开私有副本，重建私有 SHM，再通过
backup API 取得包含已提交 WAL 的一致快照，不写源 DB/WAL/SHM。现存 rollback journal
会使操作停止，须先显式恢复源库，工具不自行修复源数据。

只复制数据库引用且大小/哈希一致的不可变成果文件，不读取原项目中的源路径。
未登记孤儿文件与 `.pending-*` 只报告数量，不纳入数据包。单个成果上限 2 MiB、
登记文件至多 10000 个；DB 和临时输入 WAL 各至多 512 MiB、输入 SHM 至多 16 MiB，
最终 DB 加成果总计至多 1 GiB，清单至多 8 MiB。超限失败，不静默遗漏。

不包含项目树、`.minimax` 子任务副本、handoff、外部附件、外部配置文件、技能正文
目录、插件安装文件、SSH 材料、系统钥匙串、容器或镜像，也不复制服务 lock、运行时
marker 和安装所有权文件。**数据库内部的配置、凭据字段和执行记录仍原样保留**，
因此应将数据包视为敏感文件，只处理可信来源。哈希能发现损坏，不能证明来源可信。

## 恢复边界

恢复先在目标父目录建立私有暂存，拒绝 traversal、链接/特殊成员、重复/额外/缺失成员、
压缩炸弹、格式不支持、大小或哈希不符、损坏 SQLite、未知 schema、成果引用不一致
等情况。完整校验 SQLite integrity、外键、schema 与成果文本后，才原子发布到全新
目录。失败清理只针对工具记录的自有临时文件身份，不递归删除外部内容。

**恢复目录不可直接作为可运行实例启动。** 原项目绝对路径、主机/会话绑定、数据库内
凭据、定时任务、运行状态、process/installation 所有权元数据均保留原值。
命令不运行迁移、模型、任务、调度器、插件或服务，不改写 install/process 身份以认领
旧资源。后续运营使用必须另行进行安全、显式的绑定与状态核对；本批未提供自动重新
绑定或启动功能。单纯 clone 成功不证明旧任务/容器可恢复，也不满足跨主机运行验收。

POSIX 创建和恢复会在发布前同步暂存目录/成果目录，并在无覆盖原子发布后同步目标
父目录；同步失败不报告成功。Windows 会刷新文件内容并使用无覆盖原子发布，
**尚未验证目录项在断电后的持久性**，不能把它等同 POSIX 的目录 fsync 保证。

## 验证范围

冻结的两个新增产品/测试文件在 Windows 离线测试 **50 项通过，57.30 秒**；包含真实
Schema041 库、BOM/CRLF 多版本、仅存 WAL 的已提交数据、全部源文件字节不变、
readonly DB 与 ArtifactService 读取、运行租约拒绝、WAL/SHM 外部链接 canary、恶意包、
已有目标保全、发布前后 fsync 故障清理与格式拒绝。Ruff 和 diff 检查通过。
独立复核另有相关 11 项通过，与该 50 项重叠，不相加。

真实 Linux 私有 staging 使用基线 `196d84b` 的 `agent/`、`.gitattributes`，仅加入上述
两个冻结新增文件（尚未提交的候选），共 1081 个文件。bundle、文本成果、认证 HTTP、
项目、存储、迁移和服务生命周期七模块 **152 项通过、1 项仅 Windows 存储用例跳过**，
pytest 45.37 秒；50 项 bundle 全部执行，实际导入冻结模块并走 POSIX 路径。
全部源文件测试前后哈希不变，三份原始 stdout/stderr/JUnit 日志已下载并校验哈希；
预期警告来自故意构造重复 ZIP 成员的夹具。bundle 命令未启动运营服务，相关生命周期
测试另外启动隔离 mock 服务，不涉及付费模型或公共容器执行。

候选归档 SHA256：`2d050c38f2835852d91617b4a0cbc1ddea093d0ab539f40ed4fc3ee1d5f5f8b3`。
报告 `.verification/linux-m7-bundle-regression-20261027T041616Z-edf23b11.json`
的 SHA256：`74d4d4bb092273f41301c2407e6c3c0e745ef32c5a5faf415626669abff3e461`；
报告内保留源清单及三份原始日志的长度/哈希。Windows、Linux 与独立复核计数有重叠，
不相加。这些测试不代表完整应用备份、运营恢复、公共受限执行、正式真实任务集或
M7 整体验收。
