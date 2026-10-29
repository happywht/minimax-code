import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p))
import unittest, io, sales
def run():
 suite=unittest.defaultTestLoader.discover(str(p),pattern="test_sales.py")
 stream=io.StringIO(); result=unittest.TextTestRunner(stream=stream).run(suite)
 return result
r=run(); assert r.testsRun >= 1 and r.wasSuccessful()
sales.discount=lambda quantity: 15
r=run(); assert r.testsRun >= 1 and not r.wasSuccessful()
print("independent checks passed")
