import { afterEach, expect, it, vi } from "vitest";
import { localBrowserOrigin } from "../localOrigin";

afterEach(() => vi.unstubAllGlobals());

it("keeps local previews on the browser hostname without changing the service port", () => {
  vi.stubGlobal("window", { location: { hostname: "localhost", protocol: "http:" } });
  expect(localBrowserOrigin("http://127.0.0.1:8766")).toBe("http://localhost:8766");
  vi.stubGlobal("window", { location: { hostname: "127.0.0.1", protocol: "http:" } });
  expect(localBrowserOrigin("http://localhost:8765")).toBe("http://127.0.0.1:8765");
});

it("preserves remote origins and explicit protocol boundaries", () => {
  vi.stubGlobal("window", { location: { hostname: "localhost", protocol: "http:" } });
  expect(localBrowserOrigin("https://remote.example:9443")).toBe("https://remote.example:9443");
  expect(localBrowserOrigin("https://127.0.0.1:8765")).toBe("https://127.0.0.1:8765");
});
