"""Cross-ecosystem error type for tool execution (R107).

Fusion of grok-build's ``xai-tool-runtime::error`` — the error type tool
authors code against. Where :mod:`minimax_code.tool_protocol.error_wire`
(``ToolErrorWire``) is the *wire shape* that travels inside JSON-RPC
``error.data``, :class:`ToolError` is the *runtime contract* a tool
returns from its body. The two are bridged by :meth:`ToolError.to_wire`
(the Python equivalent of the crate's ``impl From<ToolError> for
ToolErrorWire``).

``ToolError`` is a struct with a ``kind`` discriminator and a tool-provided
``detail`` string. The ``detail`` is the model-facing message — tools MUST
provide a human-readable explanation of what went wrong, since this text is
sent back to the model to inform its next action. The optional ``source``
field carries a causal chain for developer logs (never sent to the model);
the optional ``details`` field carries structured metadata (JSON-Schema
validation reports, ``retry_after`` hints, ``tool_id``, ``card_id``).

Why this is the first ``tool_runtime`` module
---------------------------------------------

Every other ``xai-tool-runtime`` module references :class:`ToolError` in
its signatures — ``ToolStream`` yields ``Result<TypedToolOutput,
ToolError>``, ``ToolDispatch::call`` returns it, ``terminal_only``
constructs it. The error type is the leaf of the dependency graph, so it
lands first. Its own dependency is just ``ToolErrorWire`` (R83) and
``ToolId`` (R82), both already migrated under :mod:`tool_protocol`.

Kind discriminator
------------------

:class:`ToolErrorKind` is a :class:`enum.StrEnum` whose member *values* are
the snake_case identifiers the Rust ``as_str()`` match returns — the value
IS the metrics/log tag, so ``as_str()`` is just ``self.value`` (single
source of truth). The crate's ``#[derive(Serialize, Deserialize)]`` on
``ToolErrorKind`` uses Rust's default externally-tagged PascalCase variant
names; that serde shape is **not** reproduced here because ``ToolError``
itself never travels on the wire (the bridge to ``ToolErrorWire`` is the
only serialisation path), so the PascalCase form is YAGNI until a consumer
asks for ``ToolError``-level serde.

Wire bridge mapping
-------------------

:meth:`ToolError.to_wire` dispatches on ``kind`` and constructs the
matching ``ToolErrorWire`` variant. Eleven of the nineteen kinds map onto
``ToolErrorWire::Custom`` with the snake_case subcode (the wire-side
recogniser key); the subcode is merged into object-shaped ``details``
under a ``"code"`` key (without clobbering an existing one) so decode-side
classifiers keying on ``details.code`` keep working. The remaining eight
kinds map onto typed wire variants (``ToolNotFound``, ``PermissionDenied``,
``Timeout``, ``Cancelled``, ``InvalidArguments``, ``Execution``,
``BehaviorVersionUnsupported``, ``RenderLimited``, ``TerminalError``),
extracting ``tool_id`` / ``elapsed_ms`` / ``requested`` / ``card_id`` from
``details``.

Not-ToolError-Exception
-----------------------

:class:`ToolError` does **not** subclass :class:`Exception`: the Rust
``ToolError`` is a plain ``struct`` that implements ``std::error::Error``
(it is returned inside ``Result``, not thrown), and the ``source`` field
is an ordinary data field rather than Python's ``__cause__`` chain. Tools
return ``ToolError``; callers that want to throw wrap it. Keeping it a
dataclass avoids the ``source``/``__cause__`` semantic collision and
matches the crate's struct-not-trait-object shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from minimax_code.tool_protocol.error_wire import (
    BehaviorVersionUnsupported as BehaviorVersionUnsupportedWire,
)
from minimax_code.tool_protocol.error_wire import (
    Cancelled as CancelledWire,
)
from minimax_code.tool_protocol.error_wire import (
    Custom as CustomWire,
)
from minimax_code.tool_protocol.error_wire import (
    Execution as ExecutionWire,
)
from minimax_code.tool_protocol.error_wire import (
    InvalidArguments as InvalidArgumentsWire,
)
from minimax_code.tool_protocol.error_wire import (
    PermissionDenied as PermissionDeniedWire,
)
from minimax_code.tool_protocol.error_wire import (
    RenderLimited as RenderLimitedWire,
)
from minimax_code.tool_protocol.error_wire import (
    TerminalError as TerminalErrorWire,
)
from minimax_code.tool_protocol.error_wire import (
    Timeout as TimeoutWire,
)
from minimax_code.tool_protocol.error_wire import (
    ToolErrorWire,
)
from minimax_code.tool_protocol.error_wire import (
    ToolNotFound as ToolNotFoundWire,
)
from minimax_code.tool_protocol.ids import IdError, ToolId

__all__ = [
    "ToolError",
    "ToolErrorKind",
]


# -----------------------------------------------------------------------
# ToolErrorKind — discriminator (StrEnum values = as_str snake_case tags).
# -----------------------------------------------------------------------


class ToolErrorKind(StrEnum):
    """Discriminator for tool errors.

    Member values are the snake_case identifiers the Rust ``as_str()``
    returns — used as the ``Custom`` wire subcode and as the metrics/log
    tag. The nineteen variants mirror ``xai_tool_runtime::ToolErrorKind``
    one-for-one; ``Custom`` is the forward-compat catch-all for kinds not
    yet named (the subcode rides in ``ToolError.details["code"]``).
    """

    #: The tool has no implementation for the requested operation.
    NOT_IMPLEMENTED = "not_implemented"
    #: Inputs failed validation.
    INVALID_ARGUMENTS = "invalid_arguments"
    #: No tool registered under the given id.
    NOT_FOUND = "not_found"
    #: Caller lacks required permissions (403-shaped).
    PERMISSION_DENIED = "permission_denied"
    #: Authentication failed (401-shaped).
    UNAUTHORIZED = "unauthorized"
    #: The tool ran past its time budget.
    TIMEOUT = "timeout"
    #: The caller cancelled the tool call.
    CANCELLED = "cancelled"
    #: Rate limit exceeded.
    RATE_LIMITED = "rate_limited"
    #: The caller's usage pool / billing balance is exhausted (out of
    #: credits). Payment-required-shaped; distinct from :attr:`RATE_LIMITED`
    #: so the surface can show "out of credits" rather than "try again later".
    USAGE_POOL_EXHAUSTED = "usage_pool_exhausted"
    #: The caller hit a usage limit with no balance verdict behind it (the
    #: non-billable allowance ran out). Distinct from
    #: :attr:`USAGE_POOL_EXHAUSTED` (an explicit out-of-balance verdict).
    USAGE_LIMIT_REACHED = "usage_limit_reached"
    #: The billing global rate limiter shed this request (transient load
    #: shed). Distinct from :attr:`RATE_LIMITED` (per-user quota) so the
    #: surface can render a billing-specific "try again later" with a
    #: retry hint (``retry_after_secs`` rides in ``details`` when known).
    GLOBAL_RATE_LIMIT = "global_rate_limit"
    #: The caller hit their per-user concurrency cap. Transient — retry
    #: once one finishes. Distinct from :attr:`GLOBAL_RATE_LIMIT` (shared-
    #: backend load shed) so the surface can tailor a "too many in
    #: progress" message.
    CONCURRENCY_LIMIT = "concurrency_limit"
    #: Upstream service unavailable.
    SERVICE_UNAVAILABLE = "service_unavailable"
    #: Network-level failure.
    NETWORK_ERROR = "network_error"
    #: Tool body returned an error.
    EXECUTION = "execution"
    #: Requested behavior version not supported.
    BEHAVIOR_VERSION_UNSUPPORTED = "behavior_version_unsupported"
    #: Render-card budget exceeded.
    RENDER_LIMITED = "render_limited"
    #: Terminal subprocess failure.
    TERMINAL_ERROR = "terminal_error"
    #: Forward-compat catch-all. The subcode rides in ``details["code"]``.
    CUSTOM = "custom"

    def as_str(self) -> str:
        """Snake-case identifier for metrics / logs.

        Delegates to the StrEnum value (single source of truth) — the value
        IS the tag, so this is just ``self.value``.
        """
        return self.value


# -----------------------------------------------------------------------
# Detail-extraction helpers (read structured fields off `details`).
#
# These mirror the `details_val.and_then(|d| d.get(key)).and_then(...)`
# chains in the Rust `From<ToolError> for ToolErrorWire` impl. Each handles
# `details is None` and `details is not a dict` by returning the default,
# matching serde_json's `Value::Null`-safe accessors.
# -----------------------------------------------------------------------


def _detail_get(details: Any, key: str) -> object | None:
    """Read ``details[key]`` if ``details`` is a dict, else ``None``."""
    if isinstance(details, dict):
        return details.get(key)
    return None


def _tool_id_from_details(details: Any, key: str = "tool_id") -> ToolId:
    """Extract a :class:`ToolId` from ``details[key]``.

    Mirrors ``details_val.and_then(|d| d.get(key)).and_then(|v|
    v.as_str()).and_then(|s| ToolId::new(s).ok()).unwrap_or_else(||
    ToolId::new("unknown").unwrap())``: a missing or malformed value falls
    back to ``ToolId("unknown")`` rather than raising — the wire shape
    requires a concrete id, and "unknown" is the crate's documented default.
    """
    raw = _detail_get(details, key)
    if isinstance(raw, str):
        try:
            return ToolId(raw)
        except IdError:
            pass
    return ToolId("unknown")


def _int_from_details(details: Any, key: str, default: int) -> int:
    """Extract a non-bool int from ``details[key]`` (Rust ``as_u64``)."""
    raw = _detail_get(details, key)
    # ``bool`` is an ``int`` subclass; Rust's ``as_u64`` rejects JSON
    # booleans, so exclude ``bool`` here.
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return default


def _str_from_details(details: Any, key: str, default: str) -> str:
    """Extract a string from ``details[key]`` (Rust ``as_str``)."""
    raw = _detail_get(details, key)
    if isinstance(raw, str):
        return raw
    return default


def _optional_str_from_details(details: Any, key: str) -> str | None:
    """Extract an optional string from ``details[key]`` (``None`` if absent)."""
    raw = _detail_get(details, key)
    if isinstance(raw, str):
        return raw
    return None


def _custom_details_with_code(details: Any, code: str) -> Any:
    """Merge the subcode into object-shaped ``details`` under ``"code"``.

    Carries a :class:`ToolError`'s structured ``details`` onto a
    ``Custom`` wire variant while keeping the round-trip recognisable: the
    decoder replaces the ``{"code": <subcode>}`` object that
    :meth:`ToolError.custom` installs with the wire ``details`` verbatim,
    so the subcode is merged into object-shaped details **without
    clobbering an existing ``code`` key**. Non-dict details pass through
    unchanged.

    Returns a shallow copy so the caller's ``details`` is not mutated
    (Rust takes ownership of the ``Value``; Python mutates a copy).
    """
    if isinstance(details, dict):
        merged = dict(details)
        merged.setdefault("code", code)
        return merged
    return details


# -----------------------------------------------------------------------
# ToolError — the runtime error struct.
# -----------------------------------------------------------------------


@dataclass
class ToolError:
    """Cross-ecosystem error type for tool execution.

    Every error carries:

    * :attr:`kind` — the machine-readable discriminator.
    * :attr:`detail` — the model/user-facing message that tools MUST
      provide (sent back to the model so it can adjust its next action).
    * :attr:`source` — optional causal chain for developer debugging;
      **not** sent to the model.
    * :attr:`details` — optional structured metadata (per-field validation
      errors, ``retry_after`` hints, ``tool_id``, ``card_id``).
    """

    kind: ToolErrorKind
    detail: str
    #: Optional causal chain for developer logs. NOT sent to the model.
    source: BaseException | None = None
    #: Optional structured metadata (JSON-Schema report, retry hints, etc.).
    details: Any = None

    # -- core constructors ------------------------------------------------

    @classmethod
    def new(cls, kind: ToolErrorKind, detail: str) -> ToolError:
        """Core constructor. All other constructors delegate here."""
        return cls(kind=kind, detail=detail)

    def with_details(self, details: Any) -> ToolError:
        """Attach structured metadata (builder; returns self for chaining)."""
        self.details = details
        return self

    def with_source(self, source: BaseException) -> ToolError:
        """Attach a causal error chain for developer logs (not sent to model)."""
        self.source = source
        return self

    # -- one constructor per kind (ergonomic tool code) -------------------

    @classmethod
    def not_implemented(cls, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.NOT_IMPLEMENTED, detail)

    @classmethod
    def invalid_arguments(cls, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.INVALID_ARGUMENTS, detail)

    @classmethod
    def not_found(cls, tool_id: ToolId, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.NOT_FOUND, detail).with_details(
            {"tool_id": str(tool_id)}
        )

    @classmethod
    def permission_denied(cls, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.PERMISSION_DENIED, detail)

    @classmethod
    def unauthorized(cls, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.UNAUTHORIZED, detail)

    @classmethod
    def timeout(cls, tool_id: ToolId, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.TIMEOUT, detail).with_details(
            {"tool_id": str(tool_id)}
        )

    @classmethod
    def cancelled(cls, tool_id: ToolId, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.CANCELLED, detail).with_details(
            {"tool_id": str(tool_id)}
        )

    @classmethod
    def rate_limited(cls, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.RATE_LIMITED, detail)

    @classmethod
    def usage_pool_exhausted(cls, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.USAGE_POOL_EXHAUSTED, detail)

    @classmethod
    def usage_limit_reached(cls, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.USAGE_LIMIT_REACHED, detail)

    @classmethod
    def global_rate_limit(cls, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.GLOBAL_RATE_LIMIT, detail)

    @classmethod
    def concurrency_limit(cls, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.CONCURRENCY_LIMIT, detail)

    @classmethod
    def service_unavailable(cls, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.SERVICE_UNAVAILABLE, detail)

    @classmethod
    def network_error(cls, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.NETWORK_ERROR, detail)

    @classmethod
    def execution(cls, tool_id: ToolId, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.EXECUTION, detail).with_details(
            {"tool_id": str(tool_id)}
        )

    @classmethod
    def terminal_error(cls, tool_id: ToolId, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.TERMINAL_ERROR, detail).with_details(
            {"tool_id": str(tool_id)}
        )

    @classmethod
    def custom(cls, code: str, detail: str) -> ToolError:
        return cls.new(ToolErrorKind.CUSTOM, detail).with_details({"code": code})

    # -- introspection ----------------------------------------------------

    def variant_name(self) -> str:
        """Snake-case identifier for the kind (delegates to :meth:`ToolErrorKind.as_str`)."""
        return self.kind.as_str()

    # -- dunder (Display = detail; Debug = kind + detail + optional) ------

    def __str__(self) -> str:
        return self.detail

    def __repr__(self) -> str:
        parts = [f"kind={self.kind.value}", f"detail={self.detail!r}"]
        if self.source is not None:
            parts.append(f"source={self.source!r}")
        if self.details is not None:
            parts.append(f"details={self.details!r}")
        return f"ToolError({', '.join(parts)})"

    # -- From<serde_json::Error> -----------------------------------------

    @classmethod
    def from_json_error(cls, error: BaseException) -> ToolError:
        """Convert a JSON parse error into an invalid-arguments error.

        Python equivalent of ``impl From<serde_json::Error> for ToolError``:
        ``serde_json::Error`` becomes ``invalid_arguments(value.to_string())``.
        Accepts any :class:`BaseException` (the Python json module raises
        :class:`json.JSONDecodeError`, a :class:`ValueError` subclass).
        """
        return cls.invalid_arguments(str(error))

    # -- Wire bridge: From<ToolError> for ToolErrorWire -------------------

    def to_wire(self) -> ToolErrorWire:
        """Project this error onto its wire form.

        Dispatches on :attr:`kind` and constructs the matching
        :class:`~minimax_code.tool_protocol.error_wire.ToolErrorWire`
        variant. Eleven kinds map onto ``Custom`` with the snake_case
        subcode (merged into object-shaped ``details`` under ``"code"``);
        eight map onto typed variants, extracting ``tool_id`` /
        ``elapsed_ms`` / ``requested`` / ``card_id`` from ``details``.
        """
        kind = self.kind
        detail = self.detail
        details = self.details

        if kind is ToolErrorKind.NOT_IMPLEMENTED:
            return CustomWire(
                subcode="not_implemented",
                message=detail,
                details=_custom_details_with_code(details, "not_implemented"),
            )
        if kind is ToolErrorKind.INVALID_ARGUMENTS:
            return InvalidArgumentsWire(message=detail, details=details)
        if kind is ToolErrorKind.NOT_FOUND:
            return ToolNotFoundWire(tool_id=_tool_id_from_details(details))
        if kind is ToolErrorKind.PERMISSION_DENIED:
            return PermissionDeniedWire(reason=detail)
        if kind is ToolErrorKind.UNAUTHORIZED:
            return CustomWire(
                subcode="unauthorized",
                message=detail,
                details=_custom_details_with_code(details, "unauthorized"),
            )
        if kind is ToolErrorKind.TIMEOUT:
            return TimeoutWire(
                tool_id=_tool_id_from_details(details),
                elapsed_ms=_int_from_details(details, "elapsed_ms", 0),
            )
        if kind is ToolErrorKind.CANCELLED:
            return CancelledWire(tool_id=_tool_id_from_details(details))
        if kind is ToolErrorKind.RATE_LIMITED:
            return CustomWire(
                subcode="rate_limited",
                message=detail,
                details=_custom_details_with_code(details, "rate_limited"),
            )
        if kind is ToolErrorKind.USAGE_POOL_EXHAUSTED:
            return CustomWire(
                subcode="usage_pool_exhausted",
                message=detail,
                details=_custom_details_with_code(details, "usage_pool_exhausted"),
            )
        if kind is ToolErrorKind.USAGE_LIMIT_REACHED:
            return CustomWire(
                subcode="usage_limit_reached",
                message=detail,
                details=_custom_details_with_code(details, "usage_limit_reached"),
            )
        if kind is ToolErrorKind.GLOBAL_RATE_LIMIT:
            return CustomWire(
                subcode="global_rate_limit",
                message=detail,
                details=_custom_details_with_code(details, "global_rate_limit"),
            )
        if kind is ToolErrorKind.CONCURRENCY_LIMIT:
            return CustomWire(
                subcode="concurrency_limit",
                message=detail,
                details=_custom_details_with_code(details, "concurrency_limit"),
            )
        if kind is ToolErrorKind.SERVICE_UNAVAILABLE:
            return CustomWire(
                subcode="service_unavailable",
                message=detail,
                details=_custom_details_with_code(details, "service_unavailable"),
            )
        if kind is ToolErrorKind.NETWORK_ERROR:
            return CustomWire(
                subcode="network_error",
                message=detail,
                details=_custom_details_with_code(details, "network_error"),
            )
        if kind is ToolErrorKind.EXECUTION:
            return ExecutionWire(
                tool_id=_tool_id_from_details(details),
                message=detail,
            )
        if kind is ToolErrorKind.BEHAVIOR_VERSION_UNSUPPORTED:
            return BehaviorVersionUnsupportedWire(
                tool_id=_tool_id_from_details(details),
                requested=_str_from_details(details, "requested", "unknown"),
            )
        if kind is ToolErrorKind.RENDER_LIMITED:
            return RenderLimitedWire(
                tool_id=_tool_id_from_details(details),
                reason=detail,
                card_id=_optional_str_from_details(details, "card_id"),
            )
        if kind is ToolErrorKind.TERMINAL_ERROR:
            return TerminalErrorWire(
                tool_id=_tool_id_from_details(details),
                message=detail,
            )
        if kind is ToolErrorKind.CUSTOM:
            return CustomWire(
                subcode=_str_from_details(details, "code", "custom"),
                message=detail,
                details=details,
            )
        # Exhaustive: the nineteen-variant StrEnum is fully matched above.
        raise AssertionError(f"unhandled ToolErrorKind: {kind!r}")
