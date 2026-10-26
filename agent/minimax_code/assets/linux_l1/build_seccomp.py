"""Reproduce the reviewed policy from the preserved baseline bytes."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

TRANSFORM_VERSION = 1
BASELINE_SHA256 = "cc374cf23846ce1f62f4dc807a8e2b8673c783c6f56cb475467621035d281e6c"
POLICY_SHA256 = "41353e75c367b32482d0367ba5632ea6f27fba0b390b68418401549ab8cd545f"
DENIED = {"sched_setscheduler", "sched_setattr", "sched_setparam", "sched_setaffinity",
    "setpriority", "ioprio_set", "ptrace", "process_vm_readv", "process_vm_writev",
    "pidfd_getfd", "process_mrelease", "perf_event_open", "kcmp"}


def build(raw: bytes) -> bytes:
    if hashlib.sha256(raw).hexdigest() != BASELINE_SHA256:
        raise ValueError("Frozen seccomp baseline identity differs")
    policy = copy.deepcopy(json.loads(raw))
    if policy["defaultAction"] != "SCMP_ACT_ERRNO":
        raise ValueError("Frozen seccomp baseline is not default deny")
    rules, prlimit = [], []
    for rule in policy["syscalls"]:
        if "prlimit64" in rule["names"] and rule["action"] == "SCMP_ACT_ALLOW":
            own = copy.deepcopy(rule)
            if own.get("args"):
                raise ValueError("Review changed prlimit baseline conditions")
            own["names"] = ["prlimit64"]
            own["args"] = [{"index": 0, "value": 0, "op": "SCMP_CMP_EQ"}]
            prlimit.append(own)
        rule["names"] = [name for name in rule["names"] if name not in DENIED | {"prlimit64"}]
        if rule["names"]:
            rules.append(rule)
    if len(prlimit) != 1:
        raise ValueError("Review changed prlimit baseline rule count")
    rules.extend(prlimit)
    rules.append({"names": sorted(DENIED), "action": "SCMP_ACT_ERRNO", "errnoRet": 1})
    rules.append({"names": ["prlimit64"], "action": "SCMP_ACT_ERRNO", "errnoRet": 1,
        "args": [{"index": 0, "value": 0, "op": "SCMP_CMP_NE"}]})
    policy["syscalls"] = rules
    encoded = (json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if hashlib.sha256(encoded).hexdigest() != POLICY_SHA256:
        raise ValueError("Derived seccomp policy identity differs")
    return encoded


if __name__ == "__main__":
    directory = Path(__file__).resolve().parent
    output = build((directory / "seccomp-baseline-0.57.4.json").read_bytes())
    (directory / "seccomp-pid1-v1.json").write_bytes(output)
