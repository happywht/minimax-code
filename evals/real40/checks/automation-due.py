import importlib.util, json, pathlib, sys
p = pathlib.Path(sys.argv[1]); spec = importlib.util.spec_from_file_location("candidate", p / "scheduler.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
jobs = json.loads((p / "jobs.json").read_text(encoding="utf-8"))
assert m.due_jobs(jobs, "2026-10-29T09:30:00Z") == ["a", "d"]
assert m.due_jobs(jobs, "2026-10-29T09:29:59Z") == ["d"]
assert m.due_jobs(jobs, "2026-10-29T10:30:00Z") == ["a", "b", "d"]
assert m.due_jobs([], "2026-10-29T09:30:00Z") == []
assert m.due_jobs([{"id":"x","enabled":False,"interval_minutes":1,"last_run":None}], "2026-10-29T09:30:00Z") == []
print("independent checks passed")
