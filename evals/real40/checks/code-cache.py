import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/'cache.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
cache={"a":{"value":0,"expires_at":100},"b":{"value":"ok","expires_at":101}}; before={k:v.copy() for k,v in cache.items()}
assert m.get_valid(cache,"a",99)==0 and m.get_valid(cache,"a",100) is None
assert m.get_valid(cache,"b",100)=="ok" and m.get_valid(cache,"missing",100) is None and cache==before
print("independent checks passed")
