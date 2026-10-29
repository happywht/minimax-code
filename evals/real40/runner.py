from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / 'manifest.json'
IMMUTABLE = {'code-stock': ['REQUEST.md'], 'office-summary': ['REQUEST.md', 'sales.csv'],
             'automation-due': ['REQUEST.md', 'jobs.json'],
             'code-discount': ['REQUEST.md', 'discount.py'], 'code-lookup': ['REQUEST.md'],
             'code-boundary': ['REQUEST.md'], 'code-cancel': ['REQUEST.md'],
             'code-pagination': ['REQUEST.md'], 'code-csv': ['REQUEST.md'],
             'code-idempotency': ['REQUEST.md'], 'code-redact': ['REQUEST.md'],
             'code-cache': ['REQUEST.md'], 'code-test': ['REQUEST.md', 'sales.py'],
             'code-money': ['REQUEST.md'], 'code-stock-concurrency': ['REQUEST.md'],
             'code-time': ['REQUEST.md'], 'code-batch': ['REQUEST.md'],
             'code-backoff': ['REQUEST.md'], 'code-migrate': ['REQUEST.md'],
             'code-path': ['REQUEST.md'], 'code-api': ['REQUEST.md'], 'code-perf': ['REQUEST.md'],
             'office-source-brief': ['REQUEST.md', 'sources.json'],
             'office-minutes': ['REQUEST.md', 'meeting.json'],
             'office-reconcile': ['REQUEST.md', 'ledger.json'],
             'office-revisions': ['REQUEST.md', 'versions.json'],
             'office-slides': ['REQUEST.md', 'brief.json'],
             'office-chart-data': ['REQUEST.md', 'monthly.csv'],
             'office-conflict': ['REQUEST.md', 'evidence.json'],
             'office-report-check': ['REQUEST.md', 'report.json'],
             'office-clean': ['REQUEST.md', 'contacts.csv'],
             'automation-disable': ['REQUEST.md', 'jobs.json'],
             'automation-retry': ['REQUEST.md'],
             'automation-approval': ['REQUEST.md'],
             'automation-audit': ['REQUEST.md', 'events.json']}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path: Path) -> dict[str, str]:
    return {p.relative_to(path).as_posix(): digest(p) for p in sorted(path.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}


def tasks() -> dict:
    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    items = data['tasks']
    if len(items) != 40 or len({x['id'] for x in items}) != 40:
        raise ValueError('manifest must contain 40 unique tasks')
    if {c: sum(t['category'] == c for t in items) for c in ('code', 'office', 'remote', 'automation')} != {'code': 20, 'office': 10, 'remote': 5, 'automation': 5}:
        raise ValueError('invalid category counts')
    return {t['id']: t for t in items}


def ready(task_id: str) -> dict:
    task = tasks()[task_id]
    if task['readiness'] != 'ready' or task['asset'] not in IMMUTABLE:
        raise ValueError(f'{task_id} is not ready')
    return task


def prepare(task_id: str, workspace: Path) -> None:
    task = ready(task_id)
    src = ROOT / 'assets' / task['asset']
    if workspace.exists():
        raise FileExistsError(f'workspace exists: {workspace}')
    shutil.copytree(src, workspace)


def verify(task_id: str, workspace: Path, round_number: int, record: Path) -> dict:
    task = ready(task_id)
    if round_number not in (1, 2, 3):
        raise ValueError('round must be 1, 2 or 3')
    if record.exists():
        raise FileExistsError(f'record exists: {record}')
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError('workspace must be a directory')
    asset = task['asset']
    baseline = ROOT / 'assets' / asset
    check = ROOT / 'checks' / f'{asset}.py'
    initial = tree(workspace)
    input_hashes = tree(baseline)
    immutable_ok = all((workspace / n).is_file() and digest(workspace / n) == digest(baseline / n)
                       for n in IMMUTABLE[asset])
    output, exit_code = '', None
    status = 'failed'
    if immutable_ok:
        try:
            proc = subprocess.run([sys.executable, '-I', '-B', str(check), str(workspace)],
                                  capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
            output, exit_code = proc.stdout + proc.stderr, proc.returncode
            status = 'passed' if exit_code == 0 else 'failed'
        except subprocess.TimeoutExpired:
            status, output = 'timeout', 'independent check timed out'
    else:
        output = 'immutable input differs or is missing'
    after = tree(workspace)
    if after != initial:
        status = 'failed'
        output += '\nworkspace changed during verification'
    result = {'schema': 1, 'task_id': task_id, 'round': round_number,
              'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'workspace': str(workspace), 'baseline_sha256': input_hashes,
              'checker_sha256': digest(check), 'candidate_sha256': initial,
              'artifact_status': status, 'check_exit_code': exit_code, 'check_output': output,
              'execution_status': 'not_recorded', 'budget_status': 'not_recorded',
              'delivery_status': 'not_proven'}
    record.parent.mkdir(parents=True, exist_ok=True)
    with record.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Prepare and independently check real40 fixtures; never invokes a model.')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('task'); p.add_argument('workspace', type=Path)
    p = sub.add_parser('verify'); p.add_argument('task'); p.add_argument('workspace', type=Path)
    p.add_argument('--round', type=int, required=True); p.add_argument('--record', type=Path, required=True)
    sub.add_parser('status')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare(args.task, args.workspace)
            print(f'Prepared {args.task}: {args.workspace}')
        elif args.command == 'verify':
            result = verify(args.task, args.workspace, args.round, args.record)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['artifact_status'] == 'passed' else 1
        else:
            for t in tasks().values():
                print(f"{t['id']}\t{t['readiness']}\t{t['title']}\t" + ('not_run\t' * 3).rstrip())
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
