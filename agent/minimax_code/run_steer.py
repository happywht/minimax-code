"""Durable Native-only interjections, consumed between tool batches."""
from __future__ import annotations

import asyncio
import hashlib

from .agent.interjection import format_interjection


def step_id(run_id: str, key: str) -> str:
    return "steer_" + hashlib.sha256((run_id + "\0" + key).encode()).hexdigest()


class SteerController:
    def __init__(self, job, emit):
        self.job, self.emit = job, emit
        self.lock = asyncio.Lock()
        self.open = True
        self.pending = []

    async def accept(self, key: str, text: str):
        async with self.lock:
            ident = step_id(self.job.run_id, key)
            prior = await self.job.dao.get_step(ident)
            if prior:
                if (prior.get("payload") or {}).get("text") != text:
                    raise ValueError("Idempotency key belongs to different input")
                return prior
            if not self.open or self.job.cancel_requested:
                raise ValueError("This run no longer accepts additional input")
            if len(self.pending) >= 10:
                raise ValueError("Too many pending inputs for this run")
            step = await self.job.dao.create_step(id=ident, run_id=self.job.run_id,
                session_id=self.job.session_id, kind="observation", status="pending",
                title="运行中补充", summary="已接收，等待工具批次结束",
                payload={"source": "native_steer", "text": text})
            self.pending.append(step)
            await self.emit("run.step.started", {"run_id": self.job.run_id, "step": step})
            return step

    async def drain(self, messages, persist):
        async with self.lock:
            while self.pending and self.open and not self.job.cancel_requested:
                step = self.pending[0]
                message = {"role": "user", "content": format_interjection(step["payload"]["text"]),
                           "metadata": {"native_steer_step_id": step["id"]}}
                messages.append(message)
                await persist(self.job.session_id, message)
                updated = await self.job.dao.complete_step(step["id"], status="completed",
                    summary="已加入后续模型上下文；不保证模型已处理")
                self.pending.pop(0)
                await self.emit("run.step.completed", {"run_id": self.job.run_id, "step": updated})

    async def close(self):
        async with self.lock:
            self.open = False
            while self.pending:
                step = self.pending.pop(0)
                updated = await self.job.dao.complete_step(step["id"], status="failed",
                    summary="任务结束，未加入模型上下文", error="任务结束前没有可用的工具批次边界")
                await self.emit("run.step.completed", {"run_id": self.job.run_id, "step": updated})
