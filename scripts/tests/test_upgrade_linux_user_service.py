import importlib.util
import json
import sys
import types
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / 'upgrade-linux-user-service.py'
spec = importlib.util.spec_from_file_location('upgrade_linux', SCRIPT)
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for name in ('old', 'new', 'workspace', 'data', 'backups', 'units'):
        (tmp_path / name).mkdir()
    for name in ('old', 'new'):
        root = tmp_path / name
        (root / 'bin').mkdir()
        (root / 'bin' / 'python').write_text('python')
        (root / 'web').mkdir()
        (root / 'web' / 'index.html').write_text('html')
    args = Namespace(unit='minimax.service', unit_file=str(tmp_path / 'units' / 'minimax.service'),
        old_release=str(tmp_path / 'old'), old_python=str(tmp_path / 'old' / 'bin' / 'python'),
        old_web=str(tmp_path / 'old' / 'web'), new_release=str(tmp_path / 'new'),
        new_python=str(tmp_path / 'new' / 'bin' / 'python'), new_web=str(tmp_path / 'new' / 'web'),
        workspace=str(tmp_path / 'workspace'), data=str(tmp_path / 'data'),
        backups=str(tmp_path / 'backups'), port=8765, quiesced=True)
    monkeypatch.setattr(upgrade, 'private_dir', lambda path: None)
    monkeypatch.setattr(upgrade.os, 'getuid', lambda: Path(args.unit_file).stat().st_uid, raising=False)
    monkeypatch.setattr(upgrade.os, 'O_NOFOLLOW', 0, raising=False)
    if not hasattr(upgrade.os, 'fchmod'):
        monkeypatch.setattr(upgrade.os, 'fchmod', lambda fd, mode: None, raising=False)
    monkeypatch.setitem(sys.modules, 'fcntl', types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *a: None))
    # Portable tests exercise orchestration; executable and ownership checks are covered by generator tests.
    monkeypatch.setattr(upgrade.generator, 'generate', lambda **kw: f"{kw['python']}|{kw['web_dist']}\n")
    Path(args.unit_file).write_bytes(upgrade.generator.generate(python=args.old_python, web_dist=args.old_web).encode())
    calls = []
    def command(argv, timeout=90):
        calls.append(argv)
        if '--property=FragmentPath' in argv:
            return args.unit_file
        if '--property=NeedDaemonReload' in argv:
            return 'no'
        if '--property=ActiveState' in argv:
            return 'inactive'
        if 'create' in argv:
            Path(argv[-1]).write_text('backup')
        return ''
    monkeypatch.setattr(upgrade, 'command', command)
    monkeypatch.setattr(upgrade, 'health', lambda port: None)
    return args, calls, tmp_path


def test_success_orders_backup_before_switch_and_starts(setup):
    args, calls, tmp = setup
    transaction = upgrade.upgrade(args)
    assert json.loads((transaction / 'state.json').read_text())['state'] == 'healthy'
    assert (transaction / 'old.service').read_text().startswith(args.old_python)
    assert (transaction / 'data-vault.zip').read_text() == 'backup'
    assert Path(args.unit_file).read_text().startswith(args.new_python)
    actions = [' '.join(c) for c in calls]
    assert next(i for i, c in enumerate(actions) if ' verify ' in c) < next(i for i, c in enumerate(actions) if 'daemon-reload' in c)
    assert any(' start ' in c for c in actions)


def test_backup_failure_leaves_old_unit_and_does_not_start(setup, monkeypatch):
    args, calls, tmp = setup
    original = upgrade.command
    def failing(argv, timeout=90):
        if 'verify' in argv:
            raise RuntimeError('bad backup')
        return original(argv, timeout)
    monkeypatch.setattr(upgrade, 'command', failing)
    with pytest.raises(RuntimeError, match='bad backup'):
        upgrade.upgrade(args)
    assert Path(args.unit_file).read_text().startswith(args.old_python)
    assert not any('start' in call for call in calls)
    transaction = next((tmp / 'backups').glob('upgrade-*'))
    assert json.loads((transaction / 'state.json').read_text())['state'] == 'failed-before-switch'


def test_health_failure_stops_and_never_restores_old_unit(setup, monkeypatch):
    args, calls, tmp = setup
    monkeypatch.setattr(upgrade, 'health', lambda port: (_ for _ in ()).throw(RuntimeError('unhealthy')))
    with pytest.raises(RuntimeError, match='unhealthy'):
        upgrade.upgrade(args)
    assert Path(args.unit_file).read_text().startswith(args.new_python)
    assert sum('stop' in call for call in calls) == 2
    transaction = next((tmp / 'backups').glob('upgrade-*'))
    assert json.loads((transaction / 'state.json').read_text())['state'] == 'failed-during-or-after-switch'


def test_no_quiescence_or_changed_unit_refuses_before_stop(setup):
    args, calls, tmp = setup
    args.quiesced = False
    with pytest.raises(ValueError, match='quiesced'):
        upgrade.upgrade(args)
    args.quiesced = True
    Path(args.unit_file).write_text('custom configuration')
    with pytest.raises(ValueError, match='exactly match'):
        upgrade.upgrade(args)
    assert not calls


def test_stop_must_be_inactive(setup, monkeypatch):
    args, calls, tmp = setup
    original = upgrade.command
    def running(argv, timeout=90):
        if '--property=ActiveState' in argv:
            return 'failed'
        return original(argv, timeout)
    monkeypatch.setattr(upgrade, 'command', running)
    with pytest.raises(RuntimeError, match='did not stop cleanly'):
        upgrade.upgrade(args)
    assert not any('create' in call for call in calls)
    assert Path(args.unit_file).read_text().startswith(args.old_python)

@pytest.mark.parametrize('body', [b'{', b'{}', b'{"ok":true,"db":false,"web":true,"auth_required":true}', b'{"ok":true,"db":true,"web":true}', b'x' * 65537], ids=['invalid', 'missing', 'db-false', 'auth-missing', 'oversize'])
def test_health_rejects_invalid_or_degraded_response(body, monkeypatch):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return body
    monkeypatch.setattr(upgrade.urllib.request, 'build_opener', lambda *a: types.SimpleNamespace(open=lambda *a, **kw: Response()))
    monkeypatch.setattr(upgrade.time, 'monotonic', iter([0, 31]).__next__)
    with pytest.raises(RuntimeError, match='health check'):
        upgrade.health(8765)


def test_health_rejects_redirect(monkeypatch):
    import urllib.error
    def redirect(*a, **kw):
        raise urllib.error.HTTPError('http://127.0.0.1:8765/health', 302, 'redirect', {}, None)
    monkeypatch.setattr(upgrade.urllib.request, 'build_opener', lambda *a: types.SimpleNamespace(open=redirect))
    monkeypatch.setattr(upgrade.time, 'monotonic', iter([0, 31]).__next__)
    with pytest.raises(RuntimeError, match='health check'):
        upgrade.health(8765)
    assert upgrade.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://example.com') is None


def test_unit_changed_during_backup_is_not_overwritten(setup, monkeypatch):
    args, calls, tmp = setup
    original = upgrade.command
    def changed(argv, timeout=90):
        result = original(argv, timeout)
        if 'verify' in argv:
            Path(args.unit_file).write_text('operator changed unit')
        return result
    monkeypatch.setattr(upgrade, 'command', changed)
    with pytest.raises(RuntimeError, match='unit changed'):
        upgrade.upgrade(args)
    assert Path(args.unit_file).read_text() == 'operator changed unit'
    assert not any('start' in call for call in calls)


def test_start_failure_attempts_stop_and_retains_backup(setup, monkeypatch):
    args, calls, tmp = setup
    original = upgrade.command
    def failing(argv, timeout=90):
        if 'start' in argv:
            raise RuntimeError('start failed')
        return original(argv, timeout)
    monkeypatch.setattr(upgrade, 'command', failing)
    with pytest.raises(RuntimeError, match='start failed'):
        upgrade.upgrade(args)
    assert sum('stop' in call for call in calls) == 2
    transaction = next((tmp / 'backups').glob('upgrade-*'))
    assert (transaction / 'data-vault.zip').is_file()
    assert Path(args.unit_file).read_text().startswith(args.new_python)
