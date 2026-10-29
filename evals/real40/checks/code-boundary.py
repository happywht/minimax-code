import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/"discount.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for n,expected in [(0,0),(9,0),(10,10),(19,10),(20,20),(21,20)]:
 result=m.rate_percent(n); assert type(result) is int and result==expected, (n,result)
print("independent checks passed")
