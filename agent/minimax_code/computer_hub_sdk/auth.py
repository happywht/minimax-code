"""Auth credentials + pool-dedup principal keys (R139).

Fusion of grok-build's ``xai-computer-hub-sdk/src/auth.rs`` (238 lines). This
is the SDK crate's 7th leaf (after R133 error / R134 handshake / R135 refcount
/ R136 donate_pump / R137 trace_donate / R138 connection_borrow): the
credential the client attaches at WebSocket upgrade time, plus its stable
hashable projection used as the pool-dedup key.

Nearly the entire file ports cleanly -- it is the most self-contained SDK leaf
so far. ``AuthCredential`` (Bearer/Headers), ``PrincipalKey``, ``AuthIdentity``
and ``AuthProvider`` are pure data + protocol types with no tokio / pool /
connection dependency. The only Rust framework type is ``http::HeaderName``;
its validation (reject anything outside the RFC 7230 token charset, e.g. a
newline injection attempt) is reproduced here by a regex so an invalid name
still surfaces as :class:`~minimax_code.computer_hub_sdk.error.InvalidConfig`
at construction rather than being silently dropped at upgrade time.

Security contract
-----------------

Both variants carry secret material (the bearer token; the header values). The
Rust ``Debug`` impls use ``finish_non_exhaustive`` to never log the secret; the
Python ``__repr__`` overrides do the same. :class:`BearerCredential` and
:class:`PrincipalKey` are declared ``repr=False`` on the dataclass so the
auto-generated ``__repr__`` (which would embed the secret field verbatim) is
NOT used; the hand-written ``__repr__`` surfaces only the variant / a count.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from minimax_code.computer_hub_sdk.error import InvalidConfig

__all__ = [
    "AuthCredential",
    "BearerCredential",
    "HeadersCredential",
    "PrincipalKey",
    "AuthIdentity",
    "AuthProvider",
]

# RFC 7230 token = 1*tchar; tchar = "!#$%&'*+-.^_`|~" / DIGIT / ALPHA.
# Mirrors http::HeaderName::from_bytes: an invalid name (e.g. one containing
# a newline injection attempt) is rejected at construction rather than
# silently dropped at upgrade time.
_HEADER_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# The Authorization header name. http::header::AUTHORIZATION stores it
# lowercased; the Bearer upgrade_headers() path emits this canonical form.
_AUTHORIZATION = "authorization"


@dataclass(frozen=True, repr=False)
class PrincipalKey:
    """Stable hashable projection of an AuthCredential (R139).

    The pool keys connections by ``(url, principal_key)`` so two ToolServer
    builds with the same credential reuse one socket while distinct
    credentials open distinct sockets. ``fingerprint`` includes the secret
    material BY DESIGN (see the Rust module docstring's "Pool dedup and
    credential refresh" note: distinct secrets imply distinct credentials).

    ``repr=False`` + a hand-written ``__repr__`` so the fingerprint (which
    contains the secret) is never logged -- mirrors Rust ``Debug``
    ``finish_non_exhaustive``. ``__eq__`` / ``__hash__`` still key on the
    fingerprint (Rust derives ``PartialEq`` + ``Hash``).
    """

    fingerprint: str

    def __repr__(self) -> str:
        return "PrincipalKey(<redacted>)"


@dataclass(frozen=True)
class AuthIdentity:
    """Owner identity surfaced by an AuthProvider alongside its credential (R139).

    Mirrors the OAuth principal fields the provider parsed from its auth source.
    Kept separate from AuthCredential on purpose: identity does NOT participate
    in pool-dedup hashing (that keys only on the secret). No secret material,
    so the auto-generated ``__repr__`` is safe (Rust derives ``Debug``).
    """

    user_id: str
    principal_type: str | None = None
    principal_id: str | None = None


class AuthCredential(ABC):
    """Credential carried into the WebSocket upgrade (R139).

    Two concrete variants: :class:`BearerCredential` (``Authorization: Bearer
    ...``) and :class:`HeadersCredential` (a pre-built, canonically-ordered
    header bundle). Construct via the :meth:`bearer` / :meth:`headers`
    classmethods (mirrors the Rust ``AuthCredential::bearer`` /
    ``AuthCredential::headers`` associated functions).

    Satisfies :class:`AuthProvider` by default: ``current()`` returns self,
    ``identity()`` returns ``None`` (mirrors ``impl AuthProvider for
    AuthCredential``). Subclasses implement :meth:`principal_key` and
    :meth:`upgrade_headers`.

    ``__repr__`` is abstract here and overridden redacted in every subclass so
    secret material is never logged (Rust ``Debug`` ``finish_non_exhaustive``).
    """

    @property
    @abstractmethod
    def kind(self) -> str:
        """Variant tag (``"bearer"`` / ``"headers"``); used by ``__repr__``."""

    @abstractmethod
    def principal_key(self) -> PrincipalKey:
        """Stable hashable projection used as the pool dedup key."""

    @abstractmethod
    def upgrade_headers(self) -> list[tuple[str, str]]:
        """Headers to attach to the WebSocket upgrade request."""

    @abstractmethod
    def __repr__(self) -> str:
        """Redacted repr -- MUST NOT surface secret material."""

    # -- AuthProvider satisfaction (impl AuthProvider for AuthCredential) ------
    def current(self) -> AuthCredential:
        """A bare credential is its own current value (AuthProvider default)."""
        return self

    def identity(self) -> AuthIdentity | None:
        """A bare credential carries no OAuth identity (AuthProvider default)."""
        return None

    # -- Variant constructors (Rust associated functions) --------------------
    @classmethod
    def bearer(cls, token: str) -> BearerCredential:
        """Construct a bearer-token credential (``Authorization: Bearer {token}``)."""
        return BearerCredential(token=token)

    @classmethod
    def headers(cls, items: Iterable[tuple[str, str]]) -> HeadersCredential:
        """Construct a pre-built header bundle, validating each name (R139).

        Names are canonicalised to lowercase and validated against the RFC 7230
        token charset at construction so an invalid name (e.g. one containing a
        newline injection attempt) raises :class:`InvalidConfig` rather than
        being silently filtered out at upgrade time. Entries are sorted by name
        for an order-independent fingerprint (mirrors Rust ``BTreeMap``).
        """
        canonical: list[tuple[str, str]] = []
        for raw_name, raw_value in items:
            name = raw_name.lower()
            if not _HEADER_TOKEN_RE.match(name):
                raise InvalidConfig(f"invalid header name {name!r}")
            canonical.append((name, raw_value))
        canonical.sort()
        return HeadersCredential(headers=tuple(canonical))


@dataclass(frozen=True, repr=False)
class BearerCredential(AuthCredential):
    """Bearer token attached as the ``Authorization: Bearer ...`` header (R139)."""

    token: str

    @property
    def kind(self) -> str:
        return "bearer"

    def principal_key(self) -> PrincipalKey:
        return PrincipalKey(fingerprint=f"bearer:{self.token}")

    def upgrade_headers(self) -> list[tuple[str, str]]:
        return [(_AUTHORIZATION, f"Bearer {self.token}")]

    def __repr__(self) -> str:
        return "AuthCredential::Bearer(<redacted>)"


@dataclass(frozen=True, repr=False)
class HeadersCredential(AuthCredential):
    """Pre-built header bundle (e.g. signed identity headers from an upstream proxy).

    ``headers`` is canonically ordered (sorted by name at construction) so the
    fingerprint is order-independent. Names are validated + lowercased at
    construction; ``upgrade_headers`` returns them verbatim.
    """

    headers: tuple[tuple[str, str], ...]

    @property
    def kind(self) -> str:
        return "headers"

    def principal_key(self) -> PrincipalKey:
        # Concatenate canonicalised name=value pairs (already sorted) so the
        # fingerprint is order-independent -- mirrors the Rust BTreeMap walk.
        joined = "".join(f"{name}={value}\n" for name, value in self.headers)
        return PrincipalKey(fingerprint=f"headers:{joined}")

    def upgrade_headers(self) -> list[tuple[str, str]]:
        return list(self.headers)

    def __repr__(self) -> str:
        return f"AuthCredential::Headers(header_count={len(self.headers)})"


@runtime_checkable
class AuthProvider(Protocol):
    """Credential provider called on every connect/reconnect (R139).

    Rust trait with two default impls (``principal_key`` -> current credential
    key; ``identity`` -> ``None``). A Python :class:`Protocol` carries no
    default body, so the contract is: a provider that re-mints a rotating
    secret on every :meth:`current` call (e.g. a refresh-before-use bearer)
    MUST override :meth:`principal_key` to key only on stable identity,
    otherwise each rotation fragments the connection pool. A bare
    :class:`AuthCredential` satisfies this protocol via its default
    ``current`` / ``identity`` / ``principal_key``.
    """

    def current(self) -> AuthCredential:
        """The credential to attach to this connect/reconnect attempt."""
        ...

    def principal_key(self) -> PrincipalKey:
        """Stable pool-dedup key, decoupled from the per-connect credential."""
        ...

    def identity(self) -> AuthIdentity | None:
        """Owner identity behind the credential, when the provider can surface it."""
        ...
