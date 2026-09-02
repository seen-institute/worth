/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the API lives. Defaults to `/api` on the same origin. */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
