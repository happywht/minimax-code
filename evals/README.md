# MiniMax Code 真实评测

固定编程任务集的首轮真实模型评测 harness（规划 5.2「真实模型评测」的
编程类缩小版：修 bug / 跨文件 / 修测试 / 重构 / 边界 / 回归 各 1 项）。

## 用法

```bash
cd agent && uv run python ../evals/run_eval.py \
    --api-key <你的 key> \
    --base-url https://open.bigmodel.cn/api/anthropic \
    --model glm-4.6 \
    [--tasks task1,task3] \
    [--budget 60000]
```

- provider/key/model 通过产品真实 RPC（`provider.create` /
  `provider.set_api_key` / `model.set_current`）配置，评测走的与
  用户在设置页里配的完全同一条路。
- 每任务预算 `--budget`（默认 60000 tokens，超限 run 落 `budget_paused`
  并计为失败——预算护栏本身也在被评测）。
- 每任务前靶场从 `target-template` 重置并打独立 git 基线。

## 产出

- `evals/report-<时间戳>.md`：逐任务验收结果 / 迭代数 / token 用量 /
  failed_tool_calls / requires_review / 自动验证（M3 validation）/
  耗时，失败样本附 pytest 尾部日志。
- 退出码：0 全过；1 有失败；2 环境错误。

## 成本估计

glm-4.6 单价下 6 任务 × 60k token 上限 ≈ 至多 36 万 token 输入输出
合计，实际通常远低于上限（简单任务 <15k）。
