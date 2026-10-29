/** Mock-only cursor binding, matching the scope semantics of the real service. */
export function metadataCursor(scope: unknown[], id: string): string {
  const bytes = new TextEncoder().encode(JSON.stringify([...scope, id]));
  return `q_${btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")}`;
}

export function metadataAnchor(cursor: unknown, scope: unknown[], query: unknown): string | null {
  if (cursor == null) return null;
  if (typeof cursor !== "string") throw new Error("Invalid cursor");
  if (query == null) return cursor;
  try {
    if (!cursor.startsWith("q_")) throw new Error();
    const encoded = cursor.slice(2).replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(encoded + "=".repeat((4 - encoded.length % 4) % 4));
    const data: unknown = JSON.parse(new TextDecoder().decode(Uint8Array.from(binary, (c) => c.charCodeAt(0))));
    if (!Array.isArray(data) || data.length !== scope.length + 1 ||
        JSON.stringify(data.slice(0, -1)) !== JSON.stringify(scope) || typeof data[data.length - 1] !== "string") throw new Error();
    return data[data.length - 1] as string;
  } catch { throw new Error("Invalid cursor for this scope"); }
}
