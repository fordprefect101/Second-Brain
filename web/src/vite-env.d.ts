/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Override the API base URL. Defaults to /api, the dev server's proxy to :8000. */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
