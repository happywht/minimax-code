实现 migrate(config)：将旧字段 timeout 秒改为 timeout_ms 毫秒；若已存在 timeout_ms 则优先保留它并删除旧字段。返回新字典、保留其他字段、不得修改输入。
