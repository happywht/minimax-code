实现 update_all(stock, changes)：changes 将商品 ID 映射到库存增量；任一 ID 不存在或结果为负则抛 ValueError，且 stock 完全不变；成功返回新字典，也不修改输入。
