#!/usr/bin/env node
/** Reproducible offline checks. No live-model option is accepted here. */
import { spawn, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createWriteStream, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const suites = JSON.parse(readFileSync(new URL("verification-suites.json", import.meta.url), "utf8"));
const args = process.argv.slice(2);
const suite = args.find((arg) => arg.startsWith("--suite="))?.split("=")[1] ?? "baseline";
const repeat = Number(args.find((arg) => arg.startsWith("--repeat="))?.split("=")[1] ?? 1);
const allowed = new Set(["baseline", "core", "replay", "full"]);
if (!allowed.has(suite) || !Number.isInteger(repeat) || repeat < 1 || repeat > 3 ||
    args.some((arg) => !/^--(suite|repeat)=/.test(arg))) {
  throw new Error("Usage: node scripts/verify.mjs --suite=baseline|core|replay|full --repeat=1|2|3");
}
for (const files of Object.values(suites)) {
  for (const file of files) {
    if (!existsSync(join(root, "agent", file))) throw new Error(`Missing suite member: ${file}`);
  }
}

function git(...gitArgs) {
  return execFileSync("git", gitArgs, { cwd: root, encoding: "utf8", windowsHide: true }).trim();
}

function sourceHash() {
  const hash = createHash("sha256");
  const paths = execFileSync("git", ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
    { cwd: root, encoding: "utf8", windowsHide: true }).split("\0").filter(Boolean).sort();
  for (const path of paths) {
    hash.update(path).update("\0");
    hash.update(existsSync(join(root, path)) ? readFileSync(join(root, path)) : "<deleted>");
  }
  return hash.digest("hex");
}

const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const output = join(root, ".verification", `${stamp}-${suite}`);
mkdirSync(output, { recursive: true });
const env = { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1",
  PYTHON_KEYRING_BACKEND: "keyring.backends.null.Keyring" };
for (const key of Object.keys(env)) {
  if (/(?:API_?KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET|PASSWORD)$/i.test(key) ||
      /^(MINIMAX_CODE_|VITE_AGENT_)/i.test(key)) delete env[key];
}
// An isolated product data directory, including for subprocess-based tests.
env.MINIMAX_CODE_DATA_DIR = join(tmpdir(), `minimax-verify-${stamp}-${process.pid}`);
const metadata = { suite, repeat, commit: git("rev-parse", "HEAD"),
  dirty: Boolean(git("status", "--porcelain")), sourceHash: sourceHash(),
  platform: process.platform, node: process.version, startedAt: new Date().toISOString(),
  mode: "offline", results: [] };
const report = () => writeFileSync(join(output, "results.json"), JSON.stringify(metadata, null, 2) + "\n");
report();
console.log(`Offline verification: ${suite}, ${repeat} pass(es). Logs: ${output}`);

async function run(name, command, commandArgs, cwd = root) {
  const logPath = join(output, `${metadata.results.length + 1}-${name}.log`);
  const log = createWriteStream(logPath);
  const start = Date.now();
  let tail = "";
  console.log(`Running ${name} ...`);
  const code = await new Promise((done) => {
    // pnpm's Windows .cmd wrapper needs cmd; names/arguments here are fixed
    // program-owned values, never interpolated user command strings.
    const child = spawn(command, commandArgs, { cwd, env, windowsHide: true,
      shell: process.platform === "win32" && command === "pnpm", stdio: ["ignore", "pipe", "pipe"] });
    for (const stream of [child.stdout, child.stderr]) stream.on("data", (data) => {
      log.write(data);
      tail = (tail + data.toString()).slice(-8000);
    });
    child.on("error", (error) => { log.write(String(error)); tail += String(error); });
    child.on("close", (exitCode) => done(exitCode ?? 1));
  });
  await new Promise((done) => log.end(done));
  metadata.results.push({ name, command: [command, ...commandArgs], exitCode: code,
    durationMs: Date.now() - start, log: logPath });
  report();
  console.log(`${code === 0 ? "PASS" : "FAIL"} ${name} (${((Date.now() - start) / 1000).toFixed(1)}s)`);
  if (code !== 0) {
    console.error(tail);
    throw new Error(`Verification failed: ${name}. See ${logPath}`);
  }
}

async function pythonSuite(name, files) {
  await run(name, "uv", ["run", "--frozen", "--extra", "dev", "python", "-m", "pytest", "-q",
    ...files, "--junitxml", join(output, `${name}.xml`)], join(root, "agent"));
}

try {
  if (suite === "baseline") {
    await run("lint", "pnpm", ["lint"]);
    await run("python-lint", "uv", ["run", "--frozen", "--extra", "dev", "ruff", "check", "minimax_code", "tests"], join(root, "agent"));
    await run("build", "pnpm", ["build"]);
  }
  for (let pass = 1; pass <= repeat; pass++) {
    if (sourceHash() !== metadata.sourceHash) throw new Error("Source changed during verification; rerun on a stable tree");
    if (suite === "baseline") await run(`web-${pass}`, "pnpm", ["test"]);
    if (suite === "core" || suite === "baseline") await pythonSuite(`core-${pass}`, suites.core);
    if (suite === "replay" || suite === "baseline") await pythonSuite(`replay-${pass}`, suites.replay);
    if (suite === "full") await pythonSuite(`full-${pass}`, []);
    if (suite === "baseline") await run(`e2e-${pass}`, "pnpm", ["test:e2e"]);
  }
  if (sourceHash() !== metadata.sourceHash) throw new Error("Source changed during verification; results are not a stable baseline");
  metadata.status = "passed";
} catch (error) {
  metadata.status = "failed";
  metadata.error = String(error);
  console.error(metadata.error);
  process.exitCode = 1;
} finally {
  metadata.finishedAt = new Date().toISOString();
  report();
  console.log(`Report: ${join(output, "results.json")}`);
}
