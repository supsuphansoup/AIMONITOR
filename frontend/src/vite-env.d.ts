/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the AIMONITOR API. Defaults to the same-origin `/api/v1`. */
  readonly VITE_API_BASE?: string;
  /** Build-time API key. Prefer entering it in the dashboard settings instead:
   *  anything baked in here ships to every visitor in the bundle. */
  readonly VITE_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
