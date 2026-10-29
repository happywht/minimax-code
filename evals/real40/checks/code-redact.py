import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/'redact.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
original={"Authorization":"secret","X-API-Key":"key","Content-Type":"json"}; result=m.redact(original)
assert result=={"Authorization":"[REDACTED]","X-API-Key":"[REDACTED]","Content-Type":"json"}
assert original["Authorization"]=="secret" and result is not original
assert m.redact({"authorization":"a","x-api-key":"b"})=={"authorization":"[REDACTED]","x-api-key":"[REDACTED]"}
print("independent checks passed")
