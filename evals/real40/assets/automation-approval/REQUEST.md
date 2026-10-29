实现 can_run(job, approval)：如果 enabled 为 false 则返回 false；若 requires_approval 为 true，仅 approval 精确为 approved 时返回 true；无需审批的启用任务返回 true。仅决策，不执行。
