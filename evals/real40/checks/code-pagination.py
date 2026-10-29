import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/"paging.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
items=[{"id":2,"created_at":"2026-01-01"},{"id":3,"created_at":"2025-01-01"},{"id":1,"created_at":"2026-01-01"}]; before=[x.copy() for x in items]
assert [x["id"] for x in m.page(items,0,3)]==[3,1,2]
assert [x["id"] for x in m.page(items,1,1)]==[1]
assert m.page(items,0,0)==[] and m.page(items,10,1)==[] and items==before
print("independent checks passed")
