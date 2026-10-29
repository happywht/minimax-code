import importlib.util, json, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/"automation.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
jobs=json.loads((p/"jobs.json").read_text(encoding="utf-8")); before=json.loads(json.dumps(jobs))
r=m.disable(jobs,"b"); assert r==[{"id":"a","enabled":True},{"id":"b","enabled":False}] and jobs==before and r is not jobs
try: m.disable(jobs,"missing")
except KeyError: pass
else: raise AssertionError("missing ID accepted")
print("independent checks passed")
