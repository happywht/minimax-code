import csv, pathlib, sys
p=pathlib.Path(sys.argv[1])
with (p/"cleaned.csv").open(encoding="utf-8",newline="") as f:
 r=csv.DictReader(f); assert r.fieldnames==["id","name","email"]; rows=list(r)
assert rows==[{"id":"1","name":"Alice","email":"alice@example.com"},{"id":"2","name":"Bob","email":"bob@example.com"},{"id":"3","name":"陈","email":"c@example.com"}], rows
print("independent checks passed")
