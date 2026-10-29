实现 page(items, offset, limit)：先按每个字典的 created_at 升序、id 升序稳定排序，再返回从 offset 开始最多 limit 项。非负 offset、limit，返回新列表，不修改输入。
