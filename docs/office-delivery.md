# 生成并登记纯数据 XLSX

成果侧栏 →「办公成果」提供真实后端的手动入口。普通聊天 Agent 也可根据用户提供或已读取的资料调用 `generate_table_xlsx`，由服务端绑定当前会话项目；生成前遵守写入权限（默认请求确认），规划模式不允许写入。Agent 返回成果 ID，可在办公成果列表中按 ID 找到并下载。选择有明确绝对根目录的项目，填写显示文件名和结构化 JSON，点击「生成并登记 XLSX」。操作只新建私有 vault 文件，不读取、修改或覆盖项目原文件；普通附件上传、模型回复和已有文本成果不会自动成为办公成果。

本片仅支持一个可见工作表的 text / number / boolean / null。最多 20 列、1000 数据行，含表头最多 5000 格；规范输入 1 MiB、输出 2 MiB。列名大小写折叠后唯一，每行宽度匹配。text 最长 4096 字符，空串规范化为空白；长编号与前导零请使用 text。数字最多 15 个有效位，绝对值不超过 999999999999999，非零绝对值至少 1e-12；拒绝负零、NaN、Infinity 和把布尔值当数字。

示例输入：

```json
{
  "sheet_name": "清单",
  "columns": [{"name": "编号", "kind": "text"}, {"name": "数量", "kind": "number"}],
  "rows": [["00012345678901234567", 12], ["=原样文本", 3], [null, 0]]
}
```

文件名只是 `.xlsx` 显示和下载名称，不是主机路径。表名、列名和值拒绝非法 XML 字符。以 `=`、`+`、`-`、`@` 开头的文字、错误标记和 URL 均强制保存为字符串，不创建公式或超链接。文件不包含宏、外部关系或嵌入对象；不访问外网，不执行模型或办公软件，不产生费用。

可选既有 Run ID 只作历史来源引用：当前服务同项目、会话显式绝对 workspace 与项目根一致的普通 native host 非 plan run；含 admitted/internal 执行元数据、其他引擎或隔离配置的会话不支持。完整会话历史最多 256 条、每条 metadata 最多 64 KiB，超限或非严格 JSON 拒绝，不抽样放行。生成前和提交时复核绑定，不借用 run 的工具权限，不恢复或完成 run，也不证明文件由该 run 生成。可以不关联 run。

## 保存、验证和失败语义

新建文件使用已锁定的 openpyxl 3.1.5，在固定受限子进程中生成。独立验证器检查保存后的全部 ZIP/XML 和单元格坐标、类型、值，完整比较规范输入，拒绝公式、外部关系、宏、额外部件、遗漏或额外单元格。验证不依赖预览窗口。报告固定 `scope=xlsx_plain_cells_v1`、`structure_status=passed`、`business_quality=unverified`、`render_status=not_attempted`，记录输入/文件 SHA-256、计数、库与验证器版本。

这代表纯数据结构和值核对通过。没有 Excel/PDF 渲染、版式/字体/分页验收、OCR、公式重算或业务正确性验收。报告不是数字签名；有人同时篡改文件和报告时，报告本身不提供真实性保证。

Linux/Windows 原生资源门沿用附件解析器：512 MiB 内存、10 CPU 秒，Windows Job Object 限一个进程并随父关闭，Linux 资源限制和父死亡信号；不支持的平台或资源门失败时拒绝生成。固定 Python worker 使用 `-I -B -S`，只接收结构化 JSON，环境仅保留必要 Windows 系统变量，不接收 API key、数据库/vault 路径、shell 或用户命令。生成 wall 15 秒、排队 5 秒、服务总预算 40 秒，stdout 3 MiB、stderr 16 KiB；这不是可执行不可信程序的通用 OS 沙盒。

独立预览进程复用已有 XLSX 严格解析，必须成功后才登记。预览为转义纯文本，A1:T100 / 1500 格 / 64 KiB 等现有上限可能截断；它不还原 Excel 排版。下载复用认证附件字节端点，服务核对不可变快照大小/hash，前端再核对实际字节大小/hash与项目/主机/服务身份。

私有文件先 fsync 并无覆盖原子发布，attachment 元数据与 office receipt 在一个事务内提交。只有提交且回读成功才返回结果；失败不写“已交付”状态。同项目与 request_key 相同且规范请求及项目 id/root_path/created_at 绑定相同则重放同一记录，内容或项目根变更时拒绝；点击“生成同名新版本”使用新 key，保留原文件。修改表格、名称或 run 会切换 key。

取消/超时先终止并回收实际 worker，文件线程与事务必须收束后释放槽。页面切换只停止本页等待，不保证服务器取消；提交边界丢失响应时刷新或同 key 重试可找回已提交记录。文件已发布但数据库失败时，可能留下一份完整私有 orphan；没有登记行或可下载成果，不会自动补录、重扫或删除其他文件。不可用/损坏快照拒绝预览和下载，没有源文件 fallback。

## 独立复验

生成成功后，点击“下载此成果的输入 JSON”“下载 XLSX 原始字节”“下载校验报告”，保留三者。输入快照仅在当前页面内与成功返回的成果关联；离开页面或切换项目后不保留。历史成果或响应丢失后找回的成果无法从服务端恢复输入，须使用生成时另行保存的输入。表单“保存当前输入 JSON”只保存当前编辑器内容，不保证与所选历史成果一致。使用安装了 attachments extra 的同一发行版（只需本地 Python，无数据库或模型）：

```powershell
cd agent
uv run --frozen --extra attachments python -m minimax_code.office_xlsx verify 'C:\exports\清单.xlsx' --spec 'C:\exports\office-input.json'
```

CLI 完整重验包及全部单元格，输出报告 JSON，成功 exit 0；失败固定脱敏信息且非零退出。将 file_sha256/input_sha256 和其余固定报告字段与下载报告比较。输入规范化包括空串变空白；SHA-256 对应规范输入 JSON，不是原 JSON 文件排版字节。命令不会连接服务、执行生成、迁移数据库或调用模型。

## 存储与兼容

Schema046 只新增 `office_deliveries` 和索引，原表不重建。receipt 严格绑定项目/服务、唯一附件及版本化报告；完整输入 JSON 不存入数据库，需自行保存。附件仍使用原不可变 attachment-vault，清单中同一字节只计一次。

bundle6 仅匹配 schema46，含严格 office 清单；当前 bundle11 对应 schema49 并保留该清单；历史 v1–v10 原样只读验证和精确恢复，冻结的历史 writer 不放宽未来版本。恢复是 data-only，不执行迁移、生成、解析器、MCP 或模型；启动新版应用的正常显式迁移另当别论。见 [bundle 协议](data-vault-bundle.md) 与 [存储结构](storage-schema.md)。

既有 data.export JSON 只带数据库元数据，不复制 vault 原字节，不能作为完整办公成果备份。需要完整保全时使用上述离线 bundle；导入元数据也不重新执行内容验证或建立新的生成证据。

升级前用原版创建对应 schema45/bundle5 完整备份。046 故障事务回滚；没有向下迁移。回退旧程序必须还原升级前备份到新的数据目录并使用原版，不可删除新表/修改 schema 号冒充旧库。真实个人数据库的备份、升级和发布由操作者明确执行，测试只使用临时库。

## 入口与维护

认证 RPC 为 `office.generate_xlsx`、`office.list`、`office.get`，严格参数与响应见 [IPC 契约](ipc-contract.md)。普通聊天注册受权限控制的 `generate_table_xlsx` 工具，不增加 run.resume 语义。Agent 必须为一次生成选择稳定 request_key；同一会话、同一 key 和相同输入重试返回原成果，输入不同会冲突，新意图使用新 key。工具生成不关联 run，不表示业务核验。mock 仅空列表并明确拒绝生成/读取，不模拟验证成功文件。

核心文件为 `office_xlsx.py`（模型/生成/全值验证/CLI）、`_office_xlsx_worker.py`、`office_process.py`（固定受限生命周期）、`office_deliveries.py` 与对应 DAO（原子登记）、`office_metadata.py`（共享严格元数据）及 `handlers_office.py`；前端 `OfficeDeliveryPanel` / `officeDeliveryStore` 复用现有附件下载边界。定向测试为 `test_office_xlsx.py`、`test_office_deliveries.py`、`test_handlers_office.py`、`test_office_bundle.py` 与同名 UI / e2e 测试。

官方库说明：[openpyxl 3.1.5 与 wheel 哈希](https://pypi.org/project/openpyxl/3.1.5/)、[新建与保存工作簿](https://openpyxl.readthedocs.io/en/stable/tutorial.html)、[公式不由 openpyxl 计算](https://openpyxl.readthedocs.io/en/stable/simple_formulae.html)、[Cell 类型推断源码](https://openpyxl.readthedocs.io/en/stable/_modules/openpyxl/cell/cell.html)。实际锁定安装版本与全值测试是本片行为依据，stable 文档页版本号不替代 lockfile。


## 聊天中的汇总和图表

普通聊天可要求 Agent 调用 `generate_table_xlsx` 的可选 `summaries` 与 `chart`。例如在原有 `spec` 之外添加：

```json
{"summaries":[{"column":"数量","operation":"sum"},{"column":"数量","operation":"count"},{"column":"数量","operation":"average","decimals":2}],"chart":{"kind":"bar","category_column":"名称","value_column":"数量","title":"数量比较"}}
```

`count` 统计非空值（包含 0 和 false）；`sum`、`average` 仅接受数字列，忽略空值；全空的 sum 为 0，平均值为空。average 默认保留 2 位，可选 0–8 位，采用十进制四舍五入（中点远离零）。计算值直接写入新增的“汇总项目”“汇总值”列，**不是公式**，修改原数据不会自动重算汇总。汇总最多 20 项，输入最多 18 列，最终工作表仍遵守 1000 行及 5000 格上限；精度或范围无法安全表示时拒绝。

`chart` 支持 `bar` 或 `line`，类别引用已有文本/数字列，数值引用已有数字列，图表只引用原始数据行，不含汇总行。带图表文件作为独立 XLSX 出现在“项目附件”，按返回的附件 ID 找到并下载，在 Excel 中查看图表；附件面板仅预览存储单元格，不呈现图表。生成时在受限 worker 用单独的 `xlsx_chart_values_v1` 范围检查所有存储单元格及图表系列引用，未做图表视觉验收或业务验证；该临时回执通过工具结果返回，不写入 `office_deliveries`，不属于或冒充旧 `xlsx_plain_cells_v1` 验收。既有完整附件 bundle 可备份文件，恢复不会重新验证图表。图表生成没有幂等重试，响应不明先检查项目附件，重复请求会创建新版本。用户上传的带图表文件只做有界存储单元格预览，不自动获得生成校验回执。


## 从项目 CSV 附件生成表格

先在项目附件中上传 CSV，再在普通聊天中提供附件名称或 ID、所需列顺序、输出列名和类型。例如：“把项目附件 data.csv 的数量列转成数字、名称列保留文本，按数量求和并生成柱形图。”Agent 可用 `generate_xlsx_from_csv` 读取并核对当前会话项目中的附件原始字节；不会只用 64 KiB 预览，也不会修改原件。工具返回来源附件 ID 和 SHA-256。调用受现有 XLSX 写入规则和规划模式约束，默认请求确认，不能跨项目选择附件。

CSV 必须为 UTF-8，可带 BOM，逗号分隔且第一行是唯一列名；原件最多 1 MiB、1000 数据行、100 个源列，最多选择 20 个不同源列，选中单元格最多 4096 字符；各行宽度必须相同。使用来源列名精确匹配，输出列可重命名和重排；空字符串转为空白。`text` 原样保存，包括以 `=` 开头的内容；`number` 接受十进制数（可带指数），不接受逗号分组、百分号、NaN 或超出 XLSX 安全精度/范围的值，保留前导零的编号应选 `text`；`boolean` 仅接受忽略大小写和两端空格的 true/false。无效编码、表头、行宽或类型转换均失败，不登记结果。最终表格及静态汇总仍受上文 XLSX 行数、格数和精度限制。

可选汇总与图表使用**输出列名**，规则和交付位置见上一节；无图表可同一请求键重试，图表文件不幂等，响应不明先检查附件。来源 ID 和哈希是工具结果中的来源记录，不意味着业务、表格内容或图表视觉已核验。从 XLSX 附件导入见下一节；不能把有界预览当成完整读取或公式计算。


## 从项目 XLSX 附件处理工作表

普通聊天可用 `generate_xlsx_from_xlsx`，提供当前项目附件 ID、源工作表的准确名称、输出文件名、输出工作表名和所选列。模型可重排列、重命名列，按上节规则转换为 `text`、`number`、`boolean`，并使用可选静态汇总或柱形/折线图。工具从 vault 校验完整原件大小和 SHA-256，经固定受限子进程完整读取指定工作表的存储单元格；不会读取预览后截断的数据，也不修改原文件。首行须为非空且无重复的列名，空单元格变空白。数据按存储行列读取，原来的样式、列宽、图表等不复制。

输入原件最多 2 MiB，指定可见表的数据须位于 A1:T1001，矩形范围最多 5000 格，提取文本最多 1 MiB；最终输出还受常规 XLSX 和汇总限制。为避免漏掉数据或误用缓存，含公式（包括有缓存的公式）、错误/日期类型、非 General 数字格式、合并单元格、隐藏行列、筛选、图表或其他复杂工作表功能的表会拒绝。请先在副本中检查结果，把公式替换为**已核实的值**、取消隐藏/合并和筛选、将数值设为 General，再重新上传；日期可先转为文本。处理结果不计算公式或校验业务、事实和视觉。

成功结果附带源附件 ID、SHA-256 和工作表名。纯数据成果使用会话、来源及请求键实现相同输入重试；图表成果位于项目附件且不幂等，响应不明先检查附件列表。解析器拒绝外链、宏及不安全容器；资源门不可用或超时时拒绝，不回退为预览导入。
