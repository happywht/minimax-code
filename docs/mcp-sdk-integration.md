# MCP SDK integration (M6-A / M6-B)

The production registry uses the official Python SDK `mcp==2.3.0` for explicit
`stdio`, legacy `sse`, and `streamable_http` configurations. The SDK negotiates
supported protocol versions within the selected transport, including legacy
initialize and modern discovery. Transport is never inferred from the URL or
silently converted between SSE and Streamable HTTP; unknown values are rejected.
Settings add/edit, typed IPC, mock backend, DAO and Schema045 preserve the same
transport enum. Existing stdio/SSE records retain their values and behavior.

Schema045 rebuilds only the MCP table inside the normal migration transaction.
Bundle format5 creates only schema45, while mappings1/41,2/42,3/43,4/44 remain
verify/restore only and restore exact SQLite bytes without migration. Old tools
reject format5/schema45. See [storage schema](storage-schema.md#schema045mcp-streamable-http)
and [data-only bundle](data-vault-bundle.md) for explicit upgrade/rollback limits.

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

SSE uses the SDK's SSE transport; Streamable HTTP uses its official connector
with an application-owned httpx2 client entered before the SDK in the same owner
stack. Both use the same origin/header/secret policy. Modern POST responses can
be JSON or bounded SSE events. Legacy HTTP sessions carry their session header
and attempt SDK DELETE termination before the HTTP client closes. Remote DELETE
is best effort; local context closure does not assert remote server state.
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

The client requests identity encoding and rejects compressed responses. Only successful GET HTTP200 SSE streams bypass the 2 MiB total body cap in the
legacy SSE transport. Streamable HTTP additionally permits POST200 SSE because
its connector consumes that path through the SDK event parser. Legacy POST and
all error/redirect/DELETE bodies remain capped regardless of an SSE MIME label,
including chunked bodies; the
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
without its response body or URL. The shared HTTP hook also records fixed HTTP
status numbers when the Streamable HTTP connector translates them into RPC
errors, without recording remote text. The project's separate httpx logging is unaffected.

No sampling, elicitation or roots callbacks are registered or advertised. Legacy
unsolicited requests receive SDK errors, and unknown methods are refused. Modern
`InputRequiredResult` automatic rounds are disabled (`input_required_max_rounds=0`),
so embedded requests cannot trigger a model, permission write or tool dispatch.
Server-supplied schemas are validated by the SDK; external JSON Schema references
are not retrieved by its validator.

## Tool bridge and limits

Tools retain namespacing, per-tool enabled state and existing permission dispatch.
The tool definition retains declared `outputSchema`. `mcp.invoke_tool` preserves
text, non-text content, `isError` and additive `structuredContent`. Structured false, zero, empty
objects/arrays and explicitly present null retain presence; absent structured
content stays absent. Audio and resource-link content join the existing text,
image and embedded-resource models. SDK schema errors fail the call; tool-level
`isError` remains an unsuccessful ToolResult.

The direct invocation IPC also exposes `contentBlocks`, the ordered content
blocks including text annotations; the existing `text` and non-text `content`
projections remain for older clients. The settings tester displays those blocks
alongside `structuredContent` when present, including explicit null. This is
an inspection view, not a renderer for external media or resource links.

The Agent bridge uses a separate bounded projection before results enter the
tool transcript: at most 16 ordered blocks, with text and resource text capped
at 2048 UTF-8 bytes per block. Image and audio data and resource blobs are
omitted, with their type and available media or resource identifiers retained.
Omitted blocks and truncated text are marked. A single text block retains the
existing `text` field; mixed or multiple blocks use `contentBlocks`. Structured
content is included only if JSON-serializable and at most 8192 serialized bytes;
otherwise an omission marker is returned. Tool errors remain failures. This
projection does not give the model image or audio perception. Direct invocation
continues to return the original content for inspection.

An optional per-server settings switch exposes `mcp__<server>__minimax_resources`
to the Agent when that server advertises resources. It is off by default. A call
requires an explicit tool or network allow rule, or interactive approval; deny
and ask rules take precedence, and planning/restricted dispatch stays blocked.
The tool lists up to 50 resources or reads an exact URI in a fresh listing from
the same live connection. It returns up to 16 matching content entries, each
text bounded to 4096 UTF-8 bytes, and omits blobs. A changed or disabled
connection is rejected before and after network operations. Resource templates,
subscriptions, binary processing and URIs not listed by the server are not
supported; returned content is untrusted. Name collisions prevent registration.
The reserved opt-in state key is `$minimax.resources`; a server tool with that
exact name also prevents resource-tool registration.

The separate `$minimax.prompts` switch is off by default. When enabled and
advertised, `mcp__<server>__minimax_prompts` lists prompt names and argument
descriptions or gets a uniquely listed name with bounded string arguments.
It checks declared and required arguments, the active connection, the same
tool/network permission rules, and the planning/restricted dispatch boundary.
It returns at most 16 role-labelled messages as untrusted tool data; text is
bounded and binary content omitted. No message is inserted into the conversation
as a user or assistant instruction, and no template is automatically executed.
Lists show at most 50 prompts and 16 arguments each, with omissions indicated.
The SDK's disabled input-required rounds remain in effect. Reserved key and
bridged name collisions prevent registration. This does not establish full
prompt interoperability or validate the server's content.

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
`uv run --frozen pytest -q tests/test_mcp_sdk_interop.py tests/test_mcp_sdk_client.py tests/test_mcp_http_policy.py tests/test_mcp_streamable_http.py`.
These are offline tests with a real official SDK server bound only to 127.0.0.1,
plus a real supervised SDK stdio child. They require no model or credentials.
Run all `tests/test_mcp*.py`, `tests/test_handlers_mcp.py` and
`tests/test_ipc_contract_doc.py` for the compatibility regression. The same locked
environment and test selection apply on Windows/Linux Python 3.11 and 3.12.
