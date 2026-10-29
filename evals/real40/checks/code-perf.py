import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p))
spec=importlib.util.spec_from_file_location("candidate",p/"unique.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
class Key:
 comparisons=0
 def __init__(self,n): self.n=n
 def __hash__(self): return hash(self.n)
 def __eq__(self,other):
  Key.comparisons+=1
  return isinstance(other,Key) and self.n==other.n
items=[{"id":Key(i)} for i in range(300)]+[{"id":Key(i)} for i in range(300)]
result=m.unique_ids(items)
assert [x.n for x in result]==list(range(300)) and Key.comparisons < 2000
print("independent checks passed")
