/** Use one loopback hostname so HttpOnly SameSite cookies reach local previews. */
export function localBrowserOrigin(base: string): string {
  if (typeof window === "undefined") return base;
  const loopback = new Set(["localhost", "127.0.0.1"]);
  try {
    const target = new URL(base);
    if (loopback.has(target.hostname) && loopback.has(window.location.hostname) &&
        target.protocol === window.location.protocol) {
      target.hostname = window.location.hostname;
      return target.toString().replace(/\/$/, "");
    }
  } catch {
    // Let the caller's normal connection diagnostics handle invalid input.
  }
  return base;
}
