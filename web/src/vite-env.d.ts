/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AGENT_URL: string;
  readonly VITE_AGENT_MODE: string;
  readonly VITE_PREVIEW_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
