import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p))
import money
seen=[]; original=money.round_cents
def spy(x):
 seen.append(x); return original(x)
money.round_cents=spy
import orders, discount
assert orders.total(2.125)==round(2.125,2) and discount.discount(3.456)==round(3.456,2)
assert seen==[2.125,3.456]
print("independent checks passed")
