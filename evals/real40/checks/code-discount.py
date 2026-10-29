import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p)); spec=importlib.util.spec_from_file_location("candidate",p/"orders.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for price,qty,expected in [(101,10,(1010,101,909,10)),(101,9,(909,0,909,0)),(101,11,(1111,111,1000,10)),(1,10,(10,1,9,10)),(99,0,(0,0,0,0))]:
 r=m.create_order(price,qty); assert tuple(r[k] for k in ("subtotal_cents","discount_cents","total_cents","rate_percent"))==expected, r
print("independent checks passed")
