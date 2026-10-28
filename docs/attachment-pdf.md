# PDF 页面文字有界预览

PDF 附件原件仍独立保存，最多 10 MiB。可选 `attachments` extra 使用
`pypdf>=6.21.3,<7`（初始锁定 6.21.3），不安装 crypto/full/image extras、Office、
OCR 或渲染程序。操作者在 agent 目录显式执行 `uv sync --extra attachments` 后重启服务；
上传不会安装依赖。缺依赖或无法建立解析资源限制时，原件仍登记并可下载，解析状态明确不可用。

## 范围与结果

只提取 PDF 已有文字，按物理页序显示 `[page 1]`、`[page 2]` 等标签，不读取自定义页码。
页标签、空页提示与换行共同计入最多 64 KiB UTF-8 / 2000 行；截断不拆 UTF-8 字符。
仅展示超限为 `truncated/preview_limit`。展示缓冲满后仍检查其余有界页面；
后页损坏使整个预览失败并清空。没有文字的混合页面标记
`[no extracted text; OCR not attempted]`。

所有页都未提取到非空白文字时，返回 `empty/no_extracted_body_text_no_ocr`、空预览，
UI 明确未尝试 OCR；不能据此断言文件没有内容或确定它来自扫描。
已有 OCR 文字层可以提取，但不验证其准确性。表格、多栏、公式、字体映射与空白不保证语义或排版正确。
不调用图片、嵌入附件、字段、XMP、链接或 writer API，不渲染 PDF，不执行 JavaScript、
OpenAction/AA、Launch、URI 或嵌入文件，也不跟随外部文件或网络引用。
未使用的 PDF 对象不做全图合法性认证。

使用 `PdfReader(BytesIO(data), strict=True, xref_stream_maximum_entries=100000)`。
所有加密文件（包括空密码）在访问页前拒绝为 `parse_failed/unsupported_feature`；
不接收密码，不调用 decrypt。上游构造器自身可能探测空密码，应用仍按 is_encrypted 拒绝。
不支持特性使用相同稳定原因；损坏为 `invalid_package`，不启用宽松修复或无界 fallback。
错误不含异常详情、文档片段或主机路径。

## 资源与生命周期

复用 DOCX 固定短子进程：每服务并发 1、排队 5 秒、墙钟 15 秒、CPU 10 秒、内存 512 MiB，
stdout 512 KiB、stderr 16 KiB；取消、重复取消、超时和超输出均回收实际子进程后释放槽。
父进程独立校验精确 JSON 字段、v2/inert_text、状态/原因、预览大小与标志。
入口只接收匿名 bytes，按已校验字节头路由固定解析器，不传 DB/vault 路径、文件名、
任意命令、插件、环境覆盖或安装指令。`-I -B -S`、Windows Job / Linux RLIMIT 与父进程死亡绑定
保持原边界，详见 [DOCX 资源边界](attachment-docx.md)。这是资源/生命周期限制，
**不是操作系统文件/网络沙箱**。

PDF 额外限制 200 页、page tree 512 entries/32 depth、xref stream 100000 entries，
单页实际已解码 content 8 MiB、累计页面 content 32 MiB。
公开 `apply_configuration` 将 declared stream、array-based stream、zlib/lzw/run-length
输出设 8 MiB；每 stream 解码工作 32 MiB、content parsing work 200000，
XForm invocation 256/page；显式 `jbig2dec_binary=None` 禁止外部 JBIG2 程序。
其他库默认限额不关闭。长度检查发生于受库限额保护的解压后，不能称为“解压前检查”。
累计页面 content 不等于递归 XForm 精确累计解压量；后者受库预算和进程资源上限共同限制。
超页数/流/树/工作量返回 `package_limit`，与仅展示截断区分。

## 元数据、离线包与兼容

继续 `parser_version=2`、`parser_scope=inert_text`，正式含义为有界惰性纯文本预览，
以 declared_type/recognized_type 都为 pdf 及预览页标签表达来源。状态/原因枚举不增加，
quality_status 始终 unverified；不自动进入对话上下文、不产生交付回执。
DOCX scope 保持 docx_body_paragraphs_tables；历史 v1/v2 元数据保持原样，不自动重解析。

继续 Schema044 / bundle4，不新增数据库列、迁移、manifest 字段或 IPC 方法。
bundle 对精确 `v2 + declared=recognized=pdf + inert_text` 组合收紧状态/原因/预览一致性，
不改变旧 v1/v2 DOCX、TXT/CSV 或扩展名错配行的校验分支。
冻结本片之前的 raw bundle4 writer、附件常量和 metadata 枚举的 hash，测试它对合法新 PDF 行
verify/restore，以及当前 reader 对旧 writer 输出恢复；这不是整个历史程序二进制的兼容承诺。
旧 v1/041、v2/042、v3/043 仍按原样验证/恢复而不迁移，详见 [离线数据包](data-vault-bundle.md)。

浏览器 mock 不执行 PDF 解析，明确 parser_unavailable/missing_dependency，原件只保留页内存。
真实 UI 只用纯文本 pre 展示，保留原件下载、未 OCR、物理页码、截断与未验证说明。

## 2026-10-28 验收与回退

Windows 五文件集成 122 项、最终 PDF 21 项和显式 `-B` 后 PDF/DOCX 62 项通过；
集合存在重叠，不相加。前端 31 项、真实浏览器附件 3 项通过，覆盖中英文物理页序、
空图片页的未 OCR 提示、刷新后的持久预览，以及原件下载字节与 SHA-256。

Linux v2 在 raw `ec73e23` 加精确 36 文件 overlay 的独立环境中运行五模块 127 项，
零失败、错误、跳过，60.686 秒；1128 份冻结源码验后字节不变且无额外文件。
独立 HTTP 服务收到真实 pidfd SIGTERM 后约 0.405 秒自然 exit0，无强制清理，
仅证明本次空闲 mock 服务的正常退出，不代表运行中任务或所有子进程退出矩阵。
未调用真实模型、不修改 systemd 或实际用户数据。

v1 整批失败保留：127 项和 SIGTERM 已成功，但最终检查发现五个 Python 字节码
缓存，原清单源码零变化。`-I` 忽略环境中的禁止缓存开关；v2 给实际解析器和
独立 service-death 测试子进程都显式加 `-B`，保留严格零额外文件检查后重新实测。

冻结提案 `.verification/pdf-sigterm-v2-freeze-proposal.json` SHA256：
`558226745540ac1ef3c8be60298aa7e9038478e5fe22f5fc1202eed299936a77`。
实际回执 `.verification/linux-pdf-sigterm-v2-20261028T113956Z-9da2d690/receipt.json`
SHA256：`ee12061af36d575bf48ea1362adba31ce72bea13975991052da3d3ca323b18a7`；
七份附件共 101,768 字节经独立复核，证据保留在当前工作区，不随发布包分发。

无需迁移用户数据库。回退本批程序后历史元数据保持原样，原件可下载；不自动
清除或重解析已有 PDF 行。旧 bundle4 仅按前述数据 reader 范围兼容；完整旧程序
的回退仍应使用匹配的发行版和备份，不承诺旧 UI 能解释全部新预览状态。

## 上游依据

- [pypdf 6.21.3 发布](https://github.com/py-pdf/pypdf/releases/tag/6.21.3)，2026-10-27。
- [PdfReader API](https://pypdf.readthedocs.io/en/6.21.3/modules/PdfReader.html)。
- [文字提取与 OCR/语义限制](https://pypdf.readthedocs.io/en/6.21.3/user/extract-text.html)。
- [strict 模式](https://pypdf.readthedocs.io/en/6.21.3/user/robustness.html)。
- [公开预算配置](https://pypdf.readthedocs.io/en/6.21.3/modules/configuration.html)。
