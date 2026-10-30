"""Authenticated, bounded browser-to-host uploads using the M5 journal."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import tempfile
from pathlib import Path, PurePosixPath

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from .host_download_http import LIMIT, SLOTS
from .hosts.ssh import push_file


def register_host_upload_routes(app):
    @app.post('/hosts/upload')
    async def upload(request: Request):
        def error(message, status):
            return JSONResponse({'error': message}, status_code=status, headers={'Cache-Control': 'no-store'})
        try:
            headers = request.headers.getlist('x-host-upload-metadata')
            if len(headers) != 1 or len(headers[0]) > 16384:
                raise ValueError('Invalid metadata')
            values = json.loads(base64.b64decode(headers[0], validate=True))
            keys = {'host_id', 'hostname', 'username', 'port', 'identity_file', 'remote_path', 'service_id', 'transfer_id'}
            if (not isinstance(values, dict) or set(values) != keys
                    or any(not isinstance(values[k], str) for k in keys - {'port'})
                    or type(values['port']) is not int):
                raise ValueError('Invalid metadata')
            path = values['remote_path']
            if (len(path) > 4096 or not path.startswith('/') or not PurePosixPath(path).name
                    or '\0' in path or any(p in ('', '.', '..') for p in path.split('/')[1:])
                    or not re.fullmatch(r'[0-9a-f]{32}', values['transfer_id'])):
                raise ValueError('Invalid target or transfer ID')
            lengths = request.headers.getlist('content-length')
            if lengths and (len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isascii() or not lengths[0].isdigit()):
                raise ValueError('Invalid length')
            if lengths and int(lengths[0]) > LIMIT:
                return error('File exceeds 10 MiB', 413)
            from .app import ensure_db
            from .storage.dao.hosts import HostsDAO
            from .storage.dao.text_artifacts import ArtifactDAO
            db = await ensure_db()
            if db is None:
                raise ValueError('Storage unavailable')
            hosts, service = HostsDAO(db), ArtifactDAO(db)
            async def binding():
                host = await hosts.get(values['host_id'])
                if (not host or not host['enabled'] or await service.service_id() != values['service_id']
                        or any(host[k] != values[k] for k in ('hostname', 'username', 'port', 'identity_file'))):
                    raise ValueError('Host or service changed')
                return host
            await binding()
            async with SLOTS:
                with tempfile.TemporaryDirectory(prefix='minimax-host-upload-') as directory:
                    local = Path(directory) / 'payload'
                    size = 0
                    async with asyncio.timeout(120):
                        with local.open('xb') as output:
                            async for chunk in request.stream():
                                size += len(chunk)
                                if size > LIMIT:
                                    return error('File exceeds 10 MiB', 413)
                                output.write(chunk)
                    host = await binding()
                    if await request.is_disconnected():
                        raise asyncio.CancelledError()
                    task = asyncio.create_task(push_file(host, local, path, transfer_id=values['transfer_id']))
                    try:
                        while not task.done():
                            await asyncio.wait({task}, timeout=0.2)
                            if await request.is_disconnected():
                                raise asyncio.CancelledError()
                        result = await task
                    finally:
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                    await binding()
                    return JSONResponse({'ok': result.ok, 'exit_code': result.exit_code,
                        'stderr': result.stderr[-2000:], 'remote_path': path,
                        'transfer_id': result.transfer_id, 'publication_state': result.publication_state,
                        'backup_path': result.backup_path}, headers={'Cache-Control': 'no-store'})
        except (ValueError, UnicodeError, json.JSONDecodeError, OSError, TimeoutError, ClientDisconnect):
            return error('Upload rejected or interrupted; inspect transfer status before retrying', 409)
