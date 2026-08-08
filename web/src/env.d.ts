/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Clerk publishable key, inlined at build time. Absent in local development,
   * where the backend runs with AUTH_MODE=local and no sign-in is required.
   */
  readonly VITE_CLERK_PUBLISHABLE_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
