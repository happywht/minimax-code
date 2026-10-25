#!/usr/bin/env node
// Run prepared release assets. Installation and frontend builds are explicit steps.
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { stat } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { Transform } from "node:stream";
import { fileURLToPath } from "node:url";

const defaultRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const prepare = "Run pnpm install, then (cd agent && uv sync), then pnpm build.";

async function requirePath(target, directory, message) {
  const info = await stat(target).catch(() => null);
  if (!info || !(directory ? info.isDirectory() : info.isFile())) throw new Error(message);
}

export async function productionConfig({ root = defaultRoot, env = process.env,
  platform = process.platform, cwd = process.cwd() } = {}) {
  root = path.resolve(root);
  const agent = path.join(root, "agent");
  const web = env.MINIMAX_CODE_WEB_DIST
    ? path.resolve(agent, env.MINIMAX_CODE_WEB_DIST) : path.join(root, "web", "dist");
  await requirePath(path.join(web, "index.html"), false,
    `Prepared frontend is missing at ${web}. Run pnpm build or set MINIMAX_CODE_WEB_DIST.`);
  const venv = path.resolve(agent, env.UV_PROJECT_ENVIRONMENT || ".venv");
  const python = env.MINIMAX_CODE_PYTHON || path.join(venv,
    platform === "win32" ? "Scripts/python.exe" : "bin/python");
  if (!path.isAbsolute(python)) throw new Error("MINIMAX_CODE_PYTHON must be an absolute executable path.");
  await requirePath(python, false, `Installed Python is missing at ${python}. ${prepare}`);
  const workspace = path.resolve(cwd,
    env.MINIMAX_CODE_WORKSPACE || env.MINIMAX_CODE_WORKSPACE_DIR || root);
  await requirePath(workspace, true,
    `Workspace does not exist: ${workspace}. Set MINIMAX_CODE_WORKSPACE to an existing directory.`);
  return { root, agent, python, workspace, web, env: { ...env,
    MINIMAX_CODE_WORKSPACE: workspace, MINIMAX_CODE_WEB_DIST: web,
    MINIMAX_CODE_ENV: "production", PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" } };
}

const posixLifetime = String.raw`
def watch_posix_parent():
    import signal, threading, time
    parent = int(sys.argv[1])
    if os.getppid() != parent or os.getpgrp() != os.getpid():
        raise RuntimeError("Production process has no owned parent/group")
    def monitor():
        while os.getppid() == parent:
            time.sleep(0.1)
        os.killpg(os.getpgrp(), signal.SIGKILL)
    threading.Thread(target=monitor, daemon=True, name="launcher-lifetime").start()
`;

// This isolated stdlib-only probe never imports application or site-package code.
const probeCode = String.raw`
import json, os, sys, runpy
${posixLifetime}
if sys.version_info < (3, 11):
    raise RuntimeError("Python 3.11 or newer is required")
created = None
if sys.platform == "win32":
    helpers = runpy.run_path(sys.argv[2])
    job = helpers['join_job']()
    kernel = helpers['_process_kernel']()
    parent = kernel.OpenProcess(0x00101000, False, int(sys.argv[1]))
    if not parent:
        raise OSError("Cannot identify production launcher")
    try:
        created = helpers['_creation_time'](kernel, parent)
    finally:
        kernel.CloseHandle(parent)
    helpers['watch_parent'](int(sys.argv[1]), created)
else:
    watch_posix_parent()
print(json.dumps({"launcher_pid": int(sys.argv[1]), "parent_created": str(created) if created else None,
                  "base_python": getattr(sys, "_base_executable", sys.executable)}), flush=True)
`;

const agentBootstrap = String.raw`
import os, sys, runpy
${posixLifetime}
if sys.platform != 'win32':
    watch_posix_parent()
try:
    import minimax_code.http_server
    import minimax_code.app
except Exception:
    import traceback
    print('Python dependency check failed. Run (cd agent && uv sync) to prepare dependencies.', file=sys.stderr, flush=True)
    traceback.print_exc()
    raise SystemExit(1)
print(sys.argv[2], flush=True)
sys.argv = ["minimax_code"]
runpy.run_module("minimax_code", run_name="__main__", alter_sys=True)
`;

function probe(config, spawnProcess, signal, timeout) {
  return new Promise((resolve, reject) => {
    let child, output = "", errors = "", settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      if (error) {
        child?.kill("SIGKILL");
        child?.stdout?.destroy();
        child?.stderr?.destroy();
        reject(error);
      } else resolve(value);
    };
    const abort = () => finish(new Error("Production startup interrupted."));
    const timer = setTimeout(() => finish(new Error("Python interpreter check timed out. Check the installed environment.")), timeout);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) { abort(); return; }
    try {
      child = spawnProcess(config.python, ["-I", "-S", "-c", probeCode, String(process.pid),
        path.join(config.agent, "minimax_code", "_windows_process_host.py")], {
        cwd: config.agent, env: config.env, shell: false, windowsHide: true,
        detached: process.platform !== "win32",
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (error) { finish(error); return; }
    child.on("error", (error) => finish(new Error(`Python could not start: ${error.message}. ${prepare}`)));
    const receive = (chunk, stderr) => {
      if (stderr) errors += chunk.toString(); else output += chunk.toString();
      if (output.length + errors.length > 32768) finish(new Error("Python identity diagnostics exceeded the output limit."));
    };
    child.stdout.on("data", (chunk) => receive(chunk, false));
    child.stderr.on("data", (chunk) => receive(chunk, true));
    child.on("close", (code) => {
      if (settled) return;
      if (code !== 0) { finish(new Error(`Python interpreter check failed (exit ${code}). ${prepare}\n${errors.trim()}`)); return; }
      try {
        const data = JSON.parse(output.trim());
        if (data.launcher_pid !== process.pid || !path.isAbsolute(data.base_python) ||
            (process.platform === "win32" && !/^[1-9][0-9]{0,19}$/.test(data.parent_created)))
          throw new Error("Invalid interpreter identity");
        finish(null, data);
      } catch { finish(new Error("Python interpreter check returned an invalid identity.")); }
    });
  });
}

function signalCode(signal) { return signal === "SIGINT" ? 130 : signal === "SIGTERM" ? 143 : 1; }

export async function runProduction({ root, env, spawnProcess = spawn,
  signals = process, log = console.error, probeTimeout = 15000, importTimeout = 15000,
  stopTimeout = 5000, output = process.stdout, errors = process.stderr } = {}) {
  let child, requested, escalation, importTimer;
  const controller = new AbortController();
  const kill = (signal) => {
    if (!child?.pid) return;
    try {
      if (process.platform === "win32") child.kill(signal);
      else process.kill(-child.pid, signal);
    } catch (error) { if (error.code !== "ESRCH") throw error; }
  };
  const stop = (signal) => {
    const repeated = Boolean(requested);
    requested ||= signal;
    controller.abort();
    kill(repeated ? "SIGKILL" : signal);
    escalation ??= setTimeout(() => kill("SIGKILL"), stopTimeout);
  };
  const interrupt = () => stop("SIGINT"), terminate = () => stop("SIGTERM");
  signals.on("SIGINT", interrupt);
  signals.on("SIGTERM", terminate);
  try {
    const config = await productionConfig({ root, env });
    const host = path.join(config.agent, "minimax_code", "_windows_process_host.py");
    if (process.platform === "win32") await requirePath(host, false,
      "Managed Windows process host is missing; restore the application files.");
    const identity = await probe(config, spawnProcess, controller.signal, probeTimeout);
    if (requested) return signalCode(requested);
    const marker = `minimax-imports-${randomUUID()}`;
    let executable = config.python, args = ["-c", agentBootstrap, String(process.pid), marker];
    if (process.platform === "win32") {
      await requirePath(identity.base_python, false, "Base Python executable is missing; repair the Python installation.");
      executable = identity.base_python;
      args = ["-I", "-S", host, "--parent", String(process.pid), String(identity.parent_created),
        "--exec", config.python, ...args];
    }
    if (requested) return signalCode(requested);
    log(`Using workspace: ${config.workspace}`);
    // The agent reports readiness; launching a process is not a health check.
    const code = await new Promise((resolve, reject) => {
      let imported = false, pending = "", startupError;
      const failStartup = (message) => {
        startupError ||= message;
        log(message);
        kill("SIGKILL");
      };
      child = spawnProcess(executable, args, { cwd: config.agent, env: config.env,
        shell: false, windowsHide: true, detached: process.platform !== "win32",
        stdio: ["inherit", "pipe", "pipe"] });
      importTimer = setTimeout(() => failStartup("Python dependency check timed out; review the installed environment."), importTimeout);
      const checkedOutput = new Transform({
        transform(chunk, encoding, callback) {
          if (imported) { callback(null, chunk); return; }
          pending += chunk.toString();
          const found = pending.match(new RegExp(`${marker}\\r?\\n`));
          if (found) {
            imported = true; clearTimeout(importTimer);
            callback(null, pending.slice(0, found.index) + pending.slice(found.index + found[0].length));
            pending = "";
          } else if (pending.length > 32768) callback(new Error("Dependency import output exceeded the limit."));
          else callback();
        },
        flush(callback) { if (pending) this.push(pending); callback(); },
      });
      checkedOutput.on("error", (error) => failStartup(error.message));
      child.stdout.pipe(checkedOutput).pipe(output, { end: false });
      child.stderr.pipe(errors, { end: false });
      child.once("error", reject);
      child.once("exit", () => {
        if (process.platform !== "win32") kill("SIGKILL");
      });
      child.once("close", (exitCode, exitSignal) => {
        resolve(startupError ? 1 : requested ? signalCode(requested) :
          exitCode === 0 && !imported ? 1 : exitCode ?? signalCode(exitSignal));
      });
    });
    return code;
  } catch (error) {
    if (!requested) log(`Production startup failed: ${error.message}`);
    return requested ? signalCode(requested) : 1;
  } finally {
    clearTimeout(escalation);
    clearTimeout(importTimer);
    signals.removeListener("SIGINT", interrupt);
    signals.removeListener("SIGTERM", terminate);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  // Drain queued diagnostics before Node exits; the managed child is closed.
  process.exitCode = await runProduction();
}
