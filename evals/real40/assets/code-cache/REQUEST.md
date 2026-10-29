实现 get_valid(cache, key, now)：缓存项是包含 value 和 expires_at 的字典；key 缺失或 now 大于等于 expires_at 时返回 None，否则返回 value。不得修改 cache。
