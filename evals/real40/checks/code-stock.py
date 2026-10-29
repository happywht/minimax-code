import importlib.util, pathlib, sys
p = pathlib.Path(sys.argv[1]) / "inventory.py"
spec = importlib.util.spec_from_file_location("candidate", p)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.CATALOG == {"A-1": (1299, 3), "B-2": (250, 12), "C-3": (4999, 0)}
for data, expected in [(m.CATALOG, 6897), ({}, 0), ({"x": (700, 0)}, 0), ({"x": (123, 2), "y": (4, 5)}, 266)]:
    result = m.total_cents(data)
    assert type(result) is int and result == expected, (data, result, expected)
print("independent checks passed")
