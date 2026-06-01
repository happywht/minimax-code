import { describe, expect, it } from "vitest";
import {
  ErrorCode,
  type JsonRpcError,
  type JsonRpcRequest,
  type JsonRpcResponse,
} from "../src/types/ipc";

describe("ipc type contracts", () => {
  it("parses a request envelope", () => {
    const r: JsonRpcRequest = {
      jsonrpc: "2.0",
      id: "abc",
      method: "ping",
      params: { foo: 1 },
    };
    expect(r.id).toBe("abc");
    expect(r.method).toBe("ping");
  });

  it("describes a response envelope with result", () => {
    const r: JsonRpcResponse<{ pong: number }> = {
      jsonrpc: "2.0",
      id: "abc",
      result: { pong: 1234 },
    };
    expect(r.result?.pong).toBe(1234);
  });

  it("describes a response envelope with error", () => {
    const e: JsonRpcError = {
      code: ErrorCode.MethodNotFound,
      message: "no such method",
    };
    const r: JsonRpcResponse = { jsonrpc: "2.0", id: "x", error: e };
    expect(r.error?.code).toBe(-32601);
  });
});
