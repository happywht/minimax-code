/**
 * v0.13.0 WS resume e2e — roadmap R13.
 *
 * Before v0.13.0 the WS broadcast was fire-and-forget: any event
 * published while the browser was reconnecting was lost forever
 * (message chunks half-delivered, tool results missing). The agent
 * now stamps every broadcast with a monotonic `seq`, keeps the last
 * 512 in a history ring, and replays `seq > since` in order after
 * `agent.ready` when a client reconnects with `?since=<last seq>`.
 *
 * Spec (raw WebSocket against the agent — no browser page needed):
 *   1. connect /ws, receive agent.ready (lifecycle frame, no seq)
 *   2. fire an agent.send_message RPC; on the WS observe sequenced
 *      broadcasts, remember the last seq we saw
 *   3. disconnect, let the RPC finish server-side (events land in
 *      the history ring while we're gone)
 *   4. reconnect with ?since=<lastSeq>
 *   5. assert we receive agent.ready followed by replayed frames
 *      with seq > lastSeq in strictly increasing order
 *
 * The mock LLM (MINIMAX_API_KEY="" in global setup) streams in
 * 16-char chunks, guaranteeing a multi-frame sequenced burst.
 */
import { test, expect } from "@playwright/test";
import { AGENT_BASE } from "./runtime-config";

type WsFrame = {
  method?: string;
  seq?: number;
  params?: Record<string, unknown>;
};

function wsUrl(since?: number): string {
  const base = `${AGENT_BASE.replace(/^http/, "ws")}/ws`;
  return since === undefined ? base : `${base}?since=${since}`;
}

/** Open a WebSocket and resolve once it's open. */
function openWs(url: string): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    ws.onopen = () => resolve(ws);
    ws.onerror = () => reject(new Error(`WS failed to open: ${url}`));
  });
}

/** Collect frames; resolve when `until(frame)` returns true. */
function nextFrames(ws: WebSocket, until: (f: WsFrame) => boolean, timeoutMs = 10_000): Promise<WsFrame[]> {
  return new Promise((resolve, reject) => {
    const frames: WsFrame[] = [];
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`timed out after ${timeoutMs}ms with ${frames.length} frames`));
    }, timeoutMs);
    const onMessage = (ev: MessageEvent) => {
      let frame: WsFrame;
      try {
        frame = JSON.parse(String(ev.data)) as WsFrame;
      } catch {
        return;
      }
      frames.push(frame);
      if (until(frame)) {
        cleanup();
        resolve(frames);
      }
    };
    const cleanup = () => {
      clearTimeout(timer);
      ws.removeEventListener("message", onMessage);
    };
    ws.addEventListener("message", onMessage);
  });
}

function closeWs(ws: WebSocket): Promise<void> {
  return new Promise((resolve) => {
    ws.onclose = () => resolve();
    ws.close();
  });
}

test("ws resume: reconnect with ?since replays missed sequenced events", async ({ request }) => {
  // 1. First connection — agent.ready is the lifecycle handshake.
  const ws1 = await openWs(wsUrl());
  const ready1 = await nextFrames(ws1, (f) => f.method === "agent.ready");
  expect(ready1[0].method).toBe("agent.ready");
  expect(ready1[0].seq).toBeUndefined();

  // 2. Fire a chat RPC without awaiting it; watch the WS for the
  //    sequenced broadcast stream. Stop at the first chunk frame and
  //    record the highest seq observed so far.
  const rpcPromise = request.post(`${AGENT_BASE}/rpc`, {
    headers: { "Content-Type": "application/json" },
    data: {
      jsonrpc: "2.0",
      id: "ws-resume-1",
      method: "agent.send_message",
      params: { content: "ws resume e2e probe" },
    },
  });

  const streamed = await nextFrames(
    ws1,
    (f) => f.method === "agent.message_chunk" && typeof f.seq === "number",
  );
  const lastSeq = Math.max(...streamed.map((f) => f.seq ?? 0));
  expect(lastSeq).toBeGreaterThanOrEqual(1);

  // 3. Drop the connection mid-stream and let the RPC finish while
  //    we're disconnected — those events land only in the history ring.
  await closeWs(ws1);
  const rpcResp = await rpcPromise;
  expect(rpcResp.status()).toBe(200);
  // Give the server a beat to finish persisting/broadcasting.
  await new Promise((r) => setTimeout(r, 300));

  // 4. Reconnect with the resume cursor.
  const ws2 = await openWs(wsUrl(lastSeq));
  const resumed = await nextFrames(ws2, (f) => f.method === "agent.message_chunk" && typeof f.seq === "number");

  // 5. agent.ready came first (lifecycle frames are never replayed),
  //    then every replayed frame carries seq > lastSeq, strictly
  //    increasing — the client-side store can apply them in order.
  expect(resumed[0].method).toBe("agent.ready");
  expect(resumed[0].seq).toBeUndefined();
  const replays = resumed.slice(1);
  expect(replays.length).toBeGreaterThanOrEqual(1);
  for (const f of replays) {
    expect(f.seq ?? 0).toBeGreaterThan(lastSeq);
  }
  const seqs = replays.map((f) => f.seq ?? 0);
  for (let i = 1; i < seqs.length; i += 1) {
    expect(seqs[i]).toBeGreaterThan(seqs[i - 1]);
  }

  await closeWs(ws2);
});
