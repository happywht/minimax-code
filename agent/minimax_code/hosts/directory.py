"""Fixed read-only directory listing program for a bound SSH host."""
from __future__ import annotations

import base64
import shlex

# Path components are opened relative to verified directory descriptors. The
# client-supplied path is data, never interpolated into the Python program.
PROGRAM = r'''import base64,itertools,json,os,stat,sys,unicodedata
path=base64.b64decode(sys.argv[1],validate=True).decode('utf-8')
offset=int(sys.argv[2])
parts=path.split('/')[1:]
if not path.startswith('/') or (path!='/' and any(p in ('','.','..') for p in parts)) or offset<0 or offset>10000: raise ValueError('invalid directory')
fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
try:
 for part in ([] if path=='/' else parts):
  new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
  os.close(fd); fd=new
 with os.scandir(fd) as scan:
  items=list(itertools.islice(scan,offset,offset+201))
  entries=[]; consumed=0
  for item in items[:200]:
   try:
    name=item.name
    name.encode('utf-8','strict')
    if not name or any(unicodedata.category(c) in ('Cc','Cf','Cs') for c in name):
     consumed+=1; continue
    if item.is_dir(follow_symlinks=False): kind='directory'
    elif item.is_file(follow_symlinks=False): kind='file'
    else:
     consumed+=1; continue
    row={'name':name,'kind':kind}
    candidate=json.dumps({'path':path,'entries':entries+[row],'offset':offset,'next_offset':10000,'limited':True},ensure_ascii=True)
    if len(candidate)>18000:
     if not consumed: raise ValueError('directory entry too long')
     break
    entries.append(row)
   except (OSError,UnicodeError): pass
   consumed+=1
  more=consumed<len(items)
  nxt=offset+consumed if more and offset+consumed<=10000 else None
  print(json.dumps({'path':path,'entries':entries,'offset':offset,'next_offset':nxt,'limited':more and nxt is None},ensure_ascii=True))
finally: os.close(fd)
'''


def directory_command(path: str, offset: int) -> str:
    source = base64.b64encode(PROGRAM.encode("utf-8")).decode("ascii")
    encoded = base64.b64encode(path.encode("utf-8")).decode("ascii")
    bootstrap = f"import base64;exec(base64.b64decode('{source}'))"
    return shlex.join(["python3", "-c", bootstrap, encoded, str(offset)])
