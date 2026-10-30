import type { ContentPartImage } from "../types/ipc";

export const CODEX_IMAGE_HANDOFF = "minimax:codex-image-handoff";
export interface CodexImageHandoff {
  host: string;
  session: string;
  project: string | null;
  image: ContentPartImage;
  result?: string;
}
