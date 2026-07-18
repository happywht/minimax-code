"""``RetryPolicy`` — maps an HTTP status to a :class:`Disposition` (R17).

Port of grok-build ``xai-circuit-breaker``'s ``retry_policy.rs``. Consolidates
the "what should I do with this response" decision so callers don't each
re-implement status-code tables. Two presets: ``server`` (429 + 5xx retry,
rest terminal) and ``client_storage`` (400/403/404 terminal-drop, 401
auth-refresh, rest retry).

Pure data: the breaker itself is transport-agnostic; this module gives
HTTP-flavoured callers a ready-made classifier so they can decide retry vs.
record-as-failure consistently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Disposition(StrEnum):
    """What a caller should do with a non-2xx HTTP response."""

    RETRYABLE = "retryable"
    AUTH_REFRESH = "auth_refresh"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class RetryPolicy:
    """Status-code classifier.

    ``classify`` returns ``None`` for 2xx (success, not an error). Otherwise
    auth_refresh > terminal > retryable > default, in that priority order.
    """

    retryable: frozenset[int] = field(default_factory=frozenset)
    auth_refresh: frozenset[int] = field(default_factory=frozenset)
    terminal: frozenset[int] = field(default_factory=frozenset)
    default: Disposition = Disposition.TERMINAL

    @classmethod
    def server(cls) -> RetryPolicy:
        """Server preset: 429 and any 5xx are retryable, everything else terminal."""
        return cls(retryable=frozenset({429}), default=Disposition.TERMINAL)

    @classmethod
    def client_storage(cls) -> RetryPolicy:
        """Client storage/upload preset: 400/403/404 terminal-drop, 401
        auth-refresh-once, everything else retried."""
        return cls(
            auth_refresh=frozenset({401}),
            terminal=frozenset({400, 403, 404}),
            default=Disposition.RETRYABLE,
        )

    def classify(self, status: int) -> Disposition | None:
        """Return the disposition for ``status``, or ``None`` for 2xx success."""
        if 200 <= status < 300:
            return None
        if status in self.auth_refresh:
            return Disposition.AUTH_REFRESH
        if status in self.terminal:
            return Disposition.TERMINAL
        if status in self.retryable or 500 <= status < 600:
            return Disposition.RETRYABLE
        return self.default

    def should_retry(self, status: int) -> bool:
        """``True`` iff ``status`` classifies as :attr:`Disposition.RETRYABLE`."""
        return self.classify(status) == Disposition.RETRYABLE


__all__ = ["Disposition", "RetryPolicy"]
