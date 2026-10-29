def unique_ids(items):
    out=[]
    for item in items:
        if item["id"] not in out: out.append(item["id"])
    return out
