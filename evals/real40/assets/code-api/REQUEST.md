实现 get_name(record)：优先返回 display_name；无此字段时回退旧字段 name；两者都缺失抛 KeyError。空字符串也是有效值。
