import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p))
spec=importlib.util.spec_from_file_location("candidate",p/"batch.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
stock={"a":3,"b":2}; before=stock.copy()
assert m.update_all(stock,{"a":-2,"b":1})=={"a":1,"b":3} and stock==before
for changes in ({"a":1,"missing":1},{"a":1,"b":-3}):
 try: m.update_all(stock,changes)
 except ValueError: pass
 else: raise AssertionError(changes)
 assert stock==before
print("independent checks passed")
