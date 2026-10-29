阅读 sources.json，输出 result.json：claims 数组依次列出已核实的 launch_date 与 capacity，每项含 claim、value、source_ids。仅当 primary 类型来源支持该值时使用它；同值的其他来源也可作为来源，source_ids 升序。对相互矛盾的 secondary 值不得采信。
