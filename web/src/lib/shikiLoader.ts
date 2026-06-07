/**
 * Lazy loader for shiki — defers the heavy syntax-highlighting bundle
 * until the first code block actually needs rendering.
 *
 * Before: static `import { codeToHtml } from "shiki"` added ~400 KB
 * to the initial page load (all bundled languages included).
 *
 * After: dynamic `import("shiki")` fires only when `highlight()` is
 * called for the first time. The promise is cached so subsequent
 * calls resolve instantly.
 *
 * Usage:
 *   const html = await highlight(code, "python");
 *   // html = "<pre ...>...</pre>"  or  null on error / unsupported lang
 */

// Module-level cache — resolved once, reused forever.
let shikiPromise: Promise<typeof import("shiki")> | null = null;

function loadShiki(): Promise<typeof import("shiki")> {
  if (!shikiPromise) {
    shikiPromise = import("shiki");
  }
  return shikiPromise;
}

/**
 * Check whether a language identifier is supported by shiki's
 * bundled language set. Triggers the dynamic import on first call.
 */
export async function isLanguageSupported(lang: string): Promise<boolean> {
  const { bundledLanguages } = await loadShiki();
  return lang in bundledLanguages;
}

/**
 * Highlight a code string to HTML using shiki.
 *
 * Returns the highlighted HTML string, or `null` if the language
 * is unsupported or highlighting fails (caller should fall back
 * to a plain `<pre>` block).
 */
export async function highlight(
  code: string,
  lang: string,
  theme: string = "github-dark",
): Promise<string | null> {
  try {
    const shiki = await loadShiki();
    if (!(lang in shiki.bundledLanguages)) return null;
    return await shiki.codeToHtml(code, { lang, theme });
  } catch {
    return null;
  }
}

/**
 * Preload shiki (optional). Call when the user opens a chat
 * or hovers a conversation that likely contains code blocks.
 * Does nothing if already loaded.
 */
export function preloadShiki(): void {
  void loadShiki();
}
