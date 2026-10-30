#!/usr/bin/env node
// Start one installed release with explicit workspace and data binding.
import { lstat, readFile, readdir, realpath, stat } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { runProduction } from "./start-production.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
function argumentsOf(args) {
  const values = { initializeNew: false };
  for (let i = 0; i < args.length; i++) {
    const item = args[i];
    if (item === "--initialize-new") {
      if (values.initializeNew) throw new Error("Duplicate --initialize-new");
      values.initializeNew = true;
    } else if (item === "--data-dir" || item === "--workspace") {
      const key = item === "--data-dir" ? "data" : "workspace";
      if (values[key] || !args[i + 1] || args[i + 1].startsWith("--")) throw new Error(`Missing or duplicate ${item}`);
      values[key] = args[++i];
    } else throw new Error(`Unknown argument: ${item}`);
  }
  if (!values.data || !values.workspace) throw new Error("Specify --data-dir and --workspace as existing absolute directories");
  return values;
}
function overlaps(a, b) {
  const inside = (parent, child) => {
    const relative = path.relative(parent, child);
    return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
  };
  return inside(a, b) || inside(b, a);
}
async function directory(value, name) {
  if (!path.isAbsolute(value)) throw new Error(`${name} must be an absolute existing directory`);
  const resolved = await realpath(value);
  if (!(await stat(resolved)).isDirectory()) throw new Error(`${name} is not a directory`);
  return resolved;
}

try {
  const args = argumentsOf(process.argv.slice(2));
  const release = await realpath(root);
  const complete = await lstat(path.join(release, ".INSTALL-COMPLETE"));
  if (!complete.isFile() || complete.isSymbolicLink()) throw new Error("Release installation is incomplete");
  try { await lstat(path.join(release, ".INSTALL-INCOMPLETE")); throw new Error("Release contains an incomplete installation marker"); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
  const manifestInfo = await lstat(path.join(release, "RELEASE-MANIFEST.json"));
  if (!manifestInfo.isFile() || manifestInfo.isSymbolicLink() || manifestInfo.size > 4 * 1024 * 1024) throw new Error("Release manifest is missing or invalid");
  const manifest = JSON.parse(await readFile(path.join(release, "RELEASE-MANIFEST.json"), "utf8"));
  if (manifest.format !== "minimax-code-release-v1" || typeof manifest.version_label !== "string" || !/^[0-9a-f]{40}$/.test(manifest.commit)) throw new Error("Release manifest identity is invalid");
  const data = await directory(args.data, "Data directory"), workspace = await directory(args.workspace, "Workspace");
  if (overlaps(release, data) || overlaps(release, workspace) || overlaps(data, workspace)) throw new Error("Release, data and workspace directories must not overlap");
  try { await lstat(path.join(data, ".RESTORE-INCOMPLETE")); throw new Error("Snapshot restore is incomplete; do not start this data directory"); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
  const dataEntries = await readdir(data);
  if (args.initializeNew && dataEntries.length) throw new Error("--initialize-new requires an empty data directory");
  if (!args.initializeNew) {
    const db = await lstat(path.join(data, "data.db"));
    if (!db.isFile() || db.isSymbolicLink()) throw new Error("Existing data.db must be a regular file; use --initialize-new only with an empty directory");
  }
  console.error(`Release label: ${manifest.version_label}; commit: ${manifest.commit} (manifest identity is not a signature).`);
  console.error(`Data: ${data} (${args.initializeNew ? "initialize empty directory" : "existing database; schema must match"})`);
  console.error(`Workspace: ${workspace}. No service is switched; startup does not establish readiness.`);
  process.exitCode = await runProduction({ root: release, env: { ...process.env,
    MINIMAX_CODE_DATA_DIR: data, MINIMAX_CODE_WORKSPACE: workspace,
    MINIMAX_CODE_WEB_DIST: path.join(release, "web", "dist"), MINIMAX_CODE_NO_DB: "0",
    MINIMAX_CODE_HTTP_HOST: "127.0.0.1", MINIMAX_CODE_RELEASE_SCHEMA_GUARD: args.initializeNew ? "initialize-new" : "existing" } });
} catch (error) {
  console.error(`Cannot start installed release: ${error.message}`);
  console.error("Prepare dependencies separately. For schema changes, back up and use an explicit upgrade procedure.");
  process.exitCode = 1;
}
