import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/"inventory.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.lookup({0:"zero",2:"two"},0)=="zero"
assert m.lookup({0:"zero",2:"two"},2)=="two"
for sku,expected in [(-1,ValueError),(3,KeyError)]:
 try: m.lookup({0:"zero",2:"two"},sku)
 except expected: pass
 else: raise AssertionError((sku,expected))
print("independent checks passed")
