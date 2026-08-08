import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const currentUser = {
  id: "user-1",
  email: "developer@local.test",
  display_name: "Local developer",
};

const createdSession = {
  id: "session-1",
  title: "Untitled practice session",
  status: "DRAFT",
  profile_id: null,
  scorecard_id: null,
  created_at: "2026-08-06T10:00:00Z",
  updated_at: "2026-08-06T10:00:00Z",
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("session dashboard", () => {
  it("loads the user and creates an empty practice session", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(currentUser), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [] }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(createdSession), { status: 201 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            upload: null,
            profile: null,
            job_target: null,
            scorecard: null,
          }),
          { status: 200 },
        ),
      );

    render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: "Welcome back, Local developer",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("No practice sessions yet")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "New practice session" }),
    );

    expect(
      await screen.findByRole("heading", {
        name: "Résumé profile",
      }),
    ).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
  });

  it("offers passwordless email sign-in when managed auth is required", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ error: { message: "Sign in is required." } }),
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ error: { message: "Sign in is required." } }),
          { status: 401 },
        ),
      );

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Your session has expired" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Reload the page to sign in again/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });

  it("shows editable M2 data with visible resume and JD sources", async () => {
    const readySession = {
      ...createdSession,
      id: "session-ready",
      title: "Backend Engineer practice",
      status: "SCORECARD_READY",
      profile_id: "profile-1",
      scorecard_id: "scorecard-1",
    };
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(currentUser), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [readySession] }), {
          status: 200,
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            upload: {
              id: "upload-1",
              original_filename: "resume.docx",
              file_type: "docx",
              media_type:
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              size: 12000,
              sha256: "a".repeat(64),
              raw_deleted_at: "2026-08-07T00:00:00Z",
              created_at: "2026-08-07T00:00:00Z",
            },
            profile: {
              id: "profile-1",
              source_resume_id: "upload-1",
              headline: "Backend engineer",
              claims: [
                {
                  id: "claim-1",
                  category: "experience",
                  text: "Built payment APIs",
                  source: {
                    source_id: "resume:block:4",
                    label: "Resume paragraph 4",
                    excerpt: "Built payment APIs and reduced latency by 35%.",
                  },
                  edited: false,
                  original_text: null,
                },
                ...Array.from({ length: 5 }, (_, index) => ({
                  id: `claim-${index + 2}`,
                  category: "experience",
                  text: `Additional backend achievement ${index + 1}`,
                  source: {
                    source_id: `resume:block:${index + 5}`,
                    label: `Resume paragraph ${index + 5}`,
                    excerpt: `Additional source-backed achievement ${index + 1}.`,
                  },
                  edited: false,
                  original_text: null,
                })),
              ],
              extractor_version: "local-rules-v1",
              version: 1,
              created_at: "2026-08-07T00:00:00Z",
              updated_at: "2026-08-07T00:00:00Z",
            },
            job_target: {
              id: "target-1",
              title: "Backend Engineer",
              seniority: "mid",
              raw_description:
                "Build backend APIs with SQL, tests, reliability, and team ownership.",
              structured_requirements: [],
              created_at: "2026-08-07T00:00:00Z",
              updated_at: "2026-08-07T00:00:00Z",
            },
            scorecard: {
              id: "scorecard-1",
              job_target_id: "target-1",
              version: 1,
              total_weight: 100,
              created_at: "2026-08-07T00:00:00Z",
              updated_at: "2026-08-07T00:00:00Z",
              competencies: [
                {
                  id: "competency-1",
                  name: "Backend API design",
                  description: "Design reliable backend APIs.",
                  weight: 60,
                  classification: "must-have",
                  seniority_expectation: "Works independently.",
                  evidence_to_collect: ["A personally designed API"],
                  question_families: ["API design"],
                  source_references: [
                    {
                      source_id: "jd:line:1",
                      label: "Job description line 1",
                      excerpt: "Build backend APIs with SQL.",
                    },
                  ],
                },
                {
                  id: "competency-2",
                  name: "Data persistence",
                  description: "Reason about SQL and data models.",
                  weight: 40,
                  classification: "must-have",
                  seniority_expectation: "Works independently.",
                  evidence_to_collect: ["A schema decision"],
                  question_families: ["Database design"],
                  source_references: [
                    {
                      source_id: "jd:line:1",
                      label: "Job description line 1",
                      excerpt: "Build backend APIs with SQL.",
                    },
                  ],
                },
              ],
            },
          }),
          { status: 200 },
        ),
      );

    render(<App />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Review setup" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Role scorecard" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Backend API design/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Data persistence/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByDisplayValue("Backend API design"),
    ).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /Backend API design/ }),
    );
    expect(screen.getByDisplayValue("Backend API design")).toBeInTheDocument();
    expect(screen.getByText("Job description line 1")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save scorecard" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Continue to preflight" }),
    ).toBeEnabled();

    await userEvent.click(screen.getByRole("tab", { name: /Profile/ }));
    expect(
      screen.getByRole("heading", { name: "Résumé profile" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Improve with AI" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Resume paragraph 4")).toBeInTheDocument();
    expect(screen.queryByText("Resume paragraph 9")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Resume paragraph 9")).toBeInTheDocument();
    expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Role scorecard" }),
    ).not.toBeInTheDocument();
  });

  it("requires confirmation before permanently deleting a session", async () => {
    const session = {
      ...createdSession,
      id: "session-delete",
      title: "Private backend practice",
      duration_minutes: 15,
      interview_type: "technical_behavioral",
      input_mode: "text_dev",
      started_at: null,
      ended_at: null,
      prompt_version: null,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(currentUser), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [session] }), { status: 200 }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    render(<App />);
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Delete Private backend practice",
      }),
    );

    expect(
      screen.getByRole("dialog", { name: "Delete practice session?" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/transcript, report, and delivery metrics/),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Delete permanently" }),
    );

    await waitFor(() =>
      expect(
        screen.queryByText("Private backend practice"),
      ).not.toBeInTheDocument(),
    );
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/interviews/session-delete",
      expect.objectContaining({
        method: "DELETE",
        body: JSON.stringify({ confirmation: "DELETE" }),
      }),
    );
  });

  it("shows private-alpha usage, retention, and guarded account deletion", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(currentUser), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [] }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            period_started_at: "2026-07-08T00:00:00Z",
            daily_interview_quota: 10,
            daily_interviews_used: 2,
            events: { session_created: 2 },
            estimated_cost_usd: "0.000000",
            cost_status: "unavailable",
            transcript_retention_days: 30,
            delivery_metrics_retention_days: 30,
          }),
          { status: 200 },
        ),
      );

    render(<App />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Privacy & usage" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Privacy & usage" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 of 10")).toBeInTheDocument();
    expect(screen.getByText(/retained for 30 days/)).toBeInTheDocument();
    expect(
      screen.getByText(/Provider cost telemetry is not available/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Voice audio is held only in browser and API memory while the configured Azure provider transcribes it/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/never written to disk and is not retained/),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Delete my account" }),
    );
    const confirm = screen.getByRole("button", {
      name: "Delete account permanently",
    });
    expect(confirm).toBeDisabled();
    await userEvent.type(
      screen.getByLabelText('Type "DELETE MY ACCOUNT" to confirm'),
      "DELETE MY ACCOUNT",
    );
    expect(confirm).toBeEnabled();
  });
});
