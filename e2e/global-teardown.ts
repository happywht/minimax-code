/**
 * Playwright globalTeardown — kill the agent spawned by globalSetup.
 *
 * Reads pid from e2e/.runtime/agent.json and kills the process.
 * Uses SIGTERM first (works on POSIX), then `taskkill /F /T` on
 * Windows to handle process trees (uv -> python -> minimax_code).
 *
 * Tolerates missing/malformed pid file (test run might have crashed
 * before globalSetup completed).
 */
import { readFileSync, existsSync, unlinkSync } from "node:fs";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { execSync } from "node:child_process";
import { AGENT_PORT } from "./runtime-config";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const RUNTIME_DIR = resolve(__dirname, ".runtime", String(AGENT_PORT));
const PID_FILE = join(RUNTIME_DIR, "agent.json");

function isWindows() {
  return process.platform === "win32";
}

function killTree(pid: number): void {
  if (!pid || Number.isNaN(pid)) return;
  try {
    if (isWindows()) {
      // /T = terminate process tree (uv spawns python which spawns minimax_code).
      // /F = force. Ignore non-zero exit (process might already be gone).
      execSync(`taskkill /PID ${pid} /T /F`, { stdio: "ignore" });
    } else {
      try {
        process.kill(pid, "SIGTERM");
      } catch {
        // already dead
      }
      // Escalate to SIGKILL after a short grace period.
      setTimeout(() => {
        try {
          process.kill(pid, "SIGKILL");
        } catch {
          // ignore
        }
      }, 500);
    }
    console.log(`[global-teardown] killed agent pid=${pid}`);
  } catch (err) {
    console.warn(`[global-teardown] could not kill pid=${pid}: ${err}`);
  }
}

export default async function globalTeardown(): Promise<void> {
  if (!existsSync(PID_FILE)) {
    console.log("[global-teardown] no pid file, nothing to kill");
    return;
  }
  let pid: number | null = null;
  try {
    const data = JSON.parse(readFileSync(PID_FILE, "utf-8")) as { pid?: number };
    pid = data.pid ?? null;
  } catch (err) {
    console.warn(`[global-teardown] could not parse pid file: ${err}`);
  }
  if (pid) killTree(pid);
  try {
    unlinkSync(PID_FILE);
  } catch {
    // best effort
  }
}
