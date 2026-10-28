# MCP SDK integration (M6-A)

The production registry uses the official Python SDK `mcp==2.3.0` for existing
`stdio` and legacy `sse` configurations. The SDK negotiates supported protocol
versions, including the legacy initialize handshake and the 2026 discovery
protocol. This does not add the Streamable HTTP transport to the product.
`streamable_http` and every unknown transport are rejected; they are never stored
or interpreted as `sse`. The database schema, export format 4, and existing UI
transport choices remain unchanged. A future HTTP transport requires its own
schema/export compatibility slice.

## Ownership and execution policy

`SDKMCPClient` gives each connection one retained asyncio owner task. That task
enters and exits every SDK/AnyIO context in the same task and in reverse order.
Callers submit bounded operations from other tasks. Cancelling one call does not
cancel the connection. Closing cancels active calls, signals the owner, and waits
for context teardown; concurrent closes share one retained cleanup task. Repeated
caller cancellation cannot abandon cleanup. A teardown timeout is reported as
unconfirmed cleanup, and the registry keeps the connection for shutdown reporting.
Transport EOF marks the connection disconnected and wakes its owner.

For stdio, a small official Transport-protocol adapter converts the project's
managed JSON pipe to SDK SessionMessage streams. The SDK stdio process launcher
is never used. Existing `before_executable_extension`, `execution.spawn_exec`,
process profiles, filtered child environment, Windows hidden-window behavior,
and process-tree termination remain responsible for execution. Explicit configured
environment values still reach the child. Ambient secret variables do not.

The legacy project `MCPClient`, server, codec and transport classes remain public
for compatibility and in-process testing. Production `MCPRegistry.add_server`
uses the official SDK; it does not silently fall back to the old client.

## HTTP and authority boundary

SSE uses the SDK's SSE transport with an application-owned httpx2 client factory.
Only explicit headers or an explicit bearer token are supplied. Ambient proxies,
netrc and other environment-derived HTTP credentials are disabled (`trust_env=False`).
OAuth configuration metadata is retained, but OAuth discovery, browser login,
token refresh and registration are not implemented. No interactive auth handler
is installed. A 401 fails without opening a browser or modifying authorization.

URLs must be absolute HTTP(S) without userinfo, fragments or control characters.
Explicit localhost and private network targets are allowed. Headers must have
valid names and printable ASCII values; duplicate names, conflicting bearer and
Authorization values, and transport-owned headers are rejected. The client
anchors every request to the configured scheme/host/port, including SDK-created
SSE POST endpoint requests and redirect follow-ups. Same-origin GET/POST redirects
work. Cross-origin requests and HTTPS downgrades fail before sending any headers.
This deliberately also rejects same-host redirects to a different port or scheme.

The client requests identity encoding and rejects compressed responses. Only successful GET HTTP 200 SSE streams bypass the 2 MiB total body cap.
All other response bodies are capped, including POST/error/redirect bodies
claiming an SSE content type and chunked bodies; the
SDK bounds individual SSE events at 1 MiB. Long-lived SSE streams are not capped
by total bytes. SDK/httpx2/httpcore2 wire logging is suppressed at those package
loggers, because their debug/error messages can include URL queries, headers or
remote content. The integration exposes fixed errors containing operation,
exception class and an available HTTP status, never remote text or configured
secrets. This is a persistent, process-wide policy for these three package logger
names (including handlers already installed on children), not a per-connection
filter. Those packages are currently used only by this integration; adding
another integration must revisit this diagnostic tradeoff. SDK wire diagnostics
are unavailable after the first connection. Attach warnings retain the fixed
operation/leaf exception type/HTTP status, so an HTTP 401 remains distinguishable
without its response body or URL. The project's separate httpx logging is unaffected.

No sampling, elicitation or roots callbacks are registered or advertised. Legacy
unsolicited requests receive SDK errors, and unknown methods are refused. Modern
`InputRequiredResult` automatic rounds are disabled (`input_required_max_rounds=0`),
so embedded requests cannot trigger a model, permission write or tool dispatch.
Server-supplied schemas are validated by the SDK; external JSON Schema references
are not retrieved by its validator.

## Tool bridge and limits

Tools retain namespacing, per-tool enabled state and existing permission dispatch.
The bridge and `mcp.invoke_tool` preserve text, non-text content, `isError`, declared
`outputSchema` and additive `structuredContent`. Structured false, zero, empty
objects/arrays and explicitly present null retain presence; absent structured
content stays absent. Audio and resource-link content join the existing text,
image and embedded-resource models. SDK schema errors fail the call; tool-level
`isError` remains an unsuccessful ToolResult.

Each operation has one 30-second total deadline including semaphore wait and all
pages/retries, with at most eight active operations. Explicit tool/resource/prompt
listings are capped at 100 pages, 1000 items and 8 MiB of serialized listing data.
Cursors are at most 4096 characters and must not repeat. An incomplete/invalid
listing fails as a whole. The SDK's own modern tool-header mismatch retry is also
bounded by its release-defined page cap and the same total operation deadline.
Modern liveness uses tools/list because modern MCP removed ping; legacy uses ping.

## Release and reproducibility

The release wheel, rather than the changing main README, is the API baseline:

- [PyPI mcp 2.3.0](https://pypi.org/project/mcp/2.3.0/) and
  [release metadata](https://pypi.org/pypi/mcp/2.3.0/json).
- [Official release source](https://github.com/modelcontextprotocol/python-sdk/tree/v2.3.0),
  commit `2118f14f8a19bc158d8a1cf90af58d85d187f849`.
- `mcp-2.3.0-py3-none-any.whl` SHA256
  `dd0c44c089d16453e8ae31a3877a0054d7a2314caaa81f5e0541b9b1734b2377`.
- `mcp_types-2.3.0-py3-none-any.whl` SHA256
  `968efdbdaedfab06adae40d378a34395f1090c5921d4be3c9cde283aaf76d91d`.

Release metadata supports Python >=3.10; this project retains Python >=3.11.
`uv.lock` locks both SDK packages to 2.3.0 and the new httpx2 stack separately from
existing httpx 0.28.1. FastAPI, Starlette, Pydantic and AnyIO stay at their existing
locked versions. Existing idna updates from 3.17 to 3.20 to satisfy resolution.
No dependency is fetched dynamically during connection startup.

From `agent/`, run `uv sync --frozen --extra attachments --extra dev`, then
`uv run --frozen pytest -q tests/test_mcp_sdk_interop.py tests/test_mcp_sdk_client.py tests/test_mcp_http_policy.py`.
These are offline tests with a real official SDK server bound only to 127.0.0.1,
plus a real supervised SDK stdio child. They require no model or credentials.
Run all `tests/test_mcp*.py`, `tests/test_handlers_mcp.py` and
`tests/test_ipc_contract_doc.py` for the compatibility regression. The same locked
environment and test selection apply on Windows/Linux Python 3.11 and 3.12.
