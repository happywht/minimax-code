function readPort(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const port = Number.parseInt(raw, 10);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error(`${name} must be a valid TCP port, received ${JSON.stringify(raw)}`);
  }
  return port;
}

// Keep browser tests away from the ports used by normal development and the
// single-process personal product. Both values remain configurable for CI.
export const AGENT_PORT = readPort("MINIMAX_CODE_E2E_PORT", 18_765);
export const WEB_PORT = readPort("MINIMAX_CODE_E2E_WEB_PORT", 15_173);
export const AGENT_BASE = `http://127.0.0.1:${AGENT_PORT}`;
export const WEB_BASE = `http://127.0.0.1:${WEB_PORT}`;
