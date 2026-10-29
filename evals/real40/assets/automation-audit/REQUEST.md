实现 alerts(events)：返回排序后的任务 ID 列表。任务最近一次事件为 failed，或存在 start 但该次 start 后没有 success/failed，则需要告警；事件输入按时间顺序，ID 去重。无 start 的 success/failed 也作为最近状态。不得修改输入。events.json 为示例。
