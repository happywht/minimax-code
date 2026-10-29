import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/'importer.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for value, expected in [("0",0),(" 12 ",12),("001",1)]:
 assert m.parse_quantity(value)==expected
for value in ("", "  ", "-1", "+2", "1.2", "a"):
 try: m.parse_quantity(value)
 except ValueError: pass
 else: raise AssertionError(value)
print("independent checks passed")
