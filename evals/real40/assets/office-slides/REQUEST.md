根据 brief.json 输出 result.json 的 slides 数组：按顺序生成标题页、指标页、下一步页。每页字段 type、title、items；标题页 items 为空，指标页按 brief 的 metrics 顺序列出 name: value，下一步页按 actions 原顺序列出。
