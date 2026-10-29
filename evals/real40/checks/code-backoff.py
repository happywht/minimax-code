import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p))
spec=importlib.util.spec_from_file_location("candidate",p/"retry.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.delays(5,2,9)==[2,4,8,9,9]
assert m.delays(0,2,9)==[] and m.delays(2,10,5)==[5,5]
print("independent checks passed")
