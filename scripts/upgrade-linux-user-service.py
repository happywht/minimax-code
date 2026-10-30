#!/usr/bin/env python3
"""Upgrade a generated systemd user unit between prepared releases on Linux.

This requires an operator-quiesced service and a private, trusted local filesystem.
On failure, retain the transaction directory and follow the manual recovery procedure.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

spec = importlib.util.spec_from_file_location('unit_generator', Path(__file__).with_name('generate-linux-user-service.py'))
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
prepare_spec = importlib.util.spec_from_file_location('upgrade_prepare', Path(__file__).with_name('prepare-release-upgrade.py'))
prepare = importlib.util.module_from_spec(prepare_spec)
prepare_spec.loader.exec_module(prepare)
evidence_spec = importlib.util.spec_from_file_location('service_evidence', Path(__file__).with_name('service-upgrade-evidence.py'))
evidence = importlib.util.module_from_spec(evidence_spec)
evidence_spec.loader.exec_module(evidence)


def command(args: list[str], timeout: int = 90) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {args[0]} {args[1]}; {result.stderr[:500]}")
    return result.stdout.strip()


def private_dir(path: Path) -> None:
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError(f'expected an existing absolute directory: {path}')
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError(f'directory must be owned by the user and mode 0700: {path}')


def inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def health(port: int) -> None:
    deadline = time.monotonic() + 30
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    while True:
        try:
            with opener.open(f'http://127.0.0.1:{port}/health', timeout=2) as response:
                raw = response.read(65537)
                if response.status == 200 and len(raw) <= 65536:
                    payload = json.loads(raw)
                    if isinstance(payload, dict) and all(
                        payload.get(key) is True for key in ('ok', 'db', 'web', 'auth_required')
                    ):
                        return
        except (OSError, ValueError, UnicodeError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('health check did not report ok, db, web and auth_required within 30 seconds')
        time.sleep(0.5)


def service_identity(unit: str) -> tuple[int, str]:
    """Read active service identity, including Linux process start time against PID reuse."""
    output = command(['systemctl', '--user', 'show', unit,
                      '--property=ActiveState', '--property=MainPID'])
    properties = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    try:
        pid = int(properties['MainPID'])
        if properties['ActiveState'] != 'active' or pid <= 0:
            raise ValueError('service is not active or has no main process')
        proc = Path('/proc') / str(pid)
        service_net = (proc / 'ns/net').stat()
        own_net = Path('/proc/self/ns/net').stat()
        if (service_net.st_dev, service_net.st_ino) != (own_net.st_dev, own_net.st_ino):
            raise ValueError('service and upgrade command use different network namespaces')
        # The command name in stat is parenthesized and may contain spaces or parentheses.
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        start_time = fields[19]
        if not start_time.isdecimal():
            raise ValueError('invalid process start time')
        return pid, start_time
    except (KeyError, IndexError, OSError, ValueError) as exc:
        raise RuntimeError(f'cannot establish active service process identity: {exc}') from exc


def verify_listener(pid: int, port: int) -> None:
    """Require every IPv4-reachable listening socket on the port to be held by MainPID."""
    try:
        inodes = set()
        loopback = set()
        for table in ('tcp', 'tcp6'):
            rows = (Path('/proc/self/net') / table).read_text().splitlines()
            if not rows or 'local_address' not in rows[0]:
                raise ValueError(f'invalid {table} socket table')
            for row in rows[1:]:
                fields = row.split()
                if len(fields) < 10:
                    raise ValueError(f'invalid {table} socket entry')
                address, port_hex = fields[1].split(':')
                if int(port_hex, 16) != port or fields[3] != '0A':
                    continue
                # Include wildcard and IPv4-mapped IPv6 listeners conservatively: procfs
                # does not expose IPV6_V6ONLY and those sockets could accept IPv4 traffic.
                reachable = (address in ('00000000', '0100007F') if table == 'tcp' else
                             address in ('0' * 32, '0000000000000000FFFF00000100007F'))
                if reachable:
                    if not fields[9].isdecimal() or fields[9] == '0':
                        raise ValueError('invalid listener inode')
                    inodes.add(fields[9])
                    if table == 'tcp' and address == '0100007F':
                        loopback.add(fields[9])
        owned = set()
        for fd in (Path('/proc') / str(pid) / 'fd').iterdir():
            target = os.readlink(fd)
            match = re.fullmatch(r'socket:\[(\d+)\]', target)
            if match:
                owned.add(match.group(1))
        if not loopback or not inodes <= owned:
            raise ValueError('loopback port listener is missing or not owned by the service main process')
    except (OSError, ValueError, IndexError) as exc:
        raise RuntimeError(f'cannot attribute loopback listener: {exc}') from exc


def upgrade(args: argparse.Namespace) -> Path:
    if not args.quiesced:
        raise ValueError('explicit --quiesced acknowledgement is required before stopping active work')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]*\.service', args.unit):
        raise ValueError('unit must be a simple systemd .service name')
    data, backups = Path(args.data), Path(args.backups)
    private_dir(data)
    private_dir(backups)
    roots = [Path(args.old_release), Path(args.new_release)]
    for root in roots:
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ValueError('release roots must be existing absolute non-symlink directories')
    old_root, new_root = (root.resolve() for root in roots)
    if inside(old_root, new_root) or inside(new_root, old_root):
        raise ValueError('old and new releases must be distinct, non-overlapping directories')
    for path, root in ((args.old_python, old_root), (args.old_web, old_root),
                       (args.new_python, new_root), (args.new_web, new_root)):
        # Do not resolve Python itself: venv executable links commonly point outside the release.
        candidate = Path(os.path.abspath(path))
        if not Path(path).is_absolute() or not inside(candidate.parent.resolve() / candidate.name, root):
            raise ValueError('release Python and frontend paths must belong to their release')
    old_python = Path(os.path.abspath(args.old_python))
    old_source = evidence.source_identity(old_root, old_python)
    protected = [data.resolve(), Path(args.workspace).resolve(), old_root, new_root]
    if any(inside(backups.resolve(), p) or inside(p, backups.resolve()) for p in protected):
        raise ValueError('backup directory must not overlap data, workspace, or releases')
    kwargs = dict(workspace=args.workspace, data=args.data, port=args.port)
    old_unit = generator.generate(python=args.old_python, web_dist=args.old_web, **kwargs).encode()
    new_unit = generator.generate(python=args.new_python, web_dist=args.new_web, **kwargs).encode()
    unit_path = Path(args.unit_file)
    if not unit_path.is_absolute() or unit_path.name != args.unit or unit_path.is_symlink() or not unit_path.is_file():
        raise ValueError('unit file must be an existing absolute regular file with the requested unit name')
    unit_stat = unit_path.stat()
    if unit_stat.st_uid != os.getuid() or unit_path.read_bytes() != old_unit:
        raise ValueError('existing unit must be user-owned and exactly match the generated old unit')
    fragment = command(['systemctl', '--user', 'show', args.unit, '--property=FragmentPath', '--value'])
    if Path(fragment) != unit_path:
        raise ValueError('loaded service fragment does not match the supplied unit file')
    if command(['systemctl', '--user', 'show', args.unit, '--property=DropInPaths', '--value']):
        raise ValueError('unit drop-ins are unsupported; preserve them and use manual upgrade')
    if command(['systemctl', '--user', 'show', args.unit, '--property=NeedDaemonReload', '--value']) != 'no':
        raise ValueError('loaded unit is stale; resolve systemd configuration before upgrading')
    command(['systemctl', '--user', 'is-active', '--quiet', args.unit])
    # Serialize cooperating upgrades for this unit. Never remove the lock.
    import fcntl
    lock_path = unit_path.parent / ('.' + args.unit + '.upgrade.lock')
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        transaction = Path(tempfile.mkdtemp(prefix='upgrade-', dir=backups))
        state = {'state': 'prepared', 'unit': args.unit, 'unit_file': str(unit_path),
                 'old_release': str(old_root), 'new_release': str(new_root), 'data': str(data)}
        def record(value: str) -> None:
            state['state'] = value
            temp = transaction / 'state.tmp'
            with temp.open('w', encoding='utf-8') as out:
                json.dump(state, out, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, transaction / 'state.json')
            directory_fd = os.open(transaction, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        (transaction / 'old.service').write_bytes(old_unit)
        record('prepared')
        print(f'Transaction: {transaction}', flush=True)
        switching = False
        try:
            record('stopping')
            command(['systemctl', '--user', 'stop', args.unit])
            # is-active --quiet returns nonzero for failed/unknown too; require explicit inactive.
            active = command(['systemctl', '--user', 'show', args.unit, '--property=ActiveState', '--value'])
            if active != 'inactive':
                raise RuntimeError(f'service did not stop cleanly: {active}')
            record('stopped')
            bundle = transaction / 'data-vault.zip'
            created = prepare.bundle_command(old_python, old_root / 'agent', 'create', '--data-dir', args.data, '--output', str(bundle))
            before_sha, before_size = evidence.digest_file(bundle, 2 * 1024 * 1024 * 1024)
            verified = prepare.bundle_command(old_python, old_root / 'agent', 'verify', '--bundle', str(bundle))
            after_sha, after_size = evidence.digest_file(bundle, 2 * 1024 * 1024 * 1024)
            if (type(created.get('schema_version')) is not int or type(created.get('format_version')) is not int
                    or verified.get('schema_version') != created['schema_version']
                    or verified.get('format_version') != created['format_version']
                    or (before_sha, before_size) != (after_sha, after_size)
                    or evidence.source_identity(old_root, old_python) != old_source):
                raise RuntimeError('Backup receipts, source identity or bundle bytes changed')
            state['backup_evidence'] = {'version': 1, 'backup_sha256': before_sha, 'bytes': before_size,
                'schema_version': verified['schema_version'], 'format_version': verified['format_version'],
                'old_source': old_source}
            record('backup-verified')
            # A changed unit is never silently overwritten.
            current = unit_path.stat()
            if (unit_path.is_symlink() or (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size) !=
                    (unit_stat.st_dev, unit_stat.st_ino, unit_stat.st_mtime_ns, unit_stat.st_size) or
                    unit_path.read_bytes() != old_unit):
                raise RuntimeError('unit changed during upgrade; refusing replacement')
            record('switching-unit')
            switching = True
            temp_fd, temp_name = tempfile.mkstemp(prefix='.minimax-upgrade-', dir=unit_path.parent)
            try:
                with os.fdopen(temp_fd, 'wb') as out:
                    os.fchmod(out.fileno(), stat.S_IMODE(unit_stat.st_mode))
                    out.write(new_unit)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temp_name, unit_path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            record('unit-switched')
            command(['systemctl', '--user', 'daemon-reload'])
            record('starting')
            command(['systemctl', '--user', 'start', args.unit])
            command(['systemctl', '--user', 'is-active', '--quiet', args.unit])
            # Type=simple can report started before the application binds its port.
            health(args.port)
            identity = service_identity(args.unit)
            verify_listener(identity[0], args.port)
            health(args.port)
            if service_identity(args.unit) != identity:
                raise RuntimeError('service main process changed during health check')
            verify_listener(identity[0], args.port)
            if service_identity(args.unit) != identity:
                raise RuntimeError('service main process changed during listener verification')
            record('healthy')
            return transaction
        except BaseException:
            # A new process may have migrated the database. Stop it but never restore or restart old code.
            if switching:
                try:
                    command(['systemctl', '--user', 'stop', args.unit])
                except BaseException:
                    pass
            try:
                record('failed-during-or-after-switch' if switching else 'failed-before-switch')
            except BaseException:
                pass
            raise
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('unit', 'unit-file', 'old-release', 'old-python', 'old-web', 'new-release',
                 'new-python', 'new-web', 'workspace', 'data', 'backups'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--quiesced', action='store_true', help='acknowledge all active work was safely quiesced')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('this command requires Linux systemd user services')
    try:
        result = upgrade(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        print(f'Upgrade failed: {exc}. Inspect the transaction and follow manual recovery; do not restart old code on upgraded data.', file=sys.stderr)
        return 1
    print(f'Upgrade complete; retain backup and transaction: {result}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
