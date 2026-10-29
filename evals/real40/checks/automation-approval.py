import importlib.util, json, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p/"automation.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for job,approval,expected in [({"enabled":False,"requires_approval":False},"approved",False),({"enabled":True,"requires_approval":True},None,False),({"enabled":True,"requires_approval":True},"pending",False),({"enabled":True,"requires_approval":True},"rejected",False),({"enabled":True,"requires_approval":True},"approved",True),({"enabled":True,"requires_approval":False},None,True)]:
 assert m.can_run(job,approval) is expected,(job,approval)
print("independent checks passed")
