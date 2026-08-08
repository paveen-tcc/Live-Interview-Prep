import { describe, expect, it } from "vitest";

import { statusPill, statusPillClass } from "./status";

describe("interview status pills", () => {
  it("gives every known status a readable label", () => {
    const statuses = [
      "DRAFT",
      "PROFILE_READY",
      "SCORECARD_READY",
      "CONNECTING",
      "IN_PROGRESS",
      "RECONNECTING",
      "FAILED_RECOVERABLE",
      "TRANSCRIPT_FINALIZING",
      "EVALUATING",
      "REPORT_READY",
    ];

    for (const status of statuses) {
      const { label } = statusPill(status);
      expect(label).not.toMatch(/_/);
      expect(label).not.toBe(label.toUpperCase());
    }
  });

  it("maps terminal and in-flight states to distinct variants", () => {
    expect(statusPill("REPORT_READY")).toEqual({
      label: "Report ready",
      variant: "done",
    });
    expect(statusPill("IN_PROGRESS")).toEqual({
      label: "In progress",
      variant: "live",
    });
    expect(statusPill("FAILED_RECOVERABLE")).toEqual({
      label: "Needs attention",
      variant: "fail",
    });
    expect(statusPill("SCORECARD_READY")).toEqual({
      label: "Scorecard ready",
      variant: "work",
    });
  });

  it("emits a variant class only when the pill is not neutral", () => {
    expect(statusPillClass("DRAFT")).toBe("badge");
    expect(statusPillClass("REPORT_READY")).toBe("badge badge--done");
    expect(statusPillClass("IN_PROGRESS")).toBe("badge badge--live");
  });

  it("degrades an unrecognised status instead of rendering nothing", () => {
    expect(statusPill("SOME_FUTURE_STATE")).toEqual({
      label: "some future state",
      variant: "neutral",
    });
    expect(statusPillClass("SOME_FUTURE_STATE")).toBe("badge");
  });
});
