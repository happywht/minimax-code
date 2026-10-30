"""Bounded, authenticated, explicit remote-host browser downloads."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import shlex
from pathlib import PurePosixPath

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .hosts.ssh import build_ssh_args, _stop_transfer_process, ssh_environment

LIMIT = 10 * 1024 * 1024
SLOTS = asyncio.Semaphore(2)


class _BoundedResponse(Response):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            SLOTS.release()
# Walk each component without following symlinks. Hash exactly the bytes read
# from one open descriptor; reject changes to its metadata during the read.
REMOTE = r"""
import base64,hashlib,os,stat,sys
p=base64.b64decode(sys.argv[1]).decode('utf-8')
parts=p.split('/')[1:]
fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
try:
 for part in parts[:-1]:
  new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
  os.close(fd); fd=new
 f=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
 try:
  before=os.fstat(f)
  if not stat.S_ISREG(before.st_mode) or before.st_size>10485760: raise ValueError('not a bounded regular file')
  chunks=[]; size=0
  while True:
   chunk=os.read(f,min(65536,10485761-size))
   if not chunk: break
   chunks.append(chunk); size+=len(chunk)
   if size>10485760: raise ValueError('file too large')
  after=os.fstat(f)
  if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns) or size!=before.st_size: raise ValueError('file changed')
  data=b''.join(chunks)
  sys.stdout.buffer.write((str(size)+' '+hashlib.sha256(data).hexdigest()+'\n').encode()+data)
 finally: os.close(f)
finally: os.close(fd)
"""

async def _read(host, path, *, strict_host_key=False):
    for value in (host['hostname'], host['username']):
        if not value or value.startswith('-') or any(ord(c) <= 32 or ord(c) == 127 or c == '@' for c in value):
            raise ValueError('Unsafe SSH host identity')
    encoded = base64.b64encode(path.encode()).decode('ascii')
    code = base64.b64encode(REMOTE.encode()).decode('ascii')
    command = shlex.join(['python3', '-c', f"import base64;exec(base64.b64decode('{code}'))", encoded])
    argv = build_ssh_args(hostname=host['hostname'], username=host['username'],
                          port=int(host['port']), identity_file=host.get('identity_file') or None,
                          remote_command=command, strict_host_key=strict_host_key)
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL,
                                                env=ssh_environment())
    try:
        async with asyncio.timeout(120):
            header = await proc.stdout.readline()
            match = re.fullmatch(rb'(0|[1-9][0-9]{0,7}) ([0-9a-f]{64})\n', header)
            if not match or int(match[1]) > LIMIT:
                raise ValueError('Invalid remote file or size limit exceeded')
            size = int(match[1]); digest = match[2].decode()
            data = await proc.stdout.readexactly(size)
            if await proc.stdout.read(1) or await proc.wait() != 0:
                raise ValueError('Remote download failed')
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Remote download checksum mismatch')
            return data, digest
    finally:
        await _stop_transfer_process(proc)


def register_host_download_routes(app):
    @app.post('/hosts/download')
    async def download(request: Request):
        # ApplicationBoundary authenticates before this handler reads the body.
        def error(message, status):
            return JSONResponse({'error': message}, status_code=status, headers={'Cache-Control': 'no-store'})
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 16384:
                    return error('Request too large', 413)
            values = json.loads(body)
            keys = {'host_id', 'hostname', 'username', 'port', 'identity_file', 'remote_path', 'service_id'}
            if not isinstance(values, dict) or set(values) != keys:
                raise ValueError('Invalid request')
            if any(not isinstance(values[k], str) for k in keys - {'port'}) or type(values['port']) is not int:
                raise ValueError('Invalid request')
            path = values['remote_path']; target = PurePosixPath(path)
            if (len(path) > 4096 or not path.startswith('/') or not target.name or '\0' in path
                    or any(part in ('', '.', '..') for part in path.split('/')[1:])):
                raise ValueError('Invalid remote path')
            from .app import ensure_db
            from .storage.dao.hosts import HostsDAO
            from .storage.dao.text_artifacts import ArtifactDAO
            db = await ensure_db()
            if db is None: raise ValueError('Storage unavailable')
            hosts, service = HostsDAO(db), ArtifactDAO(db)
            async def binding():
                host = await hosts.get(values['host_id'])
                if (not host or not host['enabled'] or await service.service_id() != values['service_id']
                    or any(host[k] != values[k] for k in ('hostname', 'username', 'port', 'identity_file'))):
                    raise ValueError('Host or service changed; refresh')
                return host
            host = await binding()
            await SLOTS.acquire()
            handed_off = False
            try:
                await binding()
                if await request.is_disconnected():
                    raise asyncio.CancelledError()
                task = asyncio.create_task(_read(host, path))
                try:
                    while not task.done():
                        await asyncio.wait({task}, timeout=0.2)
                        if await request.is_disconnected():
                            raise asyncio.CancelledError()
                    data, digest = await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                await binding()
                response = _BoundedResponse(data, media_type='application/octet-stream', headers={
                    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                    'Content-Security-Policy': "default-src 'none'; sandbox",
                    'X-Host-Download-SHA256': digest, 'X-Host-Download-Service': values['service_id'],
                })
                handed_off = True
                return response
            finally:
                if not handed_off:
                    SLOTS.release()
        except (ValueError, UnicodeError, json.JSONDecodeError, asyncio.IncompleteReadError, TimeoutError, OSError):
            return error('Remote download failed, changed, or exceeded the 10 MiB limit', 409)
