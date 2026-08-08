import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportPage } from "./ReportPage";
import type { InterviewReport, InterviewSession } from "./types";

const readyInterview: InterviewSession = {
  id: "interview-1",
  title: "Backend Engineer practice",
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
  updated_at: "2026-08-07T10:31:00Z",
};

const report: InterviewReport = {
  interview_id: readyInterview.id,
  status: "REPORT_READY",
  evaluator_version: "evaluator-v1",
  prompt_version: "evaluation-v1",
  overall_score: 3.6,
  assessed_weight: 80,
  total_weight: 100,
  coverage_percentage: 80,
  competency_results: [
    {
      competency_id: "competency-1",
      name: "API design",
      weight: 60,
      classification: "must-have",
      assessment: "scored",
      score: 4,
      rating_confidence: "high",
      evidence: [
        {
          turn_id: "turn-4",
          sequence: 4,
          quote: "I used idempotency keys and bounded retries.",
        },
      ],
      evidence_summary:
        "Explained a resilient write-path with explicit trade-offs.",
      gaps: ["Did not discuss schema evolution."],
      recommendations: ["Compare additive and breaking API changes."],
    },
    {
      competency_id: "competency-2",
      name: "Operational ownership",
      weight: 20,
      classification: "trainable",
      assessment: "not_assessed",
      score: null,
      rating_confidence: null,
      evidence: [],
      evidence_summary: null,
      gaps: [],
      recommendations: [],
      not_assessed_reason: "No production incident question was reached.",
    },
  ],
  strengths: ["Explained API trade-offs using a concrete project."],
  gaps: ["Operational evidence was not collected."],
  practice_exercises: [
    {
      title: "Design an idempotent payment endpoint",
      competency_ids: ["competency-1"],
      instruction: "Talk through the request lifecycle and failure modes.",
      success_criteria: [
        "Name the storage, retry, and concurrency trade-offs.",
      ],
    },
  ],
  uncertainty: ["Operational ownership was not assessed."],
  delivery_coaching: {
    interview_id: readyInterview.id,
    status: "available",
    consented: true,
    consent_version: "delivery-v1",
    unavailable_reason: null,
    baseline: {
      turn_count: 2,
      turn_ids: ["turn-2", "turn-4"],
      words_per_minute: 125,
      filler_words_per_100_words: 2.1,
      average_pause_ms: 800,
      average_response_delay_ms: 900,
    },
    metrics: [],
    observations: [
      {
        turn_id: "turn-4",
        category: "pace",
        text: "Speaking pace was 140 words per minute, 15 above your baseline.",
      },
    ],
    suggestions: ["Pause between the problem, options, and trade-off."],
  },
  completed_at: "2026-08-07T10:32:00Z",
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("evidence report", () => {
  it("renders scores, not-assessed state, candidate evidence, and separate delivery guidance", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(report), { status: 200 }),
      );

    render(
      <ReportPage
        interview={readyInterview}
        onBack={vi.fn()}
        onInterviewUpdated={vi.fn()}
      />,
    );

    expect(
      await screen.findByRole("heading", { name: "Backend Engineer practice" }),
    ).toBeInTheDocument();
    expect(screen.getByText("3.6 / 5")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByText("Not assessed")).toBeInTheDocument();
    expect(
      screen.getByText("No production incident question was reached."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Speaking metrics never change this evidence score."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Speaking delivery" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/140 words per minute/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Delete delivery metrics" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Disable delivery coaching" }),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByText("View 1 transcript excerpt"));
    expect(
      screen.getByText(/I used idempotency keys and bounded retries/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "Design an idempotent payment endpoint",
      }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/interviews/interview-1/report",
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: "application/json" }),
      }),
    );
  });

  it("shows evaluation progress without requesting a report early", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const evaluatingInterview = {
      ...readyInterview,
      status: "EVALUATING",
    };

    render(
      <ReportPage
        interview={evaluatingInterview}
        onBack={vi.fn()}
        onInterviewUpdated={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Reviewing your evidence" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Transcript final")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("evaluation kickoff", () => {
  it("requests evaluation once even while the poller republishes the interview", async () => {
    const finalizing: InterviewSession = {
      ...readyInterview,
      status: "TRANSCRIPT_FINALIZING",
    };
    const evaluateCalls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/evaluate")) {
          evaluateCalls.push(url);
          return new Response(
            JSON.stringify({
              interview_id: finalizing.id,
              status: "EVALUATING",
            }),
            { status: 200 },
          );
        }
        return new Response(JSON.stringify(finalizing), { status: 200 });
      },
    );

    // A new object identity each render is exactly what the 2-second poller
    // produced; it previously re-fired /evaluate on every tick and raced the
    // background job into a unique-constraint violation.
    const view = render(
      <ReportPage
        interview={{ ...finalizing }}
        onBack={vi.fn()}
        onInterviewUpdated={vi.fn()}
      />,
    );
    for (let tick = 0; tick < 4; tick += 1) {
      view.rerender(
        <ReportPage
          interview={{ ...finalizing }}
          onBack={vi.fn()}
          onInterviewUpdated={vi.fn()}
        />,
      );
    }

    await waitFor(() => expect(evaluateCalls.length).toBeGreaterThan(0));
    expect(evaluateCalls).toHaveLength(1);
  });
});
