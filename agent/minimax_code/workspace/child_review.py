"""Bounded review of a finished child against the current parent workspace."""
from __future__ import annotations

import difflib
import hashlib
import json
import stat

from .child import ChildWorkspace, _digest, _path, _scan, collection_history


def preview(child: ChildWorkspace) -> dict:
    if child.record.get("restricted"):
        raise ValueError("Restricted child workspaces require their own collection path")
    if child.record["state"] not in {"completed", "failed", "cancelled", "collected"}:
        raise ValueError("Child workspace is active or needs manual review")
    final = _scan(child.root)
    if final != child.record.get("final"):
        raise ValueError("Child files changed after execution; preserve them for manual review")
    modes = child.record.get("final_owner_executable", {})
    if any(bool(_path(child.root, path).lstat().st_mode & stat.S_IXUSR) != executable
           for path, executable in modes.items()):
        raise ValueError("Child file permissions changed after execution; preserve them for manual review")
    baseline = child.record["baseline"]
    paths = sorted(p for p in baseline.keys() | final.keys() if baseline.get(p) != final.get(p))
    if len(paths) > 500:
        raise ValueError("More than 500 changed files; review and collect manually")
    files = []
    remaining = 65536
    merge_child_remaining = 65536
    for path in paths:
        parent_path, child_path = _path(child.parent, path), _path(child.root, path)
        current = _digest(parent_path)
        after = final.get(path)
        resolution = child.record.get("resolutions", {}).get(path, {})
        resolved = (current is not None and resolution.get("parent_sha256") == current
                    and resolution.get("child_sha256") == after)
        conflict = not resolved and current != after and (current != baseline.get(path) or after is None)
        reason = ("deletion_requires_review" if current == baseline.get(path) and after is None
                  else "parent_changed" if conflict else None)
        entry = {"path": path, "baseline_sha256": baseline.get(path),
                 "parent_sha256": current, "child_sha256": after, "conflict": reason,
                 "resolved": resolved}
        if path in modes:
            entry["owner_executable"] = modes[path]
        # Only small UTF-8 files are rendered, and the diff budget is shared.
        try:
            if remaining and all(not f.exists() or f.stat().st_size <= 16384 for f in (parent_path, child_path)):
                def read_verified(file, digest):
                    if digest is None:
                        return ""
                    with file.open("rb") as stream:
                        data = stream.read(16385)
                    if len(data) > 16384 or hashlib.sha256(data).hexdigest() != digest:
                        raise ValueError("File changed during review; refresh before collection")
                    return data.decode("utf-8")

                before = read_verified(parent_path, current)
                after_text = read_verified(child_path, after)
                if reason == "parent_changed" and current is not None and after is not None:
                    entry["merge_parent_text"] = before
                    child_bytes = len(after_text.encode("utf-8"))
                    if child_bytes <= merge_child_remaining:
                        entry["merge_child_text"] = after_text
                        merge_child_remaining -= child_bytes
                diff = "".join(difflib.unified_diff(before.splitlines(True), after_text.splitlines(True),
                                                    fromfile="current parent", tofile="child result"))
                entry["diff"] = diff[:remaining]
                entry["diff_truncated"] = len(diff) > remaining
                remaining -= len(entry["diff"])
            else:
                entry["diff_unavailable"] = True
        except (UnicodeError, OSError):
            entry["diff_unavailable"] = True
        files.append(entry)
    identity = child.parent.stat()
    revision = hashlib.sha256(json.dumps({"run_id": child.run_id, "state": child.record["state"],
        "root": str(child.parent), "device": identity.st_dev, "inode": identity.st_ino,
        "files": [{k: v for k, v in f.items() if k not in {"diff", "diff_truncated", "diff_unavailable", "merge_parent_text", "merge_child_text"}}
                  for f in files]}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"run_id": child.run_id, "root": str(child.parent), "kind": child.record["kind"],
            "state": child.record["state"], "files": files, "revision": revision,
            "parent_identity": [identity.st_dev, identity.st_ino],
            "omitted": child.record.get("omitted", []),
            "collection_attempts": collection_history(child),
            **({"receipt": child.record["receipt"]} if child.record["state"] == "collected" else {})}
