"""Opt-in recursive resource ownership for admitted execution profiles."""

from contextvars import ContextVar

owned_runtime: ContextVar[dict | None] = ContextVar("owned_runtime", default=None)
