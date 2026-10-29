import json, pathlib, sys
p=pathlib.Path(sys.argv[1])
actual=json.loads((p / "result.json").read_text(encoding="utf-8"))
expected={'differences': [{'id': 'B', 'book_a': 250, 'book_b': 240, 'delta': 10}, {'id': 'C', 'book_a': 300, 'book_b': None, 'delta': None}, {'id': 'D', 'book_a': None, 'book_b': 80, 'delta': None}]}
def matches(a, b):
    if type(a) is not type(b): return False
    if isinstance(b, dict): return a.keys() == b.keys() and all(matches(a[k], v) for k,v in b.items())
    if isinstance(b, list): return len(a) == len(b) and all(matches(x,y) for x,y in zip(a,b))
    return a == b
assert matches(actual, expected), (actual, expected)
print("independent checks passed")
