import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import { test } from "node:test";
import { productionConfig, runProduction } from "../start-production.mjs";

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), "minimax-start-unit-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "web", "dist"), { recursive: true });
  await mkdir(path.join(root, "agent", "minimax_code"), { recursive: true });
  await writeFile(path.join(root, "web", "dist", "index.html"), "prepared frontend");
  await writeFile(path.join(root, "agent", "minimax_code", "_windows_process_host.py"), "# fixture");
  return { root, env: { MINIMAX_CODE_PYTHON: process.execPath } };
}

function launcher(exitCode = 0, probeCode = 0, invalid = false) {
  const calls = [];
  const spawnProcess = (executable, args, options) => {
    calls.push({ executable, args, options });
    const child = new EventEmitter();
    child.stdout = new PassThrough(); child.stderr = new PassThrough();
    child.kill = () => true;
    queueMicrotask(() => {
      if (calls.length === 1) {
        child.stdout.write(JSON.stringify({ launcher_pid: invalid ? 1 : process.pid,
          parent_created: "134000000000000000", base_python: process.execPath }));
        if (probeCode) child.stderr.write("missing installed dependency");
        child.emit("close", probeCode);
      } else {
        child.emit("exit", exitCode, null);
        // A process may exit before its last output/marker reaches the pipe.
        setImmediate(() => {
          child.stdout.end(args.at(-1) + "\n"); child.stderr.end();
          child.emit("close", exitCode, null);
        });
      }
    });
    return child;
  };
  return { calls, spawnProcess };
}

test("requires prepared assets before launching an interpreter", async (t) => {
  const config = await fixture(t);
  await rm(path.join(config.root, "web", "dist", "index.html"));
  const runner = launcher();
  const messages = [];
  assert.equal(await runProduction({ ...config, ...runner, log: (m) => messages.push(m) }), 1);
  assert.equal(runner.calls.length, 0);
  assert.match(messages[0], /pnpm build/);
});

test("requires an installed absolute interpreter without installing it", async (t) => {
  const config = await fixture(t);
  await assert.rejects(productionConfig({ ...config, env: { MINIMAX_CODE_PYTHON: "python" } }), /absolute/);
  await assert.rejects(productionConfig({ ...config,
    env: { MINIMAX_CODE_PYTHON: path.join(config.root, "missing-python") } }), /uv sync/);
});

test("resolves explicit assets/workspace and custom uv environment consistently", async (t) => {
  const config = await fixture(t);
  const python = path.join(config.root, "prepared-env", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  await mkdir(path.dirname(python), { recursive: true }); await writeFile(python, "installed");
  const resolved = await productionConfig({ root: config.root, cwd: config.root, env: {
    UV_PROJECT_ENVIRONMENT: "../prepared-env", MINIMAX_CODE_WEB_DIST: "../web/dist",
    MINIMAX_CODE_WORKSPACE: "web", MINIMAX_CODE_ENV: "development" } });
  assert.equal(resolved.python, python);
  assert.equal(resolved.workspace, path.join(config.root, "web"));
  assert.equal(resolved.env.MINIMAX_CODE_ENV, "production");
  assert.equal(resolved.env.MINIMAX_CODE_WEB_DIST, path.join(config.root, "web", "dist"));
  await assert.rejects(productionConfig({ ...config,
    env: { ...config.env, MINIMAX_CODE_WORKSPACE: path.join(config.root, "gone") } }), /Workspace does not exist/);
});

test("interpreter check failure and invalid identity never launch the agent", async (t) => {
  const config = await fixture(t);
  for (const runner of [launcher(0, 2), launcher(0, 0, true)]) {
    assert.equal(await runProduction({ ...config, ...runner, log: () => {} }), 1);
    assert.equal(runner.calls.length, 1);
  }
});

test("waits for the final import marker and preserves the agent exit status", async (t) => {
  const config = await fixture(t), runner = launcher(23);
  const messages = [];
  assert.equal(await runProduction({ ...config, ...runner, log: (m) => messages.push(m) }), 23);
  assert.equal(runner.calls.length, 2);
  assert.deepEqual(runner.calls[0].args.slice(0, 3), ["-I", "-S", "-c"]);
  assert.doesNotMatch(runner.calls[0].args[3], /import minimax_code\./);
  const agent = runner.calls[1];
  assert.equal(agent.options.cwd, path.join(config.root, "agent"));
  assert.equal(agent.options.shell, false); assert.equal(agent.options.windowsHide, true);
  assert.equal(agent.options.env.MINIMAX_CODE_WORKSPACE, config.root);
  assert.match(agent.args[agent.args.indexOf("-c") + 1], /runpy.run_module\("minimax_code"/);
  assert.equal(messages.some((m) => /ready|listening|started/.test(m)), false);
  const successful = launcher();
  assert.equal(await runProduction({ ...config, ...successful, log: () => {} }), 0,
    "a marker delivered after exit but before close must still confirm imports");
});

test("spawn errors and interpreter timeouts return failure and remove signal handlers", async (t) => {
  const config = await fixture(t);
  for (const timeout of [false, true]) {
    const signals = new EventEmitter();
    const spawnProcess = () => {
      const child = new EventEmitter(); child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.kill = () => true;
      if (!timeout) queueMicrotask(() => child.emit("error", new Error("ENOENT")));
      return child;
    };
    assert.equal(await runProduction({ ...config, spawnProcess, signals, log: () => {}, probeTimeout: 10 }), 1);
    assert.equal(signals.listenerCount("SIGTERM"), 0);
    assert.equal(signals.listenerCount("SIGINT"), 0);
  }
});
