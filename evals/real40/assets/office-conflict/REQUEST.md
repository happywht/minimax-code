根据 evidence.json 输出 result.json，按 field 升序。每个 field 的不同 value 数为 1 时 status=consistent，否则 status=conflict；列出不同 values 和全部 source_ids，均升序；不要裁决冲突。
