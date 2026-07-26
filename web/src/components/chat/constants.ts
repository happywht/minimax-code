/**
 * Shared constants for the chat composer.
 */

/** Custom event other components fire to prefill the composer. */
export const SUGGESTION_EVENT = "minimax:suggestion";

export const MAX_ATTACHMENTS = 5;
export const MAX_IMAGES = 5;
export const MAX_FILE_CHARS = 32_000;
export const MAX_INPUT_CHARS = 8000;

export const ACCEPTED_EXTS =
  ".txt,.md,.py,.ts,.tsx,.js,.jsx,.json,.yaml,.yml,.toml,.cfg,.sh,.bat,.sql,.html,.css,.csv,.log,.xml,.ini,.env,.gitignore,.editorconfig,.eslintrc,.prettierrc";

export interface AttachedFile {
  name: string;
}
