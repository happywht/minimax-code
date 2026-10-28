# XLSX 存储单元格有界预览

项目附件保存最多 10 MiB 的独立不可变原件。可选 `attachments` extra 使用
`openpyxl>=3.1.5,<3.2`（锁定 3.1.5）与 defusedxml；操作者在 agent 目录执行
`uv sync --extra attachments` 后重启服务。上传不会安装依赖，缺依赖明确返回
`parser_unavailable/missing_dependency`，原件仍登记、可下载。

这是一份惰性文本投影，不是电子表格引擎。公式不会执行、重算或验证，宏不会执行，
外部引用不会刷新或访问。预览、缓存值和成功解析均不代表计算正确、办公交付或质量验收。
质量状态始终 `unverified`，附件不自动进入对话上下文。

## 首版范围

每表展示 A1:T100，全工作簿最多 1500 个非空单元格；表头记录表名、顺序、可见性和
原工作簿 1900/1904 epoch。包含 hidden/veryHidden 工作表，不还原样式、图表或 Excel 版式。
每行是转义后的 JSON 纯文本，记录坐标、存储类型和原存储值。共享字符串解析为文本，
普通字符串即使以 `=` 开头仍为字符串。数字保留原词法（例如 `123.400`），布尔值为
存储的 `0`/`1`，错误值和 ISO 日期保留原文；不显示按样式推断的日期或浮点转换结果。

公式记录 `formula` 与 `cached_raw` 两个独立值。`cached_present=false` 表示没有 `<v>`；
空数字 `<v/>` 是 `unknown`，有值是 `stored`，包括零、false 和字符串空值。
所有公式均标记 `cache_verified=false/recalculated=false`。缓存可能缺失或陈旧，
例如 `1+1` 的存储缓存为 `999` 时照实展示 `999`，不会修正为 `2`。
只支持普通公式；共享、数组和 data-table 公式明确拒绝，不展开或猜测。

所有实际单元格为空时使用既有 `v2/inert_text + empty/ok`，预览为空、截断为 false；
空格、零、false 和没有缓存的公式均不算空。此含义只适用于 declared/recognized 都为
xlsx 的精确组合，不借用 DOCX/PDF 的 `no_extracted_body_text_no_ocr`。
有内容但超出窗口、展示数量、64 KiB UTF-8 或 2000 行时为 `truncated/preview_limit`。
窗口外内容仍计入完整有界预检；展示截断不跳过后部的非法 XML、值、关系或包预算。

## 包、资源与生命周期

服务只在 declared_type 与 recognized_type 都为 xlsx 时选择 XLSX 解析器。
父进程向固定内部 bootstrap 传递受信 enum `docx/pdf/xlsx`，worker 严格校验参数数量和
enum；最后两个参数仍是受信 sysconfig 目录。kind 不是 RPC、环境、命令或插件入口。
只通过匿名 stdin 传入 bytes，不传 DB/vault 路径或原文件名；解析器从不保存原件。

复用 DOCX 生命周期：每服务并发 1、排队 5 秒、墙钟 15 秒、CPU 10 秒、内存 512 MiB，
stdout 512 KiB、stderr 16 KiB，`-I -B -S`，实际子进程取消/超限后回收再释放槽。
父进程独立验证精确元数据、XLSX scope/status/reason 与输出上限。Windows Job 和
Linux RLIMIT/父进程死亡绑定不变；这是资源与生命周期约束，**不是文件/网络 OS 沙箱**。

复用严格 ZIP 预检：2048 成员、中央目录 1 MiB、单成员实际解压 8 MiB、合计 32 MiB，
拒绝 ZIP64、多卷、加密、重复/大小写别名、路径穿越、非法压缩及 CRC/长度不符。
全部 XML/rels（包括未展示和未使用部件）以 defusedxml 拒绝 DTD、实体和外部实体。
最多 32 表、100000 个实际单元格、50000 个实际共享字符串、4096 个 cellXfs 样式、
1000000 个 XML 元素，单存储字符串最多 65536 UTF-8 bytes；预算超限整份解析失败。
不以工作表 dimension 决定扫描范围，库的两个 read-only 视图显式 reset_dimensions，
formula/data_only 从相同 bytes 加载并按表/坐标绑定，最后都 close。
库加载前已完整检查共享字符串、样式、实际单元格和关系，不能把 read_only 当内存上限。

首版拒绝**所有 External 关系，包括普通超链接**；这会使部分正常带链接工作簿不可解析。
宏、OLE、嵌入包、ActiveX、外链/连接、非工作表 sheet、未知关系等明确 unsupported。
不会移除这些内容后重新保存或尝试修复。损坏用 `invalid_package`，不安全 ZIP 用
`unsafe_package`，硬预算用 `package_limit`；失败不泄露异常详情或文档内容。

## 持久化与兼容

仍使用 Schema044 / bundle4 和既有 attachment IPC，没有迁移、字段或方法新增。
bundle 对精确 `v2 + declared=recognized=xlsx + inert_text` 单独校验；PDF/DOCX 的分支
和历史字段保持原规则。旧 v1–v4 数据包只校验/恢复原字节，不执行解析、不自动迁移。
固定旧 bundle4 writer/metadata/attachments 源字节用于与合法新 XLSX 行双向读写验证；
这是 data-only reader 的兼容范围，不承诺整个历史应用能够解释新预览。

真实 UI 只用 `<pre>` 展示文本，刷新后保留持久预览，下载仍核对原字节与 SHA-256。
mock 仍只识别 Office ZIP 候选、明确解析器不可用，原件仅保存在页内存，不模拟解析成功。

## 2026-10-28 双平台验证与回退

在独立 Python 3.12.10 环境中，按锁文件安装可选依赖后，XLSX/PDF/DOCX/附件/HTTP/bundle
六模块最终 **162 项通过，零失败、零跳过**，pytest 113.26 秒；测试前后本批28份观察文件
字节不变。XLSX 31、PDF 21、DOCX 41、附件34、HTTP 20、bundle 15，已包含真实受限
worker、拒绝路径、后部坏数据、取消回收、持久预览与原件保全。契约/bundle 专项53项
与此集合重叠，不相加。此前六模块159通过、1个旧PID探针校验替身未接内部关键字失败；
只适配该测试替身后完成上述全绿复跑。初期迭代器边界、Ruff及该失败输出均保留。

前端两模块27项、真实后端 Chromium 附件4项、生产构建、定向ESLint和Ruff通过。
浏览器验证TXT/DOCX/PDF与XLSX原件下载；XLSX覆盖存储/缺失/未知三类公式缓存、
普通 `=` 字符串、中文、空表，以及刷新后的持久预览和下载精确字节/SHA-256。
浏览器使用独立mock服务且无真实模型调用；其测试teardown结束专属进程树，不能算产品
优雅退出验收。PDF独立SIGTERM结果仍只引用其自身批次，本批不重复或混计。

兼容测试固定旧bundle4 raw writer、metadata、attachments三份CRLF字节及哈希，
对合法新XLSX行双向create/verify/restore；恢复DB、schema版本和原件字节一致，并禁止
调用解析器或导入XLSX解析依赖。旧v1–v3仍各按原schema验证/恢复，无自动迁移；
不承诺旧schema容纳新行，也不把data-only互读当整个旧应用或运营恢复的兼容保证。

工作区完整证据 `.verification/xlsx-implementation-v1/manifest.json` SHA256：
`fc49e70253a8eb183e3df996bcf58418b658da2c6ad4e54f8c1b67fa8a063ee5`。
其源码关系为raw Git `0659456` 加所列工作区覆盖，未冒称全仓冻结。

Linux独立stage按raw `0659456` 的agent/与.gitattributes加精确15份已封存agent覆盖运行；
不包含用户hosts修改或同期未提交Podman修改。七个锁定wheel安装在新私有Python3.12环境，
只读复用baseline依赖。六模块实测同样 **162通过，零失败、错误、跳过**，pytest进程
72.529秒（JUnit71.406秒）；各模块数量与Windows一致。1119份冻结源码验后字节不变，
严格零额外文件，无超时或强制清理，pytest自然exit0。此批没有额外SIGTERM探针、
容器/systemd或真实provider调用，不借用PDF旧批的退出结论。
回执 `.verification/linux-xlsx-integration-v1-20261028T125600Z-94267f25/receipt.json`
SHA256：`8bb50e9f2a9a4843489c21f007dac4beb337d0759cf1558e67e12ec7a970dad3`；
result SHA256：`48aec82d2f91c0632f25b3ba9e35b45a39e83746002d6082c0ce495034fd2667`。
root独立核验四份附件hash/size、JUnit及162项范围；证据留在工作区，不随发行包分发。

本切片没有数据库迁移。回退程序或移除可选依赖不会重写已有原件、预览或解析状态；
历史记录不自动清除/重解析，新上传如实显示缺依赖。完整程序回退应使用匹配发行版和
升级前DB+vault备份，不承诺旧UI解释全部新预览。XLSX不读取图片文字、不做OCR；
表格清洗、编辑、重算、独立验算、生成与完整办公交付仍未完成。

## 上游依据

- [openpyxl 发布与安全提示](https://pypi.org/project/openpyxl/)：XML 防护需要 defusedxml。
- [load_workbook 参数](https://openpyxl.pages.heptapod.net/openpyxl/api/openpyxl.reader.excel.html)：
  read_only、data_only、keep_vba、keep_links、rich_text 的含义。
- [公式限制](https://openpyxl.pages.heptapod.net/openpyxl/simple_formulae.html)：库不计算公式。
- [read-only 与维度限制](https://openpyxl.pages.heptapod.net/openpyxl/optimized.html)：显式 close/reset_dimensions。
- [Excel 读取实现](https://openpyxl.pages.heptapod.net/openpyxl/_modules/openpyxl/reader/excel.html)：
  read-only 加载仍读取共享字符串与样式。
- [日期边界](https://openpyxl.pages.heptapod.net/openpyxl/datetime.html)：epoch 与日期转换。
- [Open XML 公式与缓存](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/working-with-formulas)。
