"""Abrupt process loss at occurrence/admission/effect/approval boundaries."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CHILD = r"""
import asyncio, io, json, os, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from minimax_code import app, agent
from minimax_code.config import Config
from minimax_code.ipc import builtins
from minimax_code.ipc.server import IPCServer
from minimax_code.model_snapshot import ModelSnapshot
from minimax_code.runs import get_run_service
from minimax_code.scheduler import JobScheduler
import minimax_code.scheduler as scheduler_module
import minimax_code.schedule_context as context_module
from minimax_code.storage.dao.providers import ProviderDAO
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.tasks import TasksDAO
from minimax_code.agent.core import AgentCore
from minimax_code.agent.llm import StreamChunk
import minimax_code.ipc.handlers_permissions as permissions_module

phase=sys.argv[1]
class Offline:
    default_model='saved-model';mock=True
    close=AsyncMock()
    async def stream_chat(self,*args,**kwargs):
        if phase=='writer':
            with Path('effect.txt').open('a') as stream:
                stream.write('once\n');stream.flush();os.fsync(stream.fileno())
            os._exit(23)
        if phase=='approval':
            yield StreamChunk(tool_call_deltas=[{'id':'x','type':'function','function':{'name':'write_file','arguments':'{"path":"unsafe.txt","content":"bad"}'}}],finish_reason='tool_calls')
        else:
            yield StreamChunk(delta='offline answer',finish_reason='stop',usage={'prompt_tokens':1,'completion_tokens':2,'total_tokens':3})

async def main():
    db=await app.ensure_db()  # real startup interrupted-run recovery
    sessions=SessionsDAO(db);projects=ProjectsDAO(db);runs=AgentRunsDAO(db)
    app.init_runtime=AsyncMock();app.get_sessions_dao=lambda:sessions
    app.get_projects_dao=lambda:projects;app.get_subagent_llm=lambda:Offline()
    app.ensure_codebase_indexer=lambda *args:None;app.get_codebase_indexer=lambda:None
    app.ensure_hook_manager=lambda:None;app.ensure_telemetry_engine=lambda:None
    builtins._build_system_prompt_extra=AsyncMock(return_value=None)
    permissions_module._ensure_permission_store=AsyncMock(return_value=SimpleNamespace(lookup=lambda _: {'action':'allow'}))
    async def factory(db,context):
        await context_module.validate_context(db,context)
        return ModelSnapshot(Offline(),context['model'],context['reasoning_effort'],100000,context['provider_id'],context['protocol'])
    context_module.resolve_model=factory
    server=IPCServer(Config(),stdin=io.StringIO(),stdout=io.StringIO())
    def observe(envelope):
        if phase=='approval' and envelope.get('event')=='permission.request': os._exit(23)
    server.register_listener(observe);scheduler_module._IPC_SERVER=server
    scheduler=JobScheduler(db)
    jobs=await scheduler.list_jobs()
    if not jobs:
        provider=await ProviderDAO(db).create(name='offline',protocol='openai',base_url='https://offline.invalid',models=[{'id':'saved-model'}])
        await projects.create(id='project',name='project',root_path=str(Path.cwd()))
        job=await scheduler.add_job(name='bound',cron_expr='* * * * *',payload={'prompt':'perform work'},context={'project_id':'project','provider_id':provider['id'],'model':'saved-model','reasoning_effort':'high','budget_tokens':100000})
    else: job=jobs[0]
    if phase=='claim':
        await scheduler._occurrences.claim(job['id'],source='manual',manual_key='stable',expected_revision=1)
        os._exit(23)
    if phase=='admit':
        async def die(*args,**kwargs): os._exit(23)
        TasksDAO.link_run=die  # atomic DAO admission already linked run+occurrence
    ack=await scheduler.run_now(job['id'],idempotency_key='stable',expected_revision=1)
    await asyncio.gather(*list(scheduler._inflight),return_exceptions=True)
    await scheduler._reconcile_occurrences()
    occurrences=await scheduler._occurrences.list(job['id'])
    result={'ack':ack,'occurrences':occurrences,'runs':await runs.list_runs(),'sessions':await sessions.list()}
    print(json.dumps(result),flush=True)
    await get_run_service(server).shutdown();await db.close()

try: asyncio.run(main())
except BaseException:
    import traceback
    traceback.print_exc();sys.stderr.flush();os._exit(1)
"""


@pytest.mark.parametrize("boundary", ["claim", "admit", "writer", "approval"])
def test_restart_reuses_occurrence_and_never_replays_ambiguous_effect(tmp_path, boundary):
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        MINIMAX_CODE_DATA_DIR=str(tmp_path / "data"),
        MINIMAX_CODE_WORKSPACE=str(tmp_path),
        MINIMAX_CODE_FORCE_MOCK="1",
    )
    first = subprocess.run(
        [sys.executable, "-c", CHILD, boundary],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert first.returncode == 23, first.stderr
    second = subprocess.run(
        [sys.executable, "-c", CHILD, "recover"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert second.returncode == 0, second.stderr
    data = json.loads(second.stdout.strip().splitlines()[-1])
    assert len(data["occurrences"]) == 1 and len(data["runs"]) == 1 and len(data["sessions"]) == 1
    assert not data["ack"]["created"]
    occurrence = data["occurrences"][0]
    assert occurrence["run_id"] == data["runs"][0]["id"]
    assert (
        occurrence["context"]["model"] == "saved-model"
        and occurrence["context"]["reasoning_effort"] == "high"
    )
    if boundary == "claim":
        assert occurrence["status"] == "completed"
    else:
        assert occurrence["status"] == "interrupted" and data["runs"][0]["status"] == "interrupted"
    assert not (tmp_path / "unsafe.txt").exists()
    if boundary == "writer":
        assert (tmp_path / "effect.txt").read_text() == "once\n"
