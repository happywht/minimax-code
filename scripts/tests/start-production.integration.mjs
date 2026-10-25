// Real process ownership, using a synthetic Python package and no model calls.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { copyFile, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createServer } from "node:net";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const launcher = new URL("../start-production.mjs", import.meta.url).href;
const python = process.env.MINIMAX_START_TEST_PYTHON || path.join(root, "agent", ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(read, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const result = await read(); if (result) return result; await sleep(40); }
  throw new Error("Process evidence did not arrive before the deadline");
}
function testEnvironment(extra) {
  const safe = new Set(["PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "TMPDIR", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LANG", "LC_ALL"]);
  return { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => safe.has(key.toUpperCase()))),
    MINIMAX_CODE_PYTHON: python, MINIMAX_CODE_FORCE_MOCK: "1", MINIMAX_API_KEY: "",
    PYTHON_KEYRING_BACKEND: "keyring.backends.null.Keyring", ...extra };
}
function launch(repo, env, options = {}) {
  const { cli, ...runtimeOptions } = options;
  const program = `import {runProduction} from ${JSON.stringify(launcher)};
    process.on('message', (message) => {if (message === 'stop') process.emit('SIGTERM');});
    process.exitCode = await runProduction({root:${JSON.stringify(repo)},...${JSON.stringify(runtimeOptions)}});
    if (process.connected) process.disconnect();`;
  const args = cli ? [path.join(repo, "scripts", "start-production.mjs")] : ["--input-type=module", "-e", program];
  const proc = spawn(process.execPath, args, {
    cwd: repo, env: testEnvironment(env), shell: false, windowsHide: true,
    stdio: ["ignore", "pipe", "pipe", "ipc"],
  });
  let output = "";
  for (const stream of [proc.stdout, proc.stderr]) stream.on("data", (data) => { output = (output + data).slice(-32768); });
  const closed = new Promise((resolve, reject) => { proc.once("error", reject); proc.once("close", (code, signal) => resolve({code, signal})); });
  return { proc, closed, output: () => output };
}
async function fixture(t, broken = false) {
  const dir = await mkdtemp(path.join(tmpdir(), "minimax-start-process-"));
  const packageDir = path.join(dir, "agent", "minimax_code");
  await mkdir(packageDir, { recursive: true }); await mkdir(path.join(dir, "web", "dist"), { recursive: true });
  await writeFile(path.join(dir, "web", "dist", "index.html"), "prepared");
  await writeFile(path.join(packageDir, "__init__.py"), "");
  await writeFile(path.join(packageDir, "app.py"), "");
  await writeFile(path.join(packageDir, "http_server.py"), broken ? "raise ImportError('synthetic missing dependency')" : "");
  await copyFile(path.join(root, "agent", "minimax_code", "_windows_process_host.py"),
    path.join(packageDir, "_windows_process_host.py"));
  await writeFile(path.join(packageDir, "_test_child.py"), `
import os, time
from pathlib import Path
p = Path(os.environ['START_TEST_HEARTBEAT'])
while True:
    with p.open('a') as f: f.write('beat'); f.flush()
    time.sleep(0.04)
`);
  await writeFile(path.join(packageDir, "__main__.py"), `
import os, sys, time, subprocess
from pathlib import Path
child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('_test_child.py'))])
Path(os.environ['START_TEST_MARKER']).write_text(str(child.pid))
while True: time.sleep(1)
`);
  if (broken === "gil") await writeFile(path.join(packageDir, "http_server.py"), `
import os, sys, subprocess, ctypes
from pathlib import Path
child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('_test_child.py'))])
Path(os.environ['START_TEST_MARKER']).write_text(str(child.pid))
# PyDLL holds the GIL: https://docs.python.org/3.12/library/ctypes.html#ctypes.PyDLL
ctypes.PyDLL('kernel32').Sleep(60000)
`);
  return { dir, marker: path.join(dir, "child.pid"), heartbeat: path.join(dir, "heartbeat") };
}

for (const mode of ["import-native-block-parent-exit", "import-native-block-timeout"]) {
  test(`Windows ${mode} reaps the blocked importer and descendant`, {
    timeout: 15000, skip: process.platform === "win32" ? false : "Windows Job ownership scenario",
  }, async (t) => {
    const f = await fixture(t, "gil");
    const child = launch(f.dir, { START_TEST_MARKER: f.marker, START_TEST_HEARTBEAT: f.heartbeat },
      mode.endsWith("timeout") ? { importTimeout: 2000 } : {});
    cleanup(t, f, child);
    await until(async () => (await stat(f.heartbeat).catch(() => null))?.size > 0);
    if (mode.endsWith("parent-exit")) child.proc.kill("SIGKILL");
    const outcome = await child.closed;
    if (mode.endsWith("timeout")) {
      assert.equal(outcome.code, 1);
      assert.match(child.output(), /dependency check timed out/);
    }
    await sleep(300);
    const size = (await stat(f.heartbeat)).size;
    await sleep(300);
    assert.equal((await stat(f.heartbeat)).size, size);
  });
}

function cleanup(t, fixture, child) {
  t.after(async () => {
    child.proc.kill("SIGKILL");
    await child.closed;
    await sleep(300);
    await rm(fixture.dir, { recursive: true, force: true, maxRetries: 4, retryDelay: 100 });
  });
}

test("real interpreter import failure exits before the service starts", { timeout: 20000 }, async (t) => {
  await stat(python);
  const f = await fixture(t, true), child = launch(f.dir, {});
  cleanup(t, f, child);
  assert.equal((await child.closed).code, 1, child.output());
  assert.match(child.output(), /Python dependency check failed/);
  assert.match(child.output(), /synthetic missing dependency/);
  await assert.rejects(stat(f.marker), /ENOENT/);
});

test("real CLI drains final stdout and stderr before a successful exit", { timeout: 20000 }, async (t) => {
  const f = await fixture(t);
  await mkdir(path.join(f.dir, "scripts"));
  await copyFile(fileURLToPath(new URL("../start-production.mjs", import.meta.url)),
    path.join(f.dir, "scripts", "start-production.mjs"));
  await writeFile(path.join(f.dir, "agent", "minimax_code", "__main__.py"), `
import sys
sys.stdout.write('o' * 1048576 + 'stdout-end-marker\\n'); sys.stdout.flush()
sys.stderr.write('e' * 1048576 + 'stderr-end-marker\\n'); sys.stderr.flush()
`);
  const child = launch(f.dir, {}, { cli: true });
  cleanup(t, f, child);
  const received = { stdout: { bytes: 0, tail: "" }, stderr: { bytes: 0, tail: "" } };
  for (const name of ["stdout", "stderr"]) child.proc[name].on("data", (chunk) => {
    received[name].bytes += chunk.length;
    received[name].tail = (received[name].tail + chunk.toString()).slice(-128);
  });
  assert.equal((await child.closed).code, 0, child.output());
  for (const name of ["stdout", "stderr"]) {
    assert.ok(received[name].bytes >= 1048576, `${name} output was truncated`);
    assert.match(received[name].tail, new RegExp(`${name}-end-marker\\r?\\n$`));
  }
});

for (const mode of ["signal-handler", "abrupt-parent-exit"]) {
  test(`real ${mode} stops the managed heartbeat descendant`, { timeout: 20000 }, async (t) => {
    await stat(python);
    const f = await fixture(t);
    const child = launch(f.dir, { START_TEST_MARKER: f.marker, START_TEST_HEARTBEAT: f.heartbeat });
    cleanup(t, f, child);
    await until(async () => (await stat(f.heartbeat).catch(() => null))?.size > 0).catch((e) => { throw new Error(`${e.message}\n${child.output()}`); });
    if (mode === "signal-handler") child.proc.send("stop"); else child.proc.kill("SIGKILL");
    const outcome = await child.closed;
    if (mode === "signal-handler") assert.equal(outcome.code, 143, child.output());
    await sleep(300);
    const size = (await stat(f.heartbeat)).size;
    await sleep(300);
    assert.equal((await stat(f.heartbeat)).size, size, "heartbeat continued after launcher exit");
    assert.ok(Number(await readFile(f.marker, "utf8")) > 0);
  });
}

for (const cli of [false, true]) test(`prepared real Agent ${cli ? "CLI entry" : "supervisor"} serves health, SPA and authenticated RPC`, {
  timeout: 30000,
  skip: process.env.MINIMAX_START_TEST_SOURCE ? false : "Set MINIMAX_START_TEST_SOURCE to an isolated prepared source tree",
}, async (t) => {
  const source = path.resolve(process.env.MINIMAX_START_TEST_SOURCE);
  if (cli) {
    await mkdir(path.join(source, "scripts"), { recursive: true });
    await copyFile(fileURLToPath(new URL("../start-production.mjs", import.meta.url)),
      path.join(source, "scripts", "start-production.mjs"));
  }
  const dir = await mkdtemp(path.join(tmpdir(), "minimax-start-health-"));
  const workspace = path.join(dir, "workspace"); await mkdir(workspace);
  const socket = createServer();
  await new Promise((resolve) => socket.listen(0, "127.0.0.1", resolve));
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  const token = "synthetic-production-start-auth";
  const startedAt = performance.now();
  const child = launch(source, { MINIMAX_CODE_DATA_DIR: path.join(dir, "data"),
    MINIMAX_CODE_WORKSPACE: workspace, MINIMAX_CODE_HTTP_PORT: String(port),
    MINIMAX_CODE_HTTP_HOST: "127.0.0.1", MINIMAX_CODE_AUTH_TOKEN: token,
    MINIMAX_CODE_LOG_LEVEL: "WARNING" }, { cli });
  cleanup(t, { dir }, child);
  const base = `http://127.0.0.1:${port}`;
  const health = await until(async () => {
    try {
      const data = await (await fetch(`${base}/health`, { signal: AbortSignal.timeout(500) })).json();
      return data.ok && data.web && data.db ? data : null;
    } catch { return null; }
  }, 20000).catch((e) => { throw new Error(`${e.message}\n${child.output()}`); });
  assert.equal(health.ok, true);
  t.diagnostic(`launcher_to_healthy_ms=${(performance.now() - startedAt).toFixed(1)}`);
  const page = await fetch(base);
  assert.match(page.headers.get("content-type"), /text\/html/);
  assert.match(await page.text(), /<html/i);
  const request = { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: "startup", method: "ping", params: {} }) };
  assert.equal((await fetch(`${base}/rpc`, request)).status, 401);
  const response = await fetch(`${base}/rpc`, { ...request,
    headers: { ...request.headers, Authorization: `Bearer ${token}` } });
  assert.equal(response.status, 200);
  assert.ok("result" in await response.json());
  if (cli) child.proc.kill("SIGKILL"); else child.proc.send("stop");
  const closed = await child.closed;
  if (!cli) assert.equal(closed.code, 143, child.output());
  await until(async () => {
    try { await fetch(`${base}/health`, { signal: AbortSignal.timeout(500) }); return false; }
    catch { return true; }
  }, 3000);
});
