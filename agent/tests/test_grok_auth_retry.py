"""Tests for the grok_auth retry middleware (R188).

R188 lands ``xai-grok-auth``'s feature-gated ``retry_middleware`` leaf:
:class:`AuthRetryMiddleware` stamps the bearer on the outbound request and
retries on 401 with a refreshed token, up to ``max_retries`` times. The tests
pin every branch of the retry state machine using scripted fakes (no live HTTP
mock server -- the ``send`` callable is the dispatch seam, so a recording fake
covers every path Rust's mockito e2e tests covered).

Branch coverage:

1. **Happy path** -- token stamped, 200 first try, no retry.
2. **No token** -- ``snapshot().token is None`` -> header skipped, no retry.
3. **401 + refresh False** -- one refresh attempt, no resend, 401 returned.
4. **401 + refresh True -> 200** -- stale token 401, refresh swaps to fresh,
   retry succeeds, header re-stamped.
5. **max_retries bounds attempts** -- 3 retries against perpetual 401 = 4
   sends (1 + 3), 3 refreshes, 401 returned.
6. **Non-401** -- 500 -> no retry.
7. **max_retries == 0** -- 401 -> no retry at all.
8. **refresh True but token None** -- refresh reports success yet snapshot has
   no token -> break before resend.
9. **Mid-retry non-401** -- second attempt returns 200 -> stop immediately.
"""

from __future__ import annotations

import httpx

from minimax_code.grok_auth import (
    AuthCredentialProvider,
    AuthRetryMiddleware,
    CredentialSnapshot,
    HttpAuth,
)


class _ScriptedProvider(AuthCredentialProvider):
    """Fake provider: pops the next token on each ``snapshot`` call.

    Each :meth:`snapshot` call returns the next scripted token, modelling a
    refresh-aware manager whose token changes after
    :meth:`refresh_after_unauthorized` swaps it.
    """

    def __init__(
        self,
        tokens: list[str | None],
        refresh_results: list[bool],
    ) -> None:
        self._tokens = list(tokens)
        self._refresh_results = list(refresh_results)
        self.refresh_calls = 0

    def apply(self, request: httpx.Request, base_url: str) -> httpx.Request:
        # HttpAuth trait obligation -- the retry middleware does not call it
        # (it reads snapshot().token directly), so a no-op satisfies the ABC.
        return request

    def snapshot(self) -> CredentialSnapshot:
        token = self._tokens.pop(0) if self._tokens else None
        return CredentialSnapshot(token=token)

    async def refresh_after_unauthorized(self) -> bool:
        self.refresh_calls += 1
        return self._refresh_results.pop(0) if self._refresh_results else False


class _RecordingSend:
    """Fake ``send`` callable: records the Authorization header per call.

    Returns scripted status codes in order; the request is echoed back on the
    response so callers can inspect the final header state.
    """

    def __init__(self, statuses: list[int]) -> None:
        self._statuses = list(statuses)
        self.calls: list[str | None] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.headers.get("authorization"))
        status = self._statuses.pop(0) if self._statuses else 500
        return httpx.Response(status, request=request)


def _request() -> httpx.Request:
    return httpx.Request("GET", "https://example.test/api")


# ---------------------------------------------------------------------------
# Happy path + no-token.
# ---------------------------------------------------------------------------


async def test_execute_stamps_header_on_success() -> None:
    provider = _ScriptedProvider(tokens=["my-token"], refresh_results=[])
    send = _RecordingSend([200])
    mw = AuthRetryMiddleware(credentials=provider, max_retries=1)

    resp = await mw.execute(_request(), send)

    assert resp.status_code == 200
    assert send.calls == ["Bearer my-token"]
    assert provider.refresh_calls == 0


async def test_execute_no_token_skips_header() -> None:
    provider = _ScriptedProvider(tokens=[None], refresh_results=[])
    send = _RecordingSend([200])
    mw = AuthRetryMiddleware(credentials=provider, max_retries=1)

    resp = await mw.execute(_request(), send)

    assert resp.status_code == 200
    assert send.calls == [None]
    assert provider.refresh_calls == 0


# ---------------------------------------------------------------------------
# 401 retry state machine.
# ---------------------------------------------------------------------------


async def test_execute_401_no_refresh_returns_401() -> None:
    provider = _ScriptedProvider(tokens=["tok"], refresh_results=[False])
    send = _RecordingSend([401])
    mw = AuthRetryMiddleware(credentials=provider, max_retries=1)

    resp = await mw.execute(_request(), send)

    assert resp.status_code == 401
    assert send.calls == ["Bearer tok"]  # no resend after refresh False
    assert provider.refresh_calls == 1


async def test_execute_401_refresh_succeeds_retries_to_200() -> None:
    provider = _ScriptedProvider(tokens=["stale-token", "fresh-token"], refresh_results=[True])
    send = _RecordingSend([401, 200])
    mw = AuthRetryMiddleware(credentials=provider, max_retries=1)

    resp = await mw.execute(_request(), send)

    assert resp.status_code == 200
    assert send.calls == ["Bearer stale-token", "Bearer fresh-token"]
    assert provider.refresh_calls == 1


async def test_execute_max_retries_bounds_attempts() -> None:
    # 3 retries against perpetual 401 -> 4 sends (1 initial + 3 retry), 3 refreshes.
    provider = _ScriptedProvider(
        tokens=["t0", "t1", "t2", "t3"],
        refresh_results=[True, True, True],
    )
    send = _RecordingSend([401, 401, 401, 401])
    mw = AuthRetryMiddleware(credentials=provider, max_retries=3)

    resp = await mw.execute(_request(), send)

    assert resp.status_code == 401
    assert send.calls == ["Bearer t0", "Bearer t1", "Bearer t2", "Bearer t3"]
    assert provider.refresh_calls == 3


async def test_execute_non_401_does_not_retry() -> None:
    provider = _ScriptedProvider(tokens=["tok"], refresh_results=[True, True, True])
    send = _RecordingSend([500])
    mw = AuthRetryMiddleware(credentials=provider, max_retries=3)

    resp = await mw.execute(_request(), send)

    assert resp.status_code == 500
    assert send.calls == ["Bearer tok"]
    assert provider.refresh_calls == 0


async def test_execute_max_retries_zero_skips_retry() -> None:
    provider = _ScriptedProvider(tokens=["tok"], refresh_results=[True])
    send = _RecordingSend([401])
    mw = AuthRetryMiddleware(credentials=provider, max_retries=0)

    resp = await mw.execute(_request(), send)

    assert resp.status_code == 401
    assert send.calls == ["Bearer tok"]
    assert provider.refresh_calls == 0


async def test_execute_refresh_true_but_token_none_breaks() -> None:
    # refresh reports success, but the next snapshot has no token -> no resend.
    provider = _ScriptedProvider(tokens=["tok", None], refresh_results=[True])
    send = _RecordingSend([401])
    mw = AuthRetryMiddleware(credentials=provider, max_retries=2)

    resp = await mw.execute(_request(), send)

    assert resp.status_code == 401
    assert send.calls == ["Bearer tok"]  # initial only; retry aborted (no token)
    assert provider.refresh_calls == 1


async def test_execute_stops_on_mid_retry_non_401() -> None:
    # First retry still 401, second retry 200 -> stop immediately.
    provider = _ScriptedProvider(tokens=["t0", "t1", "t2"], refresh_results=[True, True])
    send = _RecordingSend([401, 401, 200])
    mw = AuthRetryMiddleware(credentials=provider, max_retries=3)

    resp = await mw.execute(_request(), send)

    assert resp.status_code == 200
    assert send.calls == ["Bearer t0", "Bearer t1", "Bearer t2"]
    assert provider.refresh_calls == 2  # third retry never reached


# ---------------------------------------------------------------------------
# Structural: AuthRetryMiddleware collaborates with the R187 contract types.
# ---------------------------------------------------------------------------


def test_middleware_barrel_exposes_class() -> None:
    """R188 raises the crate barrel from 4 to 5 symbols."""
    from minimax_code.grok_auth import __all__ as grok_auth_all

    assert "AuthRetryMiddleware" in grok_auth_all
    assert AuthRetryMiddleware is not None


def test_middleware_accepts_any_auth_credential_provider() -> None:
    """The middleware is typed against the trait, not a concrete provider."""
    provider = _ScriptedProvider(tokens=["t"], refresh_results=[])
    mw = AuthRetryMiddleware(credentials=provider, max_retries=1)
    # The provider IS-A HttpAuth (R187 supertrait) and AuthCredentialProvider.
    assert isinstance(mw._credentials, AuthCredentialProvider)
    assert isinstance(mw._credentials, HttpAuth)
