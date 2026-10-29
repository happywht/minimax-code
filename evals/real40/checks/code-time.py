import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p))
spec=importlib.util.spec_from_file_location("candidate",p/"dates.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.utc_day("2026-01-01T00:30:00+08:00")=="2025-12-31"
assert m.utc_day("2025-12-31T23:30:00-02:00")=="2026-01-01"
assert m.utc_day("2026-01-01T12:00:00+00:00")=="2026-01-01"
print("independent checks passed")
