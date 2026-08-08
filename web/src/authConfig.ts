/**
 * Clerk is only wired up when a publishable key was present at build time.
 * Without one the app runs exactly as it did before: the backend is in
 * AUTH_MODE=local and every request is the single local developer.
 */
export const clerkPublishableKey =
  import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim() ?? "";

export const clerkEnabled = Boolean(clerkPublishableKey);
