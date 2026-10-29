import json, pathlib, sys
p=pathlib.Path(sys.argv[1])
actual=json.loads((p / "result.json").read_text(encoding="utf-8"))
expected={'slides': [{'type': 'title', 'title': '季度进展', 'items': []}, {'type': 'metrics', 'title': '指标', 'items': ['完成: 8/10', '延期: 2']}, {'type': 'actions', 'title': '下一步', 'items': ['复核延期', '确定负责人']}]}
def matches(a, b):
    if type(a) is not type(b): return False
    if isinstance(b, dict): return a.keys() == b.keys() and all(matches(a[k], v) for k,v in b.items())
    if isinstance(b, list): return len(a) == len(b) and all(matches(x,y) for x,y in zip(a,b))
    return a == b
assert matches(actual, expected), (actual, expected)
print("independent checks passed")
