# DOCX 正文有界解析

项目附件仍保存最多10 MiB的独立不可变原件。可选 `attachments` extra 使用
python-docx 1.2+ 和 defusedxml 0.7.1+；手动在 agent 目录执行
`uv sync --extra attachments`，运行中的服务须重启。上传不会安装依赖。
缺失依赖或无法建立资源限制时明确标记 parser_unavailable，原件仍登记、可下载。

新上传使用算法2、Schema043；旧算法1的字段和值保留，新增范围/原因为legacy_v1，
没有后台重解析。quality_status始终unverified，不自动加入对话上下文，不产生交付回执。
PDF/XLSX与OCR不在本片范围。

## 提取范围

按正文顺序调用 `Document.iter_inner_content()`，表格按行/格以及格内
`iter_inner_content()`递归，最多8层，合并格只取一次。预览保留body/块/行/格位置，
最多64 KiB UTF-8/2000行，截断不会拆开UTF-8字符。
外部超链接只取可见文字，不访问目标。
不读取页眉页脚、修订、文本框、批注、脚注或图片文字，不承诺排版、渲染或完整文档理解。
空正文使用empty/no_extracted_body_text_no_ocr，含义是“未提取到正文；未尝试OCR”。

上游接口：[Document](https://python-docx.readthedocs.io/en/latest/api/document.html)、
[Table/Cell](https://python-docx.readthedocs.io/en/latest/api/table.html)、
[defusedxml安全解析选项](https://pypi.org/project/defusedxml/)。

## 资源与包检查

每服务最多1个短生命周期解析子进程；排队5秒，实际进程墙钟15秒、CPU10秒、
内存512 MiB；stdout最多512 KiB、stderr最多16 KiB。父进程独立校验精确JSON字段、
算法/范围/原因/状态及预览上限。错误只返回稳定原因，不返回异常文本或文档XML。
取消、重复取消、超时和输出超限均终止并等待实际子进程后释放并发槽。

固定内部Python bootstrap使用 `-I -S`，从匿名stdin只接收已校验字节；不接收DB/vault路径、
文件名、任意命令、URL、插件、自动安装指令或服务环境秘密。第三方导入与读取输入前，
Windows使用专用ctypes Job（内存、CPU、活动进程1、kill-on-close）及服务进程创建时间watch，
直接启动base interpreter以避免venv redirector PID错误；Linux先设置RLIMIT_AS/CPU/CORE，
再建立PR_SET_PDEATHSIG与前后PPID检查。只加入服务sysconfig的受信依赖目录，
不处理site启动钩子或PYTHONPATH。其他平台或限制建立失败没有无界回退。

这是资源/生命周期边界，不是OS文件/网络沙箱。Linux只管理固定解析器PID，
不是任意子进程树管理器；该解析器不启动其他程序。Windows Job限制活动进程1。

ZIP中心目录先验限2048成员/1 MiB；拒绝不安全、重复或大小写别名名称、路径穿越、
反斜线、加密、ZIP64、多卷和除stored/deflate外压缩。逐块实际解压，单成员8 MiB、
总32 MiB，检查实际大小/CRC/流结束以及local/central一致性，绝不解压到磁盘。
全部XML/rels及声明为XML的部件以编码感知安全解析器拒绝DTD/实体/外部实体。
检查必需内容类型、根关系、内部关系存在；宏、OLE、嵌入包、ActiveX、altChunk拒绝。
外部关系只允许作为元数据保留的hyperlink，不跟随外部目标。

## 元数据与兼容

parser_scope为legacy_v1、inert_text、docx_body_paragraphs_tables或original_only。
parser_reason为ok、no_extracted_body_text_no_ocr、preview_limit、invalid_package、
unsafe_package、package_limit、unsupported_feature、missing_dependency、limits_unavailable、
queue_timeout、parser_timeout、parser_crash、output_limit、invalid_parser_output、invalid_text、
type_mismatch、unsupported_type、ocr_not_attempted或legacy_v1。

Schema043表重建保留原v1数据，新增原因和范围，允许算法1/2。离线新包只写v3/043，
旧v1/041、v2/042保持原样验证/恢复；恢复不迁移、不重新绑定、不启动服务。
真实旧v2 writer、其历史附件常量及旧包保存为字节固定夹具，用来验证旧reader拒绝v3。
数据包其他大小、引用、vault成员白名单与data-only边界保持原约束。


## 本批验收证据

Windows私有staging后端163项、前端28项、真实浏览器2项及阶段生产构建通过；
全部1538个源码文件hash不变，实际导入stage模块，排除用户同期hosts修改。
其后仅两个测试文件修正，相关3项通过；上述测试修正不改运行逻辑、前端或浏览器夹具；最终另有下述EOF空白清理。
root实际Linux最终15模块315项通过、1项仅Windows路径测试跳过（82.78秒），
1106个冻结源码文件前后hash不变，三份原始日志/JUnit均核验。计数重叠不相加。
Windows回执 `.verification/m6-docx-final/windows-staged-evidence.json` SHA256
`b4a5db64a30d8c110ccf0ac9668a05d94ec550ddf29ca806d68fa62fe7dba91b`；
Linux回执 `.verification/linux-m6-docx-regression-20261027T054555Z-ec82290d.json` SHA256
`1e69613f6ce5189b7d23011473d205513a70985cad00881d4af9259ea1892145`。
原件/坏包/实际解压计数、CPU/内存/输出超限、实际PID、超时/重复取消、服务死亡、
后续健康解析、043迁移及真实旧v1/v2包兼容均在相应平台用例中覆盖。
CI已配置独立extra专项入口，此处报告实际私有staging结果，不代表后续CI作业已运行。
早期驱动/测试失败回执原样保留；最终增加四份文档回执，并清理
`attachment_metadata.py`末尾多余空白；该Python文件AST相同，无功能变化。
该切片不覆盖完整办公交付，也不改变独立Linux启动窗口故障的未验收状态。
