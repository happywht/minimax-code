import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p))
spec=importlib.util.spec_from_file_location("candidate",p/"api.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.get_name({"name":"old"})=="old"
assert m.get_name({"display_name":"new","name":"old"})=="new"
assert m.get_name({"display_name":"","name":"old"})==""
try: m.get_name({})
except KeyError: pass
else: raise AssertionError("missing key accepted")
print("independent checks passed")
