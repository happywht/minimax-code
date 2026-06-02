//! JSON-RPC 2.0 over stdio bridge between the Tauri shell and the Python agent sidecar.
//!
//! Protocol details are documented in `docs/ipc-contract.md`. In summary:
//!   - Each message is a single line of UTF-8 JSON terminated by `\n`.
//!   - Frontend → Rust → Python: `request` (with `id`) or `notification` (no `id`).
//!   - Python → Rust → Frontend: `response` (with same `id`) or push `event`.
//!
//! The Rust side spawns the Python sidecar as a child process, then
//! concurrently reads stdout and exposes a `Mutex<Sender<...>>` to commands
//! that want to write to stdin. All read messages are parsed and emitted to
//! the webview as Tauri events (`ipc:event`, `ipc:response`).

use std::process::Stdio;
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStderr, ChildStdin, ChildStdout, Command};
use tokio::sync::Mutex as AsyncMutex;

/// Name of the Tauri event carrying a JSON-RPC response to a previous request.
pub const RESPONSE_EVENT: &str = "ipc:response";

/// Name of the Tauri event carrying a JSON-RPC notification or push event.
pub const EVENT_NAME: &str = "ipc:event";

/// Name of the Tauri event carrying sidecar lifecycle changes (started/exited).
pub const SIDE_CAR_EVENT: &str = "ipc:sidecar";

/// Locate the sidecar binary. In production builds, Tauri places it next to
/// the main executable. In dev mode, we fall back to a path relative to the
/// project root so `tauri dev` works without bundling.
fn sidecar_command(app: &AppHandle) -> Command {
    let cmd_name = if cfg!(windows) {
        "minimax-code-agent.exe"
    } else {
        "minimax-code-agent"
    };

    // Tauri injects the sidecar next to the executable in production.
    // In dev, we construct a command that invokes Python directly.
    let mut command = if let Ok(exe) = app
        .path()
        .resolve(format!("binaries/{}", cmd_name), tauri::path::BaseDirectory::Resource)
    {
        let mut c = Command::new(exe);
        c.env("MINIMAX_CODE_ENV", "production");
        c
    } else {
        // Dev mode: invoke Python directly with the unbuffered stdio flag.
        let project_root = app
            .path()
            .resource_dir()
            .ok()
            .and_then(|p| p.parent().map(|p| p.to_path_buf()))
            .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());

        let agent_dir = project_root.join("agent");
        let python = std::env::var("MINIMAX_CODE_PYTHON")
            .unwrap_or_else(|_| "python".to_string());

        let mut c = Command::new(python);
        c.current_dir(&agent_dir)
            .arg("-m")
            .arg("minimax_code")
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("MINIMAX_CODE_ENV", "development")
            // Make sure child stdio is wired correctly.
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        c
    };

    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // Detach the child from the parent's console on Windows.
        .kill_on_drop(true);
    command
}

/// Shared handle to the sidecar process. The stdin writer is wrapped in an
/// `AsyncMutex` so multiple commands can send concurrently without
/// interleaving bytes.
pub struct AgentProcess {
    pub child: AsyncMutex<Child>,
    pub stdin: AsyncMutex<ChildStdin>,
}

/// State held by Tauri for the lifetime of the application.
pub struct AppState {
    pub agent: Arc<AgentProcess>,
}

/// JSON-RPC 2.0 envelope used for the few fields we have to look at
/// before forwarding. We re-emit the full payload to the webview so the
/// frontend can perform type-narrow validation.
#[derive(Debug, Serialize, Deserialize)]
pub struct Envelope {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub method: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub params: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub event: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

/// Synchronously spawn the Python sidecar and register the `AppState` with
/// Tauri BEFORE returning to the setup closure.
///
/// **Why synchronous, not fire-and-forget?**
/// `lib.rs` calls this from inside `tauri::Builder::setup()`. If we spawned a
/// background task that calls `app.manage(...)` later, the webview would
/// mount and the React app would fire its initial IPC calls
/// (`session.list`, `agent.list`, ...) before `AppState` is registered.
/// Tauri would then reject every command with
/// `state not managed for field 'state' on command 'ipc_request'`.
///
/// By spawning the child + calling `app.manage()` synchronously here, the
/// state is guaranteed to be in Tauri's registry before the setup closure
/// returns and the webview starts loading. The stdio pumps still run
/// asynchronously — they just need the `Arc<AgentProcess>` we hand back.
pub fn init_agent_bridge(app: &AppHandle) -> anyhow::Result<()> {
    let mut command = sidecar_command(app);
    let mut child = command.spawn()?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| anyhow::anyhow!("agent stdout missing"))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| anyhow::anyhow!("agent stderr missing"))?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| anyhow::anyhow!("agent stdin missing"))?;

    let _ = app.emit(SIDE_CAR_EVENT, serde_json::json!({"status": "started"}));

    let agent = Arc::new(AgentProcess {
        child: AsyncMutex::new(child),
        stdin: AsyncMutex::new(stdin),
    });
    app.manage(AppState {
        agent: agent.clone(),
    });

    // Hand the stdio handles to a long-running pump task. This MUST run
    // after `app.manage(...)` so the agent handle is visible to commands
    // that need to write to stdin.
    tauri::async_runtime::spawn(pump_stdio(app.clone(), agent, stdout, stderr));

    Ok(())
}

/// Pump the agent's stdout and stderr for the lifetime of the process.
/// Returns when the child closes its stdout (crash or clean shutdown).
async fn pump_stdio(
    app: AppHandle,
    _agent: Arc<AgentProcess>,
    stdout: ChildStdout,
    stderr: ChildStderr,
) {
    let app_for_out = app.clone();
    let stdout_handle = tauri::async_runtime::spawn(async move {
        if let Err(e) = pump_stdout(app_for_out, stdout).await {
            tracing::error!(error = %e, "stdout pump exited");
        }
    });

    // Stderr is for human-readable logs only — just forward to tracing.
    let stderr_handle = tauri::async_runtime::spawn(async move {
        let mut lines = BufReader::new(stderr).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            tracing::warn!(target: "agent_stderr", "{}", line);
        }
    });

    let _ = stdout_handle.await;
    let _ = stderr_handle.await;
}

async fn pump_stdout(app: AppHandle, stdout: ChildStdout) -> anyhow::Result<()> {
    let mut lines = BufReader::new(stdout).lines();
    let mut buffer = String::new();

    while let Some(line) = lines.next_line().await? {
        buffer.push_str(&line);
        if line.is_empty() && buffer.is_empty() {
            continue;
        }

        // JSON-RPC framing: one JSON object per line. We accept either
        // a fully-formed object in a single line, or a streamed one.
        let trimmed = buffer.trim();
        if trimmed.is_empty() {
            buffer.clear();
            continue;
        }

        match serde_json::from_str::<Envelope>(trimmed) {
            Ok(env) => {
                buffer.clear();
                let payload = serde_json::to_value(&env).unwrap_or(Value::Null);
                // Distinguish response (has id) from event/notification.
                if env.id.is_some() && (env.result.is_some() || env.error.is_some()) {
                    let _ = app.emit(RESPONSE_EVENT, payload);
                } else {
                    let _ = app.emit(EVENT_NAME, payload);
                }
            }
            Err(e) => {
                // Wait for the rest of the line — partial JSON may have arrived.
                if is_definitely_incomplete(&e) {
                    buffer.push('\n');
                    continue;
                }
                tracing::error!(error = %e, raw = %buffer, "malformed JSON from agent");
                let _ = app.emit(
                    EVENT_NAME,
                    serde_json::json!({
                        "event": "ipc.parse_error",
                        "data": {"error": e.to_string(), "raw": buffer},
                    }),
                );
                buffer.clear();
            }
        }
    }

    Ok(())
}

fn is_definitely_incomplete(e: &serde_json::Error) -> bool {
    e.is_eof() || e.to_string().contains("EOF while parsing")
}

/// Write a single JSON-RPC message to the sidecar's stdin.
///
/// Appends a `\n` terminator as required by the protocol. The write is
/// guarded by an async mutex so concurrent commands cannot interleave.
pub async fn send_to_agent(
    state: &AppState,
    message: &str,
) -> Result<(), String> {
    let mut stdin = state.agent.stdin.lock().await;
    let mut payload = message.as_bytes().to_vec();
    payload.push(b'\n');
    stdin
        .write_all(&payload)
        .await
        .map_err(|e| format!("failed to write to agent stdin: {e}"))?;
    stdin
        .flush()
        .await
        .map_err(|e| format!("failed to flush agent stdin: {e}"))?;
    Ok(())
}
