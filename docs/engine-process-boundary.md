# Codex engine process boundary

The Codex App Server and its version probe use the same managed local process
lifetime boundary as commands: a separate process group on POSIX, and a
Windows launcher that owns a kill-on-close Job Object. The Windows launcher
also holds the actual service's process handle and creation identity; abrupt
service death closes the Job and terminates its descendants. These mechanisms
manage lifetime. They do not isolate filesystem or network access.

The engine adapter has a dedicated internal startup capability. It validates
the active workspace and rejects a missing directory, workspace escape, or
file overlay. Starting the trusted, server-selected engine is allowed during
planning; arbitrary command tools remain denied by the read-only execution
policy. Codex turns continue to receive an explicit `readOnly` sandbox for
planning, with `networkAccess: false` and no approval-based escape.

## Environment configuration

Children receive the shared allowlist of runtime variables, including PATH,
HOME/USERPROFILE, temporary directories and OS runtime paths. The Codex adapter
also preserves `CODEX_HOME`. It does not inherit the service's other secrets,
all variables ending in `_KEY`, or credentials named by a project's Codex
configuration.

An operator may explicitly select additional existing service variables with
`MINIMAX_CODE_CODEX_ENV_VARS`, a JSON array of environment variable names. For
example, an operator who deliberately uses environment-based provider login,
a proxy, and a custom CA might configure:

```powershell
$env:MINIMAX_CODE_CODEX_ENV_VARS = '["OPENAI_API_KEY","HTTPS_PROXY","CODEX_CA_CERTIFICATE"]'
```

Only the selected names are forwarded, if present. The list allows up to 64
names matching `[A-Za-z_][A-Za-z0-9_]{0,255}`. Invalid configuration fails before
launch; errors do not print selected values. Trusted Python embedding code may
also pass an explicit `extra_env` string mapping to `AppServerConnection` or
`CodexEngine.connect`. Those entries override forwarded values; malformed names
or NUL bytes are rejected. Neither setting is accepted from model tool arguments.

Codex documents stored login in `~/.codex/auth.json` or the OS credential store;
`CODEX_HOME` changes the Codex configuration and credential location. The
adapter preserves the home-directory inputs but does not read, copy or change
credentials. OS keyring integrations can need additional session metadata,
such as `DBUS_SESSION_BUS_ADDRESS` or `XDG_RUNTIME_DIR` on Linux; an operator can
opt those names in explicitly. Proxy variables, `SSL_CERT_FILE`,
`CODEX_CA_CERTIFICATE`, custom-provider `env_key` names and `env_http_headers`
names likewise require explicit selection. Project configuration never expands
this selection automatically.

References: [Codex authentication](https://learn.chatgpt.com/docs/auth) and
[advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).
The tests use synthetic values and local fake protocol servers. Real provider
authentication, keyring availability and model calls remain unverified by this
offline process suite.

## Lifetime and verification

Start, close, initialization failure, malformed protocol/EOF, leader exit, and
cancellation all retain one cleanup task until the owned process tree is
reaped. Repeated cancellation cannot detach an in-flight spawn or let a caller
release the connection while cleanup still runs. A leader-exit watcher observes
the process return code independently of pipe EOF, since a descendant can hold
stdout open. The Windows host releases its own duplicated stdio handles after
launch, so an engine's unexpected stdout EOF remains observable.

`test_process_engine_boundary.py` runs actual local synthetic processes for
environment filtering, trusted opt-in, planning startup versus command denial,
missing/escaped workspace, initialization failure, descendant heartbeat stop,
leader exit with inherited stdout, unexpected EOF, repeated cancellation,
version-probe timeout and Windows `.cmd` launch. The Windows owner-lifetime
tests kill only the actual synthetic service and verify launcher, engine and
grandchild process handles become signaled, with heartbeat activity stopped.
Protocol replay tests independently verify each turn's sandbox policy; they do
not establish real Codex provider or network behavior. The protocol pin stays
at `codex-cli 0.163.0`.
