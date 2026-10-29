import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p))
spec=importlib.util.spec_from_file_location("candidate",p/"config.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
old={"timeout":2,"name":"x"}; result=m.migrate(old)
assert result=={"timeout_ms":2000,"name":"x"} and old=={"timeout":2,"name":"x"} and result is not old
assert m.migrate({"timeout":2,"timeout_ms":500})=={"timeout_ms":500}
assert m.migrate({"name":"x"})=={"name":"x"}
print("independent checks passed")
