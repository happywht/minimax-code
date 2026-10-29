import importlib.util, json, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/"automation.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
events=json.loads((p/"events.json").read_text(encoding="utf-8")); before=json.loads(json.dumps(events))
assert m.alerts(events)==["a","c"] and events==before
assert m.alerts([{"id":"z","status":"failed"},{"id":"z","status":"success"}])==[]
assert m.alerts([{"id":"x","status":"success"},{"id":"x","status":"start"}])==["x"] and m.alerts([])==[]
print("independent checks passed")
