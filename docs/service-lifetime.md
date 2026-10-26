# 服务数据目录所有权与关闭

本批收口 CLI 服务的生命周期，适用于 `python -m minimax_code`、console script
及调用该入口的开发/生产启动器。没有数据库迁移或 RPC 格式变化。

## 修复的实际故障

在 `946b15d` 的独立临时数据目录中，两个 stdio CLI 都能回应 ping；运行标记已消失，
第二个服务收到 shutdown 后 12 秒仍未退出。探针最终清理了自己启动的全部进程。
原始证据保留在 `.verification/service-ownership-before.json`，首次超时日志也保留。
这类竞争会让后启动进程恢复另一个活跃服务的任务，不能作为可靠恢复的基础。

## 目录租约与启动标记

CLI 在日志、运行标记、迁移和恢复前取得数据目录的排他 OS 文件锁。
Windows 使用 LockFileEx，POSIX 使用 flock；争用立即拒绝启动。
固定 `.minimax_code_service.lock` 永不替换或删除，不凭 PID 或文件是否存在判断锁归属。
当前实现要求受信的本地文件系统，不承诺网络文件系统、蓄意删除锁文件，或经不同目录
硬链接同一数据库时的排他性。断言同时核对目录与锁文件的物理身份。

描述符不通过 exec 继承；fork 子进程只关闭重复描述符，不能显式解开父进程共享的锁。
没有依靠析构或 atexit 自动解锁。裸 `build_app` / `init_runtime` 嵌入不会隐式取得 CLI
租约；嵌入者须显式取得并断言同一租约，覆盖其整个运行周期。

取得租约后，先捕获旧 marker 报告，再写带独立 owner token 的当前 marker。
恢复只取一次已捕获的旧报告，不读取或删除当前 marker；即使旧 PID 与当前 PID 相同
也不推断它是当前进程。旧 marker 损坏或 PID 无效仍产生未知旧报告。
只有完整关闭后，匹配当前 token 才能清除标记，随后释放锁；旧 owner 不能删除新标记。

## 关闭顺序与失败语义

HTTP 与 stdio 等待同一个保留的 teardown task。重复取消调用者不会再次取消实际清理。

1. 停止调度接单；RunService 停止新接单，排空在途 admission，再取消并等待实际 jobs。
2. 排空普通后台子任务和终端任务，继续等待清理中新登记的后代。
3. 未匹配到可等待所有者的活跃 core、仍存活的终端或生产者清理异常，使关闭失败；
   不清空登记表来假装结束，不在此时关闭共享数据库。
4. 等待调度 dispatch 完成；逐个尝试关闭 MCP，再关闭模型客户端和数据库。
   独立资源的一个关闭失败不会跳过其余尝试，但整体仍为未确认关闭。
5. HTTP 还关闭预览及 WebSocket，并将失败传回 CLI；stdio 同样关闭数据库连接，
   不让 SQLite 工作线程把正常退出拖住。
6. `asyncio.run` 排空事件循环后才清除 marker 并释放租约。

正常保存的 failed receipt 不等于关闭失败。已完成并移出服务的历史失败任务不影响关闭；
关闭期间 admission/任务清理的未处理异常必须报告，不能被 gather(return_exceptions)
吞掉。安全拒绝接单且已保存取消回执允许正常关闭。

未确认关闭时保留 dirty marker 和原始锁描述符，直到实际进程死亡；有活跃非 daemon
线程时锁仍由该进程持有。强制终止按不洁退出处理，下一次启动显示恢复状态，不盲目重放。
本批不据此宣称所有原始执行入口、任意 Python provider、完整 POSIX 工具树或系统沙盒已覆盖。

## 验证、维护和回退

专用回归为 `agent/tests/test_service_lifetime.py` 和 `test_runtime_shutdown.py`，
已加入确定性 core 选择集。覆盖真实 CLI 重复启动、正常退出、异常退出后恢复、
锁竞争、旧回调、未知 marker、重复取消、真实终端停止、在途接单持久化故障和逐连接关闭。
冻结后的 21 个后端模块 **478 项通过、1 项平台跳过**（201.67 秒），浏览器
**7 项通过**（27.5 秒，4 项实际隔离后端、3 项受控 UI），类型与 Ruff 检查通过。
证据为 `.verification/service-lifetime-backend-final2.log`、`service-lifetime-browser-final3.log`、
`service-lifetime-source-freeze.json` 和 `service-lifetime-evidence-final.json`。
50 项关闭/RunService 专项与扩大回归重叠，不累加；这些结果不是全量后端或完整基线三轮。

早期 53 通过/2 失败分别为 Windows venv 包装 PID 误作 Agent PID，以及测试插入时
错位的关闭次数断言；修正夹具后原 55 项通过。后续审查补上真实 admission/任务移除
竞态并扩大覆盖；曾有一次历史失败测试末尾断言错位，日志同样保留。
浏览器曾 6 通过/1 失败：旧 review fixture 在 reload 时用程序化 refresh/setCurrent
与启动刷新竞争，选中不存在于当前映射的会话。改用实际侧栏点击，保持超时和产品隔离
断言；原失败日志、trace、截图保留在 `.verification/service-lifetime-browser-failure/`。

Windows 上 POSIX fork 用例按平台跳过；同一 SHA 产品 lease 模块随后在真实 Linux
Python 3.12.3 上 **7 项通过**，覆盖争用、释放/死亡后接管、fork/exec 和身份替换。
本地/远端/执行后源码 SHA 一致，临时目录删除且无所跟踪进程残留；证据为
`.verification/service-lease-linux.{json,log,exit}`。这仅验证 lease 模块，未跑完整
Linux 产品 CLI、容器或远程长驻任务，不用它填补那些验收门槛。

回退前停止当前服务并确认实际退出，保留数据库和恢复证据，再恢复配套程序文件；
本批无逆向迁移。锁文件可原地保留，不能通过删除文件来解除活跃服务所有权。
本批不是完整备份恢复、Linux 容器适配、systemd 长驻或个人生产版本验收。
