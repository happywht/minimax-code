import json, pathlib, sys
p=pathlib.Path(sys.argv[1])
actual=json.loads((p / "result.json").read_text(encoding="utf-8"))
expected={'series': [{'month': '2026-01', 'total': 10}, {'month': '2026-02', 'total': 15}, {'month': '2026-03', 'total': 15}], 'highest_month': '2026-02'}
def matches(a, b):
    if type(a) is not type(b): return False
    if isinstance(b, dict): return a.keys() == b.keys() and all(matches(a[k], v) for k,v in b.items())
    if isinstance(b, list): return len(a) == len(b) and all(matches(x,y) for x,y in zip(a,b))
    return a == b
assert matches(actual, expected), (actual, expected)
print("independent checks passed")
