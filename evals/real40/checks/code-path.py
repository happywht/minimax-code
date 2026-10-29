import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p))
spec=importlib.util.spec_from_file_location("candidate",p/"paths.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import tempfile
with tempfile.TemporaryDirectory() as root:
 assert pathlib.Path(m.safe_path(root,"child/file")).resolve()==(pathlib.Path(root)/"child/file").resolve()
 for name in ("../outside",str(pathlib.Path(root).resolve())):
  try: m.safe_path(root,name)
  except ValueError: pass
  else: raise AssertionError(name)
print("independent checks passed")
