import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("report navigation", () => {
  it("opens a ready evidence report from the session dashboard", async () => {
    const interview = {
      id: "interview-report",
      title: "Backend interview report",
      status: "REPORT_READY",
      profile_id: "profile-1",
      scorecard_id: "scorecard-1",
      duration_minutes: 30,
      interview_type: "technical_behavioral",
      input_mode: "text_dev",
      started_at: "2026-08-07T10:00:00Z",
      ended_at: "2026-08-07T10:30:00Z",
      prompt_version: "interview-v1",
      created_at: "2026-08-07T09:30:00Z",
      updated_at: "2026-08-07T10:32:00Z",
    };

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "user-1",
            email: "developer@local.test",
            display_name: "Local developer",
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [interview] }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            interview_id: interview.id,
            status: "REPORT_READY",
            evaluator_version: "evaluator-v1",
            prompt_version: "evaluation-v1",
            overall_score: null,
            assessed_weight: 0,
            total_weight: 100,
            coverage_percentage: 0,
            competency_results: [],
            strengths: [],
            gaps: [],
            practice_exercises: [],
            uncertainty: ["No candidate evidence was available."],
            completed_at: "2026-08-07T10:32:00Z",
          }),
          { status: 200 },
        ),
      );

    render(<App />);

    await userEvent.click(
      await screen.findByRole("button", { name: "View report" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Backend interview report" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Not assessed")).toBeInTheDocument();
    expect(
      screen.getByText("No candidate evidence was available."),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
