import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p))
spec=importlib.util.spec_from_file_location("candidate",p/"stock.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from concurrent.futures import ThreadPoolExecutor
for _ in range(5):
 stock=m.Stock(10)
 with ThreadPoolExecutor(max_workers=20) as pool: results=list(pool.map(lambda _:stock.take(),range(100)))
 assert sum(x is True for x in results)==10 and sum(x is False for x in results)==90 and stock.quantity==0
print("independent checks passed")
