import importlib.util, json, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/"automation.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
done={"a"}; assert m.decide(0,3,done,"a")=="skip"
assert m.decide(2,3,done,"b")=="retry" and m.decide(3,3,done,"b")=="stop"
assert m.decide(4,3,done,"b")=="stop" and done=={"a"}
print("independent checks passed")
