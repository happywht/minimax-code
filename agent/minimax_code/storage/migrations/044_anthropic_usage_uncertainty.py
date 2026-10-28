"""Old cumulative stream totals lack proof that late input/cache updates were seen.

This is uncertainty, not proof that every historical count (including zero) is
wrong. Preserve the observations as lower bounds and reuse unresolved-attempt
review. The service lease must exclude old writers before startup migration.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

VERSION = 44
REASON = "migration 044: historical cumulative stream usage coverage unverified"


def _object(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _matches(snapshot, row, provider_key="provider_id"):
    return (isinstance(snapshot, dict) and bool(row["provider"]) and bool(row["model"])
            and snapshot.get(provider_key) == row["provider"]
            and snapshot.get("model") == row["model"])


def _classification(row):
    # An editable providers row cannot establish the protocol of a past call.
    for field in ("owner_metadata", "root_metadata"):
        metadata = _object(row[field])
        if (row["purpose"] == "codex_external_turn" and metadata.get("engine") == "codex"
                and _matches(metadata.get("codex_settings"), row, "modelProvider")):
            return "codex"
        snapshot = metadata.get("model_snapshot")
        if _matches(snapshot, row) and snapshot.get("protocol") in {"anthropic", "openai"}:
            return snapshot["protocol"]
    # Missing/mismatched/malformed historical routing is not a clean exemption.
    return "unclassified_protocol"


def run(conn):
    cursor = conn.execute(
        "SELECT a.*,r.id AS owner_id,r.metadata AS owner_metadata,"
        "root.id AS root_id,root.metadata AS root_metadata "
        "FROM budget_attempts a JOIN budget_families f ON f.id=a.family_id "
        "LEFT JOIN agent_runs r ON r.id=a.run_id "
        "LEFT JOIN agent_runs root ON root.id=f.root_run_id "
        "WHERE a.state='settled' AND a.provenance='provider' ORDER BY a.rowid"
    )
    columns = [column[0] for column in cursor.description]
    for values in cursor.fetchall():
        row = dict(zip(columns, values, strict=True))
        classification = _classification(row)
        if classification in {"openai", "codex"}:
            continue
        owner = row["owner_id"] or row["root_id"]
        if owner is None:
            raise RuntimeError("Historical usage uncertainty has no durable run audit owner")
        reason = f"{row['reason']}; {REASON}" if row["reason"] else REASON
        conn.execute("UPDATE budget_attempts SET state='unknown',reason=? WHERE id=?",
                     (reason, row["id"]))
        # Do not overwrite old normalized complete flags or receipt verdicts:
        # they are historical evidence; current accounting certainty is state.
        data = {"attempt_id": row["id"], "attempt_run_id": row["run_id"],
                "state": "unknown", "previous_state": "settled", "migration": VERSION,
                "classification": classification, "reason": REASON,
                "known_lower_bound": row["known_tokens"]}
        conn.execute(
            "INSERT INTO run_events(id,run_id,seq,type,created_at,data) "
            "SELECT ?,?,COALESCE(MAX(seq),0)+1,'budget',?,? FROM run_events WHERE run_id=?",
            (f"evt_{uuid.uuid4().hex}", owner, datetime.now(UTC).isoformat(),
             json.dumps(data, ensure_ascii=False), owner),
        )
