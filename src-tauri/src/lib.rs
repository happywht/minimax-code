//! MiniMax Code - Tauri 2.x application entry point.
//!
//! The Tauri shell hosts the React web UI and a Python agent sidecar.
//! All requests between the UI and the agent flow through this Rust bridge:
//!
//!   React UI ──invoke()──▶ Tauri Command ──stdin──▶ Python Agent
//!   React UI ◀──event()─── Tauri Event   ◀──stdout── Python Agent
//!
//! See `ipc.rs` for the JSON-RPC 2.0 over stdio bridge implementation.

pub mod commands;
pub mod ipc;

use tauri::Manager;

/// Application entry point invoked by `src-tauri/src/main.rs`.
pub fn run() {
    // Configure logging — default to INFO unless overridden by RUST_LOG.
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .with_target(false)
        .compact()
        .init();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Spawn the Python agent sidecar and start bridging stdio <-> Tauri events.
            let handle = app.handle().clone();
            ipc::spawn_agent_sidecar(handle);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::ipc_request,
            commands::ipc_notify,
            commands::ping_agent,
        ])
        .run(tauri::generate_context!())
        .expect("error while running MiniMax Code");
}
