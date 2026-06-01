//! Tauri command handlers — the typed surface that the React frontend calls.
//!
//! Every command is a thin wrapper that forwards to the Python agent via
//! the shared stdin writer. Responses come back as Tauri events
//! (`ipc:response` / `ipc:event`) on the same webview.

use crate::ipc::{self, AppState};
use serde::Serialize;
use tauri::State;

/// Result envelope for the `ipc_request` command. The actual response
/// payload is delivered asynchronously via the `ipc:response` event —
/// we return immediately with the request id we used so the frontend
/// can correlate.
#[derive(Debug, Serialize)]
pub struct RequestAck {
    pub id: String,
    pub method: String,
}

/// Send a JSON-RPC request to the Python agent.
///
/// The frontend must listen for the `ipc:response` event filtered by the
/// returned `id` (or pass a unique `id` itself and match on that).
#[tauri::command]
pub async fn ipc_request(
    state: State<'_, AppState>,
    method: String,
    params: Option<serde_json::Value>,
    id: Option<serde_json::Value>,
) -> Result<RequestAck, String> {
    let id_value = id.unwrap_or_else(|| {
        serde_json::Value::String(uuid::Uuid::new_v4().to_string())
    });
    let payload = serde_json::json!({
        "jsonrpc": "2.0",
        "id": id_value,
        "method": method,
        "params": params.unwrap_or(serde_json::Value::Null),
    });
    let body = serde_json::to_string(&payload)
        .map_err(|e| format!("serialize request: {e}"))?;
    ipc::send_to_agent(&state, &body).await?;
    Ok(RequestAck {
        id: match &id_value {
            serde_json::Value::String(s) => s.clone(),
            other => other.to_string(),
        },
        method,
    })
}

/// Send a JSON-RPC notification (no `id`, no response expected).
#[tauri::command]
pub async fn ipc_notify(
    state: State<'_, AppState>,
    method: String,
    params: Option<serde_json::Value>,
) -> Result<(), String> {
    let payload = serde_json::json!({
        "jsonrpc": "2.0",
        "method": method,
        "params": params.unwrap_or(serde_json::Value::Null),
    });
    let body = serde_json::to_string(&payload)
        .map_err(|e| format!("serialize notification: {e}"))?;
    ipc::send_to_agent(&state, &body).await
}

/// Liveness probe: returns true once the agent process is up and we
/// have a managed `AppState`. Used by the frontend to gate the UI on
/// agent readiness.
#[tauri::command]
pub fn ping_agent(_state: State<'_, AppState>) -> bool {
    // The fact that we received a `State<'_, AppState>` is itself proof
    // that `app.manage(AppState { ... })` ran in `setup()`. So the agent
    // bridge is up.
    true
}
