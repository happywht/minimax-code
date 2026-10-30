"""In-process, run-bound Codex input requests; answers are never persisted."""
from __future__ import annotations

import asyncio
from typing import Any


class CodexInputRequest:
    def __init__(self, questions: list[dict[str, Any]], future: asyncio.Future[Any],
                 deadline: float, external_id: Any, thread_id: str, blocking: bool,
                 session_id: str, run_id: str) -> None:
        self.questions, self.future, self.deadline = questions, future, deadline
        self.external_id, self.thread_id, self.blocking = external_id, thread_id, blocking
        self.session_id, self.run_id = session_id, run_id
        self.resolved_remotely = False

    def resolve_ask_user(self, request_id: str, answers: Any) -> bool:
        if self.future.done() or asyncio.get_running_loop().time() >= self.deadline:
            return False
        if answers is None:
            self.future.set_result(None)
            return True
        if not isinstance(answers, list) or len(answers) != len(self.questions):
            return False
        result: dict[str, Any] = {}
        for question, answer in zip(self.questions, answers):
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 4000:
                return False
            labels = [option["label"] for option in question["options"]]
            if labels and not question["isOther"] and answer not in labels:
                return False
            result[question["id"]] = {"answers": [answer]}
        self.future.set_result({"answers": result})
        return True

    def close(self, *, remote: bool = False) -> None:
        self.resolved_remotely = self.resolved_remotely or remote
        if not self.future.done():
            self.future.set_result(None)


def parse_questions(params: Any) -> list[dict[str, Any]] | None:
    if not isinstance(params, dict) or type(params.get("isBlocking")) is not bool:
        return None
    if not all(isinstance(params.get(k), str) and params[k] for k in ("threadId", "turnId", "itemId")):
        return None
    raw = params.get("questions")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 10:
        return None
    questions, ids = [], set()
    for q in raw:
        if not isinstance(q, dict) or not all(isinstance(q.get(k), str) and 0 < len(q[k]) <= 4000 for k in ("id", "header", "question")):
            return None
        if q["id"] in ids or any(type(q.get(k, False)) is not bool for k in ("isOther", "isSecret")):
            return None
        ids.add(q["id"])
        options = q.get("options")
        if options is None:
            options = []
        if not isinstance(options, list) or len(options) > 20:
            return None
        if any(not isinstance(o, dict) or not isinstance(o.get("label"), str) or not o["label"]
               or len(o["label"]) > 4000 or not isinstance(o.get("description"), str)
               or len(o["description"]) > 4000 for o in options):
            return None
        if len({o["label"] for o in options}) != len(options):
            return None
        questions.append({"id": q["id"], "header": q["header"], "question": q["question"],
                          "options": options, "multiSelect": False,
                          "isOther": q.get("isOther", False), "isSecret": q.get("isSecret", False)})
    return questions
