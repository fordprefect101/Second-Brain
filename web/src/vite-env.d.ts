/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Override the API base URL. Defaults to http://localhost:8000. */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
