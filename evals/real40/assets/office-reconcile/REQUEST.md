核对 ledger.json 中两个账本；输出 result.json，含 differences 数组，按 id 升序列出两边金额不同或缺失的项目，字段 id、book_a、book_b、delta；缺失金额用 null，delta 仅两边均存在时为 book_a 减 book_b，否则为 null。
