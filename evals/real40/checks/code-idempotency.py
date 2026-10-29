import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/'events.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
items=[{"id":"a","value":1}]; item={"id":"a","value":9}; result=m.add_once(items,item)
assert result==[{"id":"a","value":1}] and result is not items and items==[{"id":"a","value":1}]
new={"id":"b","value":2}; result=m.add_once(items,new)
assert result==[{"id":"a","value":1},new] and result is not items and items==[{"id":"a","value":1}]
print("independent checks passed")
