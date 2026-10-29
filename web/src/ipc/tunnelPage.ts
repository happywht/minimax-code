/** Navigation metadata contains only a loopback page origin, never credentials. */
function loopbackOrigin(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(url.hostname) ||
        url.username || url.password || url.pathname !== "/" || url.search || url.hash) return null;
    return url.origin;
  } catch { return null; }
}

export function tunnelPage(): { returnOrigin: string | null } | null {
  if (typeof window === "undefined" || !loopbackOrigin(window.location.origin)) return null;
  const params = new URLSearchParams(window.location.hash.slice(1));
  if (params.get("mm-tunnel") !== "1") return null;
  const returnOrigin = loopbackOrigin(params.get("return") ?? "");
  return { returnOrigin: returnOrigin !== window.location.origin ? returnOrigin : null };
}

export function tunnelPageUrl(url: string): string {
  const params = new URLSearchParams({ "mm-tunnel": "1" });
  // A nested tunnel page needs its own launch metadata; do not offer a bare
  // origin that could reconnect that page to a different configured service.
  if (typeof window !== "undefined" && !tunnelPage()) {
    const origin = loopbackOrigin(window.location.origin);
    if (origin) params.set("return", origin);
  }
  return `${url}#${params.toString()}`;
}
