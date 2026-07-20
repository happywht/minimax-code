"""Client-agnostic auth-stamping + 401 retry orchestrator (R188).

Fusion of grok-build's ``xai-grok-auth/src/retry_middleware.rs`` (272 lines) --
the ``xai-grok-auth`` crate's 3rd and final leaf, gated behind the
``middleware`` cargo feature in Rust. :class:`AuthRetryMiddleware` wraps an
:class:`AuthCredentialProvider` and stamps the bearer onto every outbound
request, retrying with a refreshed token when the upstream returns 401.

Mapping reqwest-middleware to the platform
------------------------------------------

Rust's ``reqwest_middleware::Middleware`` is an http pipeline trait: each
outbound request flows through ``handle(req, extensions, next)`` where
``next.run(req)`` dispatches to the next layer (eventually the wire). The
platform uses httpx, which has no 1:1 middleware trait:

* httpx ``event_hooks`` fire on request/response but cannot trigger a resend
  (the response-hook return value is ignored), so the 401-retry path cannot
  be expressed there.
* A full ``httpx.AsyncBaseTransport`` subclass would bind the retry logic to
  one client's transport plumbing -- a heavier commitment than this
  feature-gated leaf warrants.

Instead this class exposes a **client-agnostic retry orchestrator**: the
caller injects the ``send`` callable (the ``Next`` in reqwest-middleware
terms). This keeps the crate's dependency-inversion philosophy intact -- the
middleware depends on the trait + a send seam, never on a concrete client
(mirrors R187's HttpAuth TYPE_CHECKING discipline). An httpx caller wires it
as ``await middleware.execute(request, client.send)``.

Request reuse
-------------

Rust clones the request (``req.try_clone()``) before sending because
``reqwest`` consumes the request body. httpx does not: ``AsyncClient.send``
reads ``request.content`` (bytes) and leaves the request reusable, so this
class re-stamps the ``Authorization`` header on the same request object
across retries. Callers must pass a request with a non-streaming body (bytes
content) for the retry path to work -- the same constraint as Rust's
``try_clone`` returning ``None`` on streaming bodies (which Rust handles by
giving up on the retry).

Python-specific adaptations (no behavior change)
-------------------------------------------------

* Rust ``Arc<dyn AuthCredentialProvider>`` -> a bare
  :class:`AuthCredentialProvider` reference (Python GC owns the lifetime; no
  ``Arc`` needed for shared ownership in a single-threaded asyncio loop).
* Rust ``u32`` -> ``int``.
* ``#[async_trait] impl Middleware`` with ``handle(req, ext, next)`` ->
  ``async def execute(request, send)`` (the Middleware handle method
  desugared; ``Next<'_>`` becomes the ``send`` callable parameter).
* ``tracing::warn!`` on header-build failure is unreachable in Python
  (``f"Bearer {token}"`` always yields a valid str; httpx accepts any str
  header value), so the branch is omitted.
* ``reqwest::StatusCode::UNAUTHORIZED`` -> the literal ``401``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

from minimax_code.grok_auth.auth_provider import AuthCredentialProvider

__all__ = ["AuthRetryMiddleware"]

_UNAUTHORIZED = 401


class AuthRetryMiddleware:
    """Stamp auth headers + retry on 401 with a refreshed token (R188).

    Wraps an :class:`AuthCredentialProvider`: every outbound request gets the
    bearer stamped onto its ``Authorization`` header, and a 401 response
    triggers up to ``max_retries`` refresh-and-retry attempts (each re-stamps
    the header with the post-refresh token before re-sending).

    The ``send`` callable injected into :meth:`execute` is the dispatch seam
    (reqwest-middleware's ``Next``): typically ``client.send`` for an
    ``httpx.AsyncClient``. Keeping it a parameter rather than binding to a
    concrete transport keeps the retry logic client-agnostic.
    """

    def __init__(self, credentials: AuthCredentialProvider, max_retries: int) -> None:
        self._credentials = credentials
        self._max_retries = max_retries

    @staticmethod
    def _apply_auth_header(request: httpx.Request, token: str) -> None:
        """Stamp ``Authorization: Bearer {token}`` onto ``request`` in place."""
        request.headers["authorization"] = f"Bearer {token}"

    async def execute(
        self,
        request: httpx.Request,
        send: Callable[[httpx.Request], Awaitable[httpx.Response]],
    ) -> httpx.Response:
        """Send ``request`` through ``send``, retrying on 401 up to ``max_retries``.

        Stamps the current bearer before the first attempt. On a 401, calls
        :meth:`AuthCredentialProvider.refresh_after_unauthorized`; if it
        reports a fresh token was obtained, re-stamps the header and retries.
        Stops as soon as a non-401 response lands, the refresher reports no
        change (``False``), the refreshed snapshot carries no token, or the
        retry budget is exhausted -- mirroring Rust's break conditions.

        The same ``request`` object is reused across retries (httpx leaves
        it reusable after ``send``); callers must pass a request with a
        non-streaming body for the retry path to fire.

        Args:
            request: The outbound HTTP request; its ``Authorization`` header
                is mutated in place across attempts.
            send: The dispatch callable (reqwest-middleware's ``Next``).
                Typically ``client.send`` for an ``httpx.AsyncClient``.

        Returns:
            The first non-401 response, or the last 401 if every attempt was
            unauthorized.
        """
        snap = self._credentials.snapshot()
        if snap.token is not None:
            self._apply_auth_header(request, snap.token)

        response = await send(request)

        if response.status_code != _UNAUTHORIZED or self._max_retries == 0:
            return response

        last_response = response
        for _ in range(self._max_retries):
            if not await self._credentials.refresh_after_unauthorized():
                break
            token = self._credentials.snapshot().token
            if token is None:
                break
            self._apply_auth_header(request, token)
            last_response = await send(request)
            if last_response.status_code != _UNAUTHORIZED:
                return last_response

        return last_response
