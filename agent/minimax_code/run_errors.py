"""Failures that must stop a durable run instead of being treated as UI errors."""

from __future__ import annotations


class RunPersistenceError(RuntimeError):
    """Required history or execution evidence could not be read or saved.

    Side effects may already exist. Stop, retain the run for review, and never
    turn this into a retryable tool result or a successful model completion.
    """
