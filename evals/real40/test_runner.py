import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('real40_runner', ROOT / 'runner.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_manifest_and_not_ready_gate(self):
        entries = r.tasks()
        self.assertEqual(sum(t['readiness'] == 'ready' for t in entries.values()), 35)
        with self.assertRaises(ValueError):
            r.prepare('remote-01', self.root / 'remote')

    def test_stock_accepts_fix_and_preserves_failure_evidence(self):
        ws = self.root / 'stock'
        r.prepare('code-01', ws)
        failed = r.verify('code-01', ws, 1, self.root / 'failed.json')
        self.assertEqual(failed['artifact_status'], 'failed')
        (ws / 'inventory.py').write_text('CATALOG = {"A-1": (1299, 3), "B-2": (250, 12), "C-3": (4999, 0)}\ndef total_cents(catalog=CATALOG):\n    return sum(price * qty for price, qty in catalog.values())\n', encoding='utf-8')
        result = r.verify('code-01', ws, 2, self.root / 'passed.json')
        self.assertEqual(result['artifact_status'], 'passed')
        self.assertEqual(result['execution_status'], 'not_recorded')
        self.assertEqual(result['delivery_status'], 'not_proven')
        with self.assertRaises(FileExistsError):
            r.verify('code-01', ws, 2, self.root / 'passed.json')
        self.assertEqual(json.loads((self.root / 'failed.json').read_text(encoding='utf-8'))['artifact_status'], 'failed')

    def test_office_verifies_exact_rows_and_unmodified_source(self):
        ws = self.root / 'office'
        r.prepare('office-01', ws)
        (ws / 'summary.csv').write_text('region,paid_orders,total_cents\nEast,1,1800\nNorth,2,12500\nSouth,2,10000\n', encoding='utf-8')
        self.assertEqual(r.verify('office-01', ws, 1, self.root / 'ok.json')['artifact_status'], 'passed')
        (ws / 'sales.csv').write_text('tampered', encoding='utf-8')
        self.assertEqual(r.verify('office-01', ws, 2, self.root / 'bad.json')['artifact_status'], 'failed')

    def test_automation_checks_boundaries_and_disabled_jobs(self):
        ws = self.root / 'auto'
        r.prepare('automation-01', ws)
        (ws / 'scheduler.py').write_text('def due_jobs(jobs, now):\n    return ["a", "d"]\n', encoding='utf-8')
        self.assertEqual(r.verify('automation-01', ws, 1, self.root / 'fake.json')['artifact_status'], 'failed')
        (ws / 'scheduler.py').write_text('from datetime import datetime, timedelta\ndef due_jobs(jobs, now):\n    now = datetime.fromisoformat(now.replace("Z", "+00:00"))\n    return sorted(j["id"] for j in jobs if j["enabled"] and (j["last_run"] is None or now - datetime.fromisoformat(j["last_run"].replace("Z", "+00:00")) >= timedelta(minutes=j["interval_minutes"])))\n', encoding='utf-8')
        self.assertEqual(r.verify('automation-01', ws, 2, self.root / 'good.json')['artifact_status'], 'passed')

    def test_new_code_fixtures_fail_before_and_pass_after_repair(self):
        candidates = {
            'code-02': ('orders.py', 'from discount import rate_percent\ndef create_order(price_cents, quantity):\n    subtotal = price_cents * quantity\n    rate = rate_percent(quantity)\n    discount = subtotal * rate // 100\n    return {"subtotal_cents": subtotal, "discount_cents": discount, "total_cents": subtotal - discount, "rate_percent": rate}\n'),
            'code-05': ('inventory.py', 'def lookup(catalog, sku):\n    if sku < 0: raise ValueError(sku)\n    return catalog[sku]\n'),
            'code-06': ('discount.py', 'def rate_percent(quantity):\n    return 20 if quantity >= 20 else 10 if quantity >= 10 else 0\n'),
            'code-07': ('orders.py', 'def cancel(order):\n    if order["status"] not in ("open", "cancelled"): raise ValueError()\n    return {**order, "status": "cancelled"}\n'),
            'code-10': ('importer.py', 'def parse_quantity(value):\n    value=value.strip()\n    if not value or any(c not in "0123456789" for c in value): raise ValueError()\n    return int(value)\n'),
            'code-12': ('events.py', 'def add_once(events, event):\n    return list(events) if any(e["id"] == event["id"] for e in events) else [*events,event]\n'),
            'code-14': ('redact.py', 'def redact(headers):\n    return {k: "[REDACTED]" if k.lower() in ("authorization", "x-api-key") else v for k,v in headers.items()}\n'),
            'code-18': ('cache.py', 'def get_valid(cache, key, now):\n    item=cache.get(key)\n    return item["value"] if item is not None and now < item["expires_at"] else None\n'),
            'code-09': ('paging.py', 'def page(items, offset, limit):\n    return sorted(items, key=lambda x: (x["created_at"], x["id"]))[offset:offset+limit]\n'),
        }
        for task, (filename, solution) in candidates.items():
            with self.subTest(task=task):
                ws = self.root / task
                r.prepare(task, ws)
                self.assertEqual(r.verify(task, ws, 1, self.root / (task + '-before.json'))['artifact_status'], 'failed')
                (ws / filename).write_text(solution, encoding='utf-8')
                self.assertEqual(r.verify(task, ws, 2, self.root / (task + '-after.json'))['artifact_status'], 'passed')

    def test_remaining_code_fixtures_with_repaired_candidates(self):
        candidates = {
            'code-03': {'test_sales.py': 'import unittest\nimport sales\nclass SalesTest(unittest.TestCase):\n    def test_discount(self):\n        self.assertEqual(sales.discount(15), 10)\n'},
            'code-04': {'money.py':'def round_cents(value): return round(value, 2)\n', 'orders.py':'from money import round_cents\ndef total(value): return round_cents(value)\n', 'discount.py':'from money import round_cents\ndef discount(value): return round_cents(value)\n'},
            'code-08': {'stock.py':'import threading\nclass Stock:\n    def __init__(self, quantity): self.quantity=quantity; self.lock=threading.Lock()\n    def take(self):\n        with self.lock:\n            if self.quantity <= 0: return False\n            self.quantity -= 1\n            return True\n'},
            'code-11': {'dates.py':'from datetime import datetime, timezone\ndef utc_day(value): return datetime.fromisoformat(value).astimezone(timezone.utc).date().isoformat()\n'},
            'code-13': {'batch.py':'def update_all(stock, changes):\n    result=stock.copy()\n    for key, delta in changes.items():\n        if key not in result or result[key]+delta < 0: raise ValueError()\n        result[key] += delta\n    return result\n'},
            'code-15': {'retry.py':'def delays(attempts,base,cap): return [min(base*2**i,cap) for i in range(attempts)]\n'},
            'code-16': {'config.py':'def migrate(config):\n    result=config.copy()\n    if "timeout" in result:\n        old=result.pop("timeout")\n        result.setdefault("timeout_ms",old*1000)\n    return result\n'},
            'code-17': {'paths.py':'from pathlib import Path\ndef safe_path(root, relative):\n    root=Path(root).resolve(); part=Path(relative)\n    if part.is_absolute(): raise ValueError()\n    path=(root / part).resolve()\n    if not path.is_relative_to(root): raise ValueError()\n    return str(path)\n'},
            'code-19': {'api.py':'def get_name(record):\n    return record["display_name"] if "display_name" in record else record["name"]\n'},
            'code-20': {'unique.py':'def unique_ids(items):\n    out=[]; seen=set()\n    for item in items:\n        value=item["id"]\n        if value not in seen: seen.add(value); out.append(value)\n    return out\n'},
        }
        for task, files in candidates.items():
            with self.subTest(task=task):
                ws=self.root / task
                r.prepare(task, ws)
                self.assertEqual(r.verify(task, ws, 1, self.root / (task+'-before.json'))['artifact_status'], 'failed')
                for filename, content in files.items():
                    (ws / filename).write_text(content, encoding='utf-8')
                result=r.verify(task, ws, 2, self.root / (task+'-after.json'))
                self.assertEqual(result['artifact_status'], 'passed', result['check_output'])

    def test_office_documents_require_independent_expected_output(self):
        examples = {
            'office-02': {'claims':[{'claim':'launch_date','value':'2026-02-01','source_ids':['S1','S2']},{'claim':'capacity','value':120,'source_ids':['S1','S3']}]},
            'office-03': {'actions':[{'id':'A1','owner':'李','due':'2026-11-03','action':'修订预算'},{'id':'A2','owner':'陈','due':'2026-11-05','action':'验证报表'}]},
            'office-04': {'differences':[{'id':'B','book_a':250,'book_b':240,'delta':10},{'id':'C','book_a':300,'book_b':None,'delta':None},{'id':'D','book_a':None,'book_b':80,'delta':None}]},
            'office-05': {'added':[{'key':'budget','value':200}],'removed':[{'key':'scope','value':'A'}],'changed':[{'key':'deadline','before':'2026-11-01','after':'2026-11-03'}]},
            'office-06': {'slides':[{'type':'title','title':'季度进展','items':[]},{'type':'metrics','title':'指标','items':['完成: 8/10','延期: 2']},{'type':'actions','title':'下一步','items':['复核延期','确定负责人']}]},
            'office-07': {'series':[{'month':'2026-01','total':10},{'month':'2026-02','total':15},{'month':'2026-03','total':15}],'highest_month':'2026-02'},
            'office-08': {'fields':[{'field':'budget','status':'conflict','values':[100,120],'source_ids':['S1','S2']},{'field':'deadline','status':'consistent','values':['2026-11-01'],'source_ids':['S1','S3']}]},
            'office-09': {'mismatches':[{'key':'orders','source':42,'report':43},{'key':'refunds','source':3,'report':2}]},
            'office-10': 'id,name,email\n1,Alice,alice@example.com\n2,Bob,bob@example.com\n3,陈,c@example.com\n',
        }
        for task, result in examples.items():
            with self.subTest(task=task):
                ws=self.root / task; r.prepare(task,ws)
                self.assertEqual(r.verify(task,ws,1,self.root/(task+'-missing.json'))['artifact_status'],'failed')
                file='cleaned.csv' if task=='office-10' else 'result.json'
                (ws/file).write_text(result if isinstance(result,str) else json.dumps(result,ensure_ascii=False), encoding='utf-8')
                self.assertEqual(r.verify(task,ws,2,self.root/(task+'-good.json'))['artifact_status'],'passed')
                (ws/file).write_text('{}',encoding='utf-8')
                self.assertEqual(r.verify(task,ws,3,self.root/(task+'-wrong.json'))['artifact_status'],'failed')

    def test_automation_fixtures_reject_initial_and_accept_valid_logic(self):
        cases={
            'automation-02': 'def disable(jobs, job_id):\n    if not any(j["id"] == job_id for j in jobs): raise KeyError(job_id)\n    return [{**j,"enabled":False} if j["id"] == job_id else j.copy() for j in jobs]\n',
            'automation-03': 'def decide(attempts,max_attempts,completed_keys,key):\n    if key in completed_keys: return "skip"\n    return "retry" if attempts < max_attempts else "stop"\n',
            'automation-04': 'def can_run(job,approval):\n    return bool(job["enabled"] and (not job["requires_approval"] or approval == "approved"))\n',
            'automation-05': 'def alerts(events):\n    latest={}\n    for e in events: latest[e["id"]]=e["status"]\n    return sorted(k for k,v in latest.items() if v in ("start","failed"))\n',
        }
        for task,solution in cases.items():
            with self.subTest(task=task):
                ws=self.root / task; r.prepare(task,ws)
                self.assertEqual(r.verify(task,ws,1,self.root/(task+'-bad.json'))['artifact_status'],'failed')
                (ws/'automation.py').write_text(solution,encoding='utf-8')
                result=r.verify(task,ws,2,self.root/(task+'-good.json'))
                self.assertEqual(result['artifact_status'],'passed',result['check_output'])

    def test_existing_workspace_and_round_validation(self):
        ws = self.root / 'stock'
        r.prepare('code-01', ws)
        with self.assertRaises(FileExistsError):
            r.prepare('code-01', ws)
        with self.assertRaises(ValueError):
            r.verify('code-01', ws, 4, self.root / 'never.json')
        self.assertFalse((self.root / 'never.json').exists())


if __name__ == '__main__':
    unittest.main()
