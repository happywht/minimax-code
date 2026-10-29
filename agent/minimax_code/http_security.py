"""Local application authentication and browser request boundaries.

The local operator and explicitly configured UI origins are trusted. Browsers
cannot choose their Origin header; local non-browser clients must present the
bearer token. This boundary does not defend against a compromised OS account.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers


class ApplicationAuth:
    def __init__(self, origins: list[str], token: str | None = None):
        self.token = token or os.environ.get("MINIMAX_CODE_AUTH_TOKEN") or secrets.token_urlsafe(32)
        self.origins = set(origins)
        self.preview_grants: dict[str, dict] = {}
        self.hosts = {"127.0.0.1", "localhost", "::1"}
        self.hosts.update(value.strip().lower() for value in os.environ.get("MINIMAX_CODE_ALLOWED_HOSTS", "").split(",") if value.strip())
        if "*" in self.hosts:
            raise ValueError("Wildcard hosts are not supported")

    def host_allowed(self, headers: Headers) -> bool:
        try:
            target = urlsplit("http://" + headers.get("host", ""))
            _ = target.port  # Reject invalid port syntax before auth/cookie handling.
            return target.hostname in self.hosts and target.username is None and target.password is None
        except ValueError:
            return False

    @staticmethod
    def cookie_name(headers: Headers) -> str:
        # Cookies ignore ports. Separate local services must not overwrite
        # each other's session cookie during a preview bootstrap.
        port = urlsplit("http://" + headers.get("host", "")).port
        return f"minimax_session_{port}" if port is not None else "minimax_session"

    def origin_allowed(self, headers: Headers, scheme: str) -> bool:
        origin = headers.get("origin")
        if origin is None:
            return True  # non-browser authenticated clients
        own = f"{scheme.replace('ws', 'http')}://{headers.get('host', '')}"
        return origin in self.origins or origin == own

    def authenticated(self, headers: Headers) -> bool:
        bearer = headers.get("authorization", "")
        candidates = [bearer[7:]] if bearer.startswith("Bearer ") else []
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get("cookie", ""))
            cookie_name = self.cookie_name(headers)
            if cookie_name in cookies:
                candidates.append(cookies[cookie_name].value)
        except Exception:
            pass
        for protocol in headers.get("sec-websocket-protocol", "").split(","):
            if protocol.strip().startswith("minimax-auth."):
                candidates.append(protocol.strip()[len("minimax-auth."):])
        return any(hmac.compare_digest(candidate.encode(), self.token.encode()) for candidate in candidates)

    def issue_preview_grant(self, workspace: str, session_id: str | None) -> str:
        now = time.monotonic()
        self.preview_grants = {key: value for key, value in self.preview_grants.items()
                               if value["expires"] > now}
        for key, value in self.preview_grants.items():
            if (value["workspace"] == workspace and value["session_id"] == session_id
                    and value["expires"] > now + 60):
                return key
        if len(self.preview_grants) >= 128:
            raise ValueError("Too many active previews; wait for an existing preview to expire")
        key = secrets.token_urlsafe(32)
        self.preview_grants[key] = {"workspace": workspace, "session_id": session_id,
                                    "expires": now + 900}
        return key

    def preview_grant(self, path: str) -> dict | None:
        parts = path.split("/", 4)
        if len(parts) != 5 or parts[1:3] != ["preview", "view"]:
            return None
        grant = self.preview_grants.get(parts[3])
        return grant if grant and grant["expires"] > time.monotonic() else None


class ApplicationBoundary:
    def __init__(self, app, auth: ApplicationAuth):
        self.app, self.auth = app, auth

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        path = scope.get("path", "/")
        grant = (self.auth.preview_grant(path)
                 if scope["type"] == "http" and scope.get("method") in {"GET", "HEAD"} else None)
        protected = path.startswith(("/rpc", "/ws", "/preview", "/complete", "/hooks", "/artifacts", "/attachments", "/hosts/download"))
        status, detail = 0, ""
        if not self.auth.host_allowed(headers):
            status, detail = 400, "Untrusted Host"
        elif ((protected or path == "/auth/session")
              and not self.auth.origin_allowed(headers, scope.get("scheme", "http"))
              and not (grant and headers.get("origin") == "null")):
            status, detail = 403, "Untrusted Origin"
        elif protected and scope.get("method") != "OPTIONS" and not grant and not self.auth.authenticated(headers):
            status, detail = 401, "Application authentication required"
        if status:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4401 if status == 401 else 4403})
            else:
                await JSONResponse({"error": detail}, status_code=status)(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # A client that offers an authentication subprotocol expects it
            # in the handshake response (notably Node's native WebSocket).
            protocol = next((value.strip() for value in headers.get(
                "sec-websocket-protocol", ""
            ).split(",") if value.strip() == f"minimax-auth.{self.auth.token}"), None)
            async def authenticated_send(message):
                if message["type"] == "websocket.accept" and protocol:
                    message = {**message, "subprotocol": protocol}
                await send(message)
            return await self.app(scope, receive, authenticated_send)
        return await self.app(scope, receive, send)


def install_application_auth(app: FastAPI, origins: list[str], *, token: str | None = None) -> ApplicationAuth:
    auth = ApplicationAuth(origins, token)
    app.state.auth = auth
    app.add_middleware(ApplicationBoundary, auth=auth)

    @app.post("/auth/session")
    async def bootstrap(request: Request):
        # Explicit header + Origin forces cross-origin browser preflight. No
        # wildcard origin and no unauthenticated CLI bootstrap are allowed.
        try:
            local = bool(request.client) and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        browser = local and bool(request.headers.get("origin")) and request.headers.get("x-minimax-client") == "web"
        if not auth.authenticated(request.headers) and not browser:
            return JSONResponse({"error": "Use the trusted local UI or a bearer token"}, status_code=401)
        response = JSONResponse({"token": auth.token}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
        response.set_cookie(auth.cookie_name(request.headers), auth.token, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/")
        return response

    return auth
