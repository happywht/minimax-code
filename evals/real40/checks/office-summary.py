import csv, pathlib, sys
p = pathlib.Path(sys.argv[1]) / "summary.csv"
with p.open(encoding="utf-8", newline="") as f:
    r = csv.DictReader(f)
    assert r.fieldnames == ["region", "paid_orders", "total_cents"]
    rows = list(r)
assert rows == [{"region":"East","paid_orders":"1","total_cents":"1800"},{"region":"North","paid_orders":"2","total_cents":"12500"},{"region":"South","paid_orders":"2","total_cents":"10000"}], rows
print("independent checks passed")
