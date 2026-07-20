"""Outbound auth visibility seam (R187).

Fusion of grok-build's ``xai-grok-auth/src/visibility.rs`` (7 lines) -- the
``xai-grok-auth`` crate's 1st leaf. :class:`HttpAuth` is the dependency-
inversion seam between ``xai-file-utils`` (the holder, which builds outbound
requests) and ``xai-grok-shell`` (the implementer, which owns credential
construction): the holder calls :meth:`HttpAuth.apply` to stamp auth headers
without reaching back into shell types.

This is the narrowest auth surface -- data-collector code holds an
:class:`HttpAuth` and never needs the wider
:class:`~minimax_code.grok_auth.auth_provider.AuthCredentialProvider`
contract. The wider contract (refresh-aware snapshot + 401 recovery) lands
alongside it in :mod:`minimax_code.grok_auth.auth_provider`.

Layer separation vs the SDK auth crate
--------------------------------------

:mod:`minimax_code.computer_hub_sdk.auth` (R139) handles **connection-pool
level** credentials: the bearer / headers attached at WebSocket upgrade time,
plus the principal-key projection used to dedup pool entries (connection
lifetime). :class:`HttpAuth` here is **request level**: it stamps auth on
each outbound HTTP request the data-collector issues (request lifetime). The
two are orthogonal -- one owns the socket's identity, the other owns the
request's headers.

Python-specific adaptations (no behavior change)
------------------------------------------------

* Rust ``pub trait HttpAuth: Send + Sync`` -> :class:`HttpAuth` as an
  :mod:`abc.ABC` with one :func:`abc.abstractmethod`. ``Send + Sync`` is
  omitted (asyncio is single-threaded under the GIL; there is no
  Rust-style thread-safety vocabulary to express).
* Rust ``reqwest::RequestBuilder`` -> :class:`httpx.Request`. The platform
  uses httpx, not reqwest; the trait's intent is header mutation, and
  ``httpx.Request.headers`` is a mutable mapping, so :meth:`apply` mutates
  in place and returns the same request (mirrors the Rust builder-chaining
  return). The annotation is kept under ``TYPE_CHECKING`` so the abstract
  seam does not gain a runtime dependency on httpx -- concrete
  implementations inject whatever request type they drive, as long as it
  exposes a ``headers`` mapping.
* Rust ``&self`` -> ``self``; ``&str`` -> ``str``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

__all__ = ["HttpAuth"]


class HttpAuth(ABC):
    """Apply auth headers to outbound requests (R187).

    Dependency-inversion seam: the holder (data-collector / file-utils)
    builds the request; the implementer (shell) owns credential
    construction and stamps the headers here. This keeps shell types out
    of the holder's import graph.

    Concrete implementers include ``xai-grok-shell``'s
    ``GrokAuthCredentials`` and the static wrapper
    :class:`~minimax_code.grok_auth.auth_provider.StaticAuthCredentialProvider`.
    """

    @abstractmethod
    def apply(self, request: httpx.Request, base_url: str) -> httpx.Request:
        """Stamp auth headers onto ``request`` and return it.

        Implementations mutate ``request.headers`` in place and return the
        same request object (mirrors Rust's ``RequestBuilder`` chaining).
        ``base_url`` lets URL-scoped auth decisions vary the header set.

        Args:
            request: The outbound HTTP request; its ``headers`` mapping is
                mutated in place.
            base_url: The request's base URL, for URL-scoped auth policy.

        Returns:
            The same ``request`` (mutated), for chaining.
        """
