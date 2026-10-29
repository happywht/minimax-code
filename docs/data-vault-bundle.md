# SQLite、文本成果与附件原件的离线数据包

此命令保存 **SQLite 数据库、已登记文本成果版本及附件原件**。当前新建包使用
v11/Schema049；仍可校验、恢复旧 v10/Schema049、v9/Schema049、v8/Schema048、v7/Schema047、v6/Schema046 和 v1/Schema041、v2/Schema042、v3/Schema043、v4/Schema044、v5/Schema045，且不迁移或重新绑定旧库。
它生成 data-only 数据副本，不代表 M7 完整应用备份或可直接启动的迁移。
PDF/XLSX预览沿用既有元数据；046/v6新增办公成果持久校验回执，文件仍复用附件vault。
早期PDF切片沿用v2/inert_text和原因枚举，未改变其当时的Schema044/bundle4。
精确v2+declared=recognized=pdf+inert_text行额外校验状态/原因/预览一致性；
历史TXT类型错配、DOCX及v1分支不变，旧行不重解析。冻结旧raw bundle4 writer/枚举验证合法新PDF行的
离线校验与恢复；此保证限于数据包读取，不代表整个历史应用二进制兼容。
XLSX有界预览同样沿用v2/inert_text，另设精确declared=recognized=xlsx分支，
包括XLSX-only empty/ok；不把PDF校验泛化为任意MIME，不改变历史分支或格式号。
固定旧bundle4 raw writer及metadata/attachments字节与合法新XLSX行双向校验/恢复，
保留DB与原件字节，不调用解析器、迁移或重新计算。公式/缓存只是未验证的存储文本；
详见[XLSX预览边界](attachment-xlsx.md)。
v10 保存数据库所列的检查点未跟踪文件副本，缺失或不安全文件会阻止创建；未登记的检查点副本和目录不会收录，请单独保全。以采集时的字节为准，不证明历史创建时的内容，外部进程仍可能在最终校验后更改文件。v11 另保存已绑定检查点 stash 对象及其可达对象闭包，并在隔离裸仓库校验；这可能包含完整 Git 历史与敏感数据；子模块独立仓库和 Git LFS 外部对象不包含在内。创建时无法访问的来源或对象、浅克隆、alternates 和部分克隆会拒绝创建。旧 v1–v10 不包含这些 Git 对象，旧 v1–v9 也不包含检查点文件副本。
现有设置页 `data.*` 导出、导入和立即备份仍是原有语义，不会自动保存独立 vault 字节。

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

v1 数据包固定 Schema041，只允许版本化 `manifest.json`、SQLite backup API 产生的
`data.db` 和数据库所有历史记录引用的 `text-artifact-vault/<snapshot_id>`。
v2 固定 Schema042，额外纳入所有注册 `attachment-vault/<snapshot_id>` 原件，
包括无法解析、类型异常、空文件和历史版本。v3固定Schema043，新增解析范围和稳定原因字段，保留历史v1元数据。v4固定Schema044，记录历史流式用量可信度迁移，不改变附件字段。v5固定Schema045，扩展MCP transport检查以保留显式streamable_http。v6固定Schema046，新增office_deliveries回执清单。v7固定Schema047，保存各主机的远端服务端口。v8固定Schema048，保存Webhook投递记录。v9固定Schema049，记录检查点来源路径。v10固定Schema049并保存检查点文件副本；v11在相同 schema 上增加有界 Git 对象包。当前工具从049创建v11，历史v1–v10只供校验与恢复；
未知版本、格式/schema组合或表结构拒绝。Schema041升级前可用旧工具先保全v1；
本工具验证/恢复旧包不会调用迁移。没有042/043/044/045/046降级迁移，回退须使用匹配旧程序的升级前备份。
它不受成果列表最新 200 条的显示限制。清单记录应用/格式/schema 版本、服务身份、
schema 哈希及各文件的字节数、SHA-256、成果 ID 和版本。

创建复用服务的排他数据目录租约；正在使用该目录的服务会使创建立即失败。源 DB、
现存 WAL/SHM 先检查 owner、普通文件、链接/重解析点、大小与身份；固定句柄下将 DB
和 WAL 复制到私有暂存并二次校验。SQLite 仅打开私有副本，重建私有 SHM，再通过
backup API 取得包含已提交 WAL 的一致快照，不写源 DB/WAL/SHM。现存 rollback journal
会使操作停止，须先显式恢复源库，工具不自行修复源数据。

只复制数据库引用且大小/哈希一致的不可变成果和附件文件，不读取原项目中的源路径。
未登记孤儿文件与 `.pending-*` 只报告数量，不纳入数据包。单个成果上限 2 MiB、
单个附件上限10 MiB，两个vault合计登记文件至多 10000 个；DB 和临时输入 WAL 各至多
512 MiB、输入 SHM 至多 16 MiB，最终 DB 加两个vault总计至多 1 GiB，清单至多 8 MiB。
任一已登记文件缺失/损坏或超限均失败，不静默遗漏。

不包含项目树、`.minimax` 子任务副本、handoff、未登记外部附件、外部配置文件、技能正文
目录、插件安装文件、SSH 材料、系统钥匙串、容器或镜像，也不复制服务 lock、运行时
marker 和安装所有权文件。**数据库内部的配置、凭据字段和执行记录仍原样保留**，
因此应将数据包视为敏感文件，只处理可信来源。哈希能发现损坏，不能证明来源可信。

## 恢复边界

恢复先在目标父目录建立私有暂存，拒绝 traversal、链接/特殊成员、重复/额外/缺失成员、
压缩炸弹、格式不支持、大小或哈希不符、损坏 SQLite、未知 schema、成果引用不一致
等情况。完整校验 SQLite integrity、外键、固定schema、成果文本及两个vault所有引用后，才原子发布到全新
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

当前 v2 候选以已提交 `dc1d554` 为基线，只覆盖明确列出的 39 个产品/测试/文档候选文件。
Windows 私有 staging 的 13 模块 **258 项通过（106.54 秒）**；前端相关 **70 项通过**，
TypeScript、ESLint、生产构建和 Ruff/diff 检查通过。真实浏览器附件/成果两项通过（15.3 秒），
覆盖原件上传、状态、转义预览、同名版本、下载 hash 与刷新持久化；308 个实际导入的后端
模块路径/hash 均匹配冻结 staging，1526 个源文件字节不变。随后仅统一 mock 的共享服务
身份，相关 20 项、类型与 lint 再次通过；这些批次计数重叠，不相加。

root 实际 Linux 运行使用同一基线的 `agent/`、`.gitattributes` 及契约输入，合计 1096 个
冻结文件：14 模块 **271 项通过、1 项仅 Windows 路径用例跳过（pytest 60.24 秒）**。
附件服务/HTTP、v1/v2 兼容、原 50 项 bundle 及真实 Git 换行夹具共 113 项全部执行，
包括重复取消、WAL/SHM canary、双 vault 的 CLI 创建/校验/恢复和认证下载。
实际导入冻结 POSIX 模块，源文件测试前后 hash 不变；三份原始 stdout/stderr/JUnit 日志
均下载并核验长度/hash。独立复核接受同一证据。未执行付费模型、公共容器或运营恢复。

冻结源 manifest SHA256：`403ab4119c19a1f95038937a5164d345cc194df39e28e56aad40921b3f287c5a`；
Linux 归档 SHA256：`c5bf4558c0252342d729edce698412fa143e19d7e3980ae8ba764f50c9ec61b2`。
报告 `.verification/linux-m6-attachments-regression-20261027T050403Z-12847f23.json`
SHA256：`de16b17fcfdff73482167a27cdd762cea0860fb3f4b8e5ddb2a817fc2ed5d813`。
该冻结源不含无关工作区修改；最终文档回执和 mock 身份修正另有字节差异清单，未改变
Linux 所测后端。以下旧 v1 回执仅证明 `dc1d554` 的历史范围，不冒用旧 152 项为 v2 验证。

旧夹具由 `dc1d554` 原始writer生成，ZIP SHA256为
`3cda5c16ba9d6138881371357427bb5e133d7c016b26d4d8c5381877d29b7731`；仓库提交的gzip仅压缩夹具，
同时保存生成来源、原ZIP哈希和旧writer精确字节，以验证新读旧包及旧读新包拒绝。

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


## DOCX 与 v3/Schema043 本批回执

新包v3及原样恢复旧v1/041、v2/042，在DOCX本批Windows私有staging163项与
最终Linux15模块315项通过、1项仅Windows路径跳过的范围内验证；前端28项、
真实浏览器2项及阶段生产构建通过。两次测试文件修正后的Linux冻结manifest为
`26090f370d052bc9baad44420a82ec6a3a1bc6b80a8af9c204104fbd8e9e1cb6`，
归档为 `0cce5585b33a61e649ed365f11bc34c84e4d1bb72c584887f56b119585047317`，
1106个源码hash不变。Linux报告
`.verification/linux-m6-docx-regression-20261027T054555Z-ec82290d.json` SHA256
`1e69613f6ce5189b7d23011473d205513a70985cad00881d4af9259ea1892145`。
详细Windows绑定、测试修正及未接受边界见[DOCX正文解析](attachment-docx.md#本批验收证据)。
历史v1/v2回执、早期驱动与测试失败记录保留，计数不相加；最终相对上述已测冻结源
仅四份Markdown回执及`attachment_metadata.py`末尾空白清理，该Python文件AST相同；
不重建或冒用Linux归档。data-only边界和运营前显式
重新绑定要求不变，不构成完整备份/恢复、M6或M7整体验收。


## 历史用量迁移与 v4/Schema044（2026-10-28）

044 的历史结算改为待核对，不补造用量或价格，保留原观测/回执与审计；完整规则见
[存储契约](storage-schema.md#schema044历史流式用量可信度)。先停服务，再用匹配旧版本
工具保全升级前 DB+vault；新服务在租约内迁移。恢复旧数据包仍保留原 schema、路径、
身份和字节，只有显式启动/迁移才执行044，不能把 data-only 恢复当运营恢复。

当前 Windows 离线兼容回归最初90项通过、1项新夹具未关闭 SQLite 句柄失败；修复该
夹具后该项单独通过，未宣称整批重新91项通过。Ruff通过。固定旧v3原始 writer 的
SHA-256 为 `6787414419eb71f3405fe767b675d9a56bcea8130464f3c62bc772bdd7b58ed2`；
它实际生成含文本、DOCX和旧预算记录的043包，新工具校验/恢复保持 DB 与原件字节，
不自动迁移，随后显式044仅改变已声明的预算状态/原因/审计；旧writer拒绝新v4。
独立复核接受该限定兼容范围。原始完成输出见工作区
`.verification/anthropic-usage-bundle-compatibility-verification.json`；尚无该批 Linux
实测或完整发行/运营恢复验收，不借用前述旧v1/v2/v3实测作为v4证据。


## MCP配置与v5/Schema045

045只扩展MCP传输类型，不把旧SSE改为HTTP，也不重写URL、headers、凭据、OAuth元数据或工具开关。
迁移事务失败则表/行/索引/触发器/版本记录一起回滚。自定义表约束不属于正式旧表形态时拒绝升级，
不通过硬编码重建静默丢弃；用户索引和触发器仍逐项保留。详见[存储契约](storage-schema.md#schema045mcp-streamable-http)。

恢复旧包不会调用045；从旧schema恢复后，当前创建命令会拒绝输出直到操作者显式完成升级。
回退使用匹配旧程序与升级前DB+vault全量备份，不能把45表直接交给旧程序，也没有逆向迁移。
MCP配置中的秘密保留在SQLite中，数据包仍应按敏感文件处理；恢复不会连接任何MCP服务或触发模型。

## 办公成果回执与v6/Schema046

046仅新增12列的`office_deliveries`表及项目/创建时间/ID索引，不重建旧表。
表保存服务、项目、附件、可选历史run、幂等请求身份、输入hash和严格有界JSON回执。
完整行及报告合同见[存储契约](storage-schema.md#schema046已登记的办公成果)。
结构范围固定`xlsx_plain_cells_v1`，报告保留`business_quality=unverified`、
`render_status=not_attempted`。已有附件预览独立保存，不把预览解析成功当全文件验证。

v6清单必须包含`office_deliveries`列表（空库也为[]），其中每项为精确的12字段行，
`validation_report`为解码后的JSON对象。校验检查严格列/行/报告字段、固定writer与validator
版本、hash/计数/大小、唯一交付ID/附件引用/项目请求key，并核对附件的project/service、
声明与识别类型均为xlsx及报告中的实际hash/大小。不存在第三份文件或第三个vault；
Office引用仍由attachments清单统一计数，DB+两个vault的总量上限不双算办公文件。
旧v1–5清单拒绝office_deliveries字段，历史schema不接受office_deliveries表。

restore保持DB和每份原文件的精确字节，不调用迁移、XLSX生成器、Office全值校验器、
附件解析器、模型或MCP服务。它核对已存回执合同与引用及文件hash；没有原始规范化输入时，
不能重新证明每格等于用户当时输入，也不完成Office渲染或业务内容核验。
冻结v5 writer/attachments/metadata来自8541f43基线checkout的原CRLF字节，
使用独立命名空间运行；v1–4夹具原字节保留。

升级046前先停服务，用匹配v5/045工具保存升级前完整DB+vault备份；046迁移失败则新增
DDL与版本一起回滚。回退旧程序须还原该备份到新数据目录，不能删新表、手工将版本号
改回45，或把v6数据库直接交给旧writer。本节说明合同；最终Windows/Linux执行证据由
本批验收记录提供，不将历史v1–5测试回执冒用为v6结果。

## 显式导入检查点 Git 对象

恢复数据包不会修改任何 Git 仓库。确认恢复后的数据目录和原仓库均不再被应用或其他 Git 进程使用，并核实仓库内容与检查点来源后，可以运行：

```bash
uv run python -m minimax_code.bundle import-checkpoint-git --data-dir /absolute/restored-data --checkpoint-id ckpt_ID --repo /absolute/original-repo --confirm-source
```

命令要求路径与记录的 `source_root` 一致，工作树及 Git common 目录的文件系统身份与采集时一致，HEAD 等于 stash 的第一父提交，并在隔离裸仓库对 pack 和对象闭包进行校验；它只导入对象，不自动应用检查点或修改 refs、index 和工作区。随后可以使用常规检查点恢复操作并检查结果。路径、目录身份和相同提交只能证明采集以来观察到的关联，不能证明检查点创建时的原始仓库身份；复制、迁移或跨主机恢复通常改变目录身份，会被拒绝导入；外部进程和 GC 不受数据目录租约限制，操作不是原子事务。中断或失败可能留下不可达对象；保留数据包以便检查或重试。数据包散列只证明完整性，不能证明来源，务必只导入可信包。
