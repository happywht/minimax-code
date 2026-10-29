实现 decide(attempts, max_attempts, completed_keys, key)：已完成 key 返回 skip；否则 attempts 小于 max_attempts 返回 retry，达到或超过上限返回 stop。不得改变 completed_keys。此函数仅决策，不执行任务。
