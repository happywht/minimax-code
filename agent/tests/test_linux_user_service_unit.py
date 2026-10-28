"""Unit generation is side-effect free and preserves the prepared environment."""
import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate-linux-user-service.py"
spec = importlib.util.spec_from_file_location("generate_linux_user_service", SCRIPT)
unit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unit)


@pytest.fixture
def prepared(tmp_path):
    python = tmp_path / "venv" / "python"
    python.parent.mkdir()
    python.write_text("interpreter")
    python.chmod(0o700)
    workspace = tmp_path / ('project space % $' if os.name == 'nt' else 'project space % $ "')
    workspace.mkdir()
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    data.chmod(0o700)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("built")
    return dict(python=str(python), workspace=str(workspace), data=str(data), web_dist=str(web), port=8765)


def test_unit_pins_loopback_paths_and_keeps_secrets_out(prepared):
    result = unit.generate(**prepared)
    assert '--http-host 127.0.0.1 --http-port 8765' in result
    assert 'Environment="MINIMAX_CODE_NO_DB=0"' in result
    assert 'KillMode=control-group' in result
    assert 'UMask=0077' in result
    assert 'project space %% $' in result
    if os.name != 'nt':
        assert '\\"' in result
    assert 'MINIMAX_API_KEY' not in result
    assert not list(Path(prepared['data']).iterdir())


def test_exec_path_preserves_dollar_and_escapes_specifiers(prepared, tmp_path):
    executable = tmp_path / 'venv $ %' / 'python'
    executable.parent.mkdir()
    executable.write_text('interpreter')
    executable.chmod(0o700)
    prepared['python'] = str(executable)
    assert 'venv $ %%' in unit.generate(**prepared)


@pytest.mark.parametrize('port', [0, -1, 65536, True])
def test_invalid_port_rejected(prepared, port):
    prepared['port'] = port
    with pytest.raises(ValueError, match='port'):
        unit.generate(**prepared)


def test_invalid_or_unbuilt_paths_rejected(prepared, tmp_path):
    for name, path in [('workspace', 'relative'), ('data', str(tmp_path / 'missing')),
                       ('python', str(tmp_path / 'missing')), ('web_dist', str(tmp_path))]:
        with pytest.raises(ValueError):
            unit.generate(**dict(prepared, **{name: path}))
    with pytest.raises(ValueError):
        unit.generate(**dict(prepared, workspace=prepared['workspace'] + '\nExecStart=evil'))


@pytest.mark.skipif(os.name == 'nt', reason='POSIX path names')
def test_ambiguous_working_directory_names_rejected(prepared, tmp_path):
    for name in ('trailing ', 'back\\slash'):
        path = tmp_path / name
        path.mkdir()
        with pytest.raises(ValueError, match='workspace path'):
            unit.generate(**dict(prepared, workspace=str(path)))


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permissions')
@pytest.mark.parametrize('mode', [0o755, 0o500])
def test_data_directory_must_be_private(prepared, mode):
    Path(prepared['data']).chmod(mode)
    with pytest.raises(ValueError, match='private'):
        unit.generate(**prepared)


@pytest.mark.skipif(os.name == 'nt', reason='symlink permissions and POSIX paths')
def test_venv_python_symlink_is_not_resolved(prepared, tmp_path):
    link = tmp_path / 'venv' / 'bin' / 'python'
    link.parent.mkdir()
    link.symlink_to(Path(prepared['python']))
    prepared['python'] = str(link)
    assert f'ExecStart="{link}"' in unit.generate(**prepared)
