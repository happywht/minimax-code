"""Maintenance fences tool handoffs and joins background cleanup."""
import asyncio

import pytest
from test_rpc_maintenance_admission import fake_producers, server

from minimax_code.agent.tools import subagents
from minimax_code.agent.tools.base import ToolResult
from minimax_code.orchestrator.subagent import register_background_run
from minimax_code.runtime.maintenance import drain_for_maintenance


async def test_maintenance_waits_for_spawn_handoff_and_background_descendants(monkeypatch):
    fake_producers(monkeypatch)
    rpc = server()
    startup, handoff = asyncio.Event(), asyncio.Event()
    running, launch_child, child_running = asyncio.Event(), asyncio.Event(), asyncio.Event()
    release_child, cleanup, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tool = subagents.SpawnSubagentTool()

    async def impl(**kwargs):
        if kwargs['name'] == 'parent':
            running.set()
            await launch_child.wait()
            child = await tool.run('child', 'work')
            assert child.success
            await cleanup.wait()
            await release_cleanup.wait()
        else:
            child_running.set()
            await release_child.wait()
            cleanup.set()

    async def spawn(self, name, prompt, parent_session_id, wait, sandbox, depends_on):
        if name == 'parent':
            startup.set()
            await handoff.wait()
        task = asyncio.create_task(subagents._drive_run(name=name))
        register_background_run(name, task)
        return ToolResult.ok({'run_id': name})

    monkeypatch.setattr(subagents, '_drive_run_impl', impl)
    monkeypatch.setattr(subagents.SpawnSubagentTool, '_run', spawn)
    request = asyncio.create_task(tool.run('parent', 'work'))
    await startup.wait()
    waiter = asyncio.create_task(drain_for_maintenance(rpc))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not waiter.done()
    late = await tool.run('late', 'work')
    assert not late.success and 'maintenance' in late.error
    handoff.set()
    assert (await request).success
    await running.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    retained = asyncio.create_task(drain_for_maintenance(rpc))
    launch_child.set()
    await child_running.wait()
    assert not retained.done()
    release_child.set()
    await cleanup.wait()
    assert not retained.done()
    release_cleanup.set()
    result = await retained
    assert 'spawn_subagent tool lifecycles' in result.drained
    assert not result.upgrade_ready

async def test_run_drain_tracks_owned_child_and_rejects_external_submit(monkeypatch):
    from types import SimpleNamespace

    from minimax_code import process_context
    from minimax_code.run_ownership import owned_runtime
    from minimax_code.runs import RunJob, RunService, RunServiceClosing, RunShutdownError

    service = RunService()
    entered, launch, launched, finish_parent = (asyncio.Event() for _ in range(4))
    child_done = asyncio.Event()
    owner = SimpleNamespace(owner=SimpleNamespace(run_id='parent'))
    monkeypatch.setattr(process_context, 'current_process_target', lambda: owner)

    async def child(job):
        child_done.set()
        raise ValueError('child cleanup failed')

    async def parent(job):
        token = owned_runtime.set({'run_service': service})
        try:
            entered.set()
            await launch.wait()
            service.submit_owned_child('child-session', child, job=RunJob('child-session', run_id='child'))
            launched.set()
            await finish_parent.wait()
        finally:
            owned_runtime.reset(token)

    service.submit('parent-session', parent, job=RunJob('parent-session', run_id='parent'))
    await entered.wait()
    drain = asyncio.create_task(service.drain())
    await asyncio.sleep(0)
    with pytest.raises(RunServiceClosing):
        service.submit('external', child)
    with pytest.raises(RunServiceClosing):
        service.submit_owned_child('external', child, job=RunJob('external', run_id='fake'))
    launch.set()
    await launched.wait()
    await child_done.wait()
    await asyncio.sleep(0)
    assert not drain.done()
    finish_parent.set()
    with pytest.raises(RunShutdownError) as exc:
        await drain
    assert 'child cleanup failed' in str(exc.value.__cause__)


async def test_cancelled_parent_cannot_start_or_admit_restricted_children(monkeypatch):
    from types import SimpleNamespace

    from minimax_code import process_context
    from minimax_code.run_ownership import owned_runtime
    from minimax_code.runs import RunJob, RunService, RunServiceClosing

    service = RunService()
    owner = SimpleNamespace(owner=SimpleNamespace(run_id='parent'))
    monkeypatch.setattr(process_context, 'current_process_target', lambda: owner)
    blocker_started, release_blocker = asyncio.Event(), asyncio.Event()
    child_submitted, parent_cleanup, release_parent = (asyncio.Event() for _ in range(3))
    child_started = False
    saved = []

    class ChildDAO:
        async def update_run_status(self, run_id, **kwargs):
            saved.append((run_id, kwargs))
            return {'id': run_id}

    async def block(job):
        blocker_started.set()
        await release_blocker.wait()

    async def child(job):
        nonlocal child_started
        child_started = True

    async def parent(job):
        token = owned_runtime.set({'run_service': service})
        try:
            service.submit_owned_child('child-session', child,
                job=RunJob('child-session', run_id='child', dao=ChildDAO()))
            child_submitted.set()
            await asyncio.Event().wait()
        finally:
            with pytest.raises(RunServiceClosing):
                service.submit_owned_child('late-session', child,
                    job=RunJob('late-session', run_id='late'))
            parent_cleanup.set()
            await release_parent.wait()
            owned_runtime.reset(token)

    blocker = service.submit('child-session', block)
    await blocker_started.wait()
    parent_job = service.submit('parent-session', parent,
                                job=RunJob('parent-session', run_id='parent'))
    await child_submitted.wait()
    child_job = service.get_job('child')
    assert service.cancel_run('parent')
    await parent_cleanup.wait()
    release_blocker.set()
    await service.wait(blocker)
    with pytest.raises(asyncio.CancelledError):
        await service.wait(child_job)
    assert not child_started
    assert saved == [('child', {'status': 'cancelled',
                               'error': 'Child execution cancelled before it started'})]
    release_parent.set()
    with pytest.raises(asyncio.CancelledError):
        await service.wait(parent_job)

async def test_background_failure_during_maintenance_fails_closed(monkeypatch):
    from minimax_code.orchestrator.subagent import drain_background_subagents

    release = asyncio.Event()

    async def background():
        await release.wait()
        raise RuntimeError('cleanup failed')

    task = asyncio.create_task(background())
    register_background_run('failed-cleanup', task)
    drain = asyncio.create_task(drain_background_subagents())
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(RuntimeError, match='cleanup was not confirmed') as exc:
        await drain
    assert str(exc.value.__cause__) == 'cleanup failed'


async def test_expired_inherited_spawn_lease_cannot_cross_fence():
    from minimax_code.orchestrator.subagent import drain_background_subagents, subagent_spawn_fence

    release = asyncio.Event()
    async def inherited():
        await release.wait()
        with pytest.raises(RuntimeError, match='admission closed'):
            with subagent_spawn_fence().enter():
                pass

    with subagent_spawn_fence().enter():
        task = asyncio.create_task(inherited())
    await drain_background_subagents()
    release.set()
    await task
