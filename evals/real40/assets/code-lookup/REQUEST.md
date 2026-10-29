修复 inventory.lookup(catalog, sku)：sku 为负整数时抛 ValueError；sku 缺失时抛 KeyError；存在时返回商品值。零是合法编号。不要修改函数签名。
