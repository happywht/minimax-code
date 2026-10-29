import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/"orders.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for state in ("open","cancelled"):
 original={"id":"42","status":state,"amount":100}; before=original.copy(); result=m.cancel(original)
 assert result=={"id":"42","status":"cancelled","amount":100} and original==before and result is not original
try: m.cancel({"status":"shipped"})
except ValueError: pass
else: raise AssertionError("shipped cancellation accepted")
print("independent checks passed")
