/**
 * Presentation mapping for interview status.
 *
 * The API status is an enum meant for logic, not for reading: rendering it raw
 * produces "SCORECARD READY". This maps each value to a human label and one of
 * four pill variants. The two states a user must notice mid-session — live and
 * failed — are the ones the pill marks with a dot, so state survives
 * colour-blindness and dark mode.
 */
export type StatusVariant = "neutral" | "live" | "work" | "done" | "fail";

export interface StatusPill {
  label: string;
  variant: StatusVariant;
}

const STATUS_PILLS: Record<string, StatusPill> = {
  DRAFT: { label: "Draft", variant: "neutral" },
  PROFILE_READY: { label: "Profile ready", variant: "work" },
  SCORECARD_READY: { label: "Scorecard ready", variant: "work" },
  CONNECTING: { label: "Connecting", variant: "live" },
  IN_PROGRESS: { label: "In progress", variant: "live" },
  RECONNECTING: { label: "Reconnecting", variant: "fail" },
  FAILED_RECOVERABLE: { label: "Needs attention", variant: "fail" },
  TRANSCRIPT_FINALIZING: { label: "Finalizing", variant: "work" },
  EVALUATING: { label: "Evaluating", variant: "work" },
  REPORT_READY: { label: "Report ready", variant: "done" },
};

/**
 * An unknown status still renders readably rather than disappearing, so a
 * server-side status added later degrades to a neutral pill instead of a gap.
 */
export function statusPill(status: string): StatusPill {
  return (
    STATUS_PILLS[status] ?? {
      label: status.replaceAll("_", " ").toLowerCase(),
      variant: "neutral",
    }
  );
}

export function statusPillClass(status: string): string {
  const { variant } = statusPill(status);
  return variant === "neutral" ? "badge" : `badge badge--${variant}`;
}
