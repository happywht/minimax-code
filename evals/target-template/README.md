# Target Repo — MiniMax Code 真实评测靶场

一个故意带缺陷的小型库存/订单/折扣项目。每个评测任务有明确的
pytest 验收标准；`run_eval.py` 在每个任务开始前把本目录重置回
pristine 状态。

## 内置缺陷地图（评测者参考，任务提示词不会泄露）

| 标记 | 位置 | 缺陷 | 对应任务 |
|---|---|---|---|
| BUG-1 | `inventory.total_value` | 合计时忽略单价 | TASK-1 |
| TASK-2 | `orders.create_order` | 未接入 discount 模块 | TASK-2 |
| TASK-3 | `test_discount`（注释区） | 无 | TASK-3 修的是被注释的错误测试？否——见 tasks.json |
| BUG-2 | `discount.discount_rate` | 10% 档边界 `<=` 应为 `>=` 语义错位 | TASK-6 |

TASK-3（修测试）：`test_discount.test_ten_percent_tier` 在任务开始前
会被 harness 改写为一个**错误期望**（`== 0.0`），任务是识别「代码对、
测试错」并修正测试。
