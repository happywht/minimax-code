实现 redact(headers)：返回新字典，大小写不敏感地将 authorization、x-api-key 对应值替换为 [REDACTED]；保留其余键值与原始键大小写，不修改输入。
