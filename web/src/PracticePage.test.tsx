import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PracticePage } from "./PracticePage";
import type { InterviewRuntime, InterviewSession } from "./types";

const interview: InterviewSession = {
  id: "session-live",
  title: "Backend practice",
  status: "SCORECARD_READY",
  profile_id: "profile-1",
  scorecard_id: "scorecard-1",
  duration_minutes: 15,
  interview_type: "technical_behavioral",
  input_mode: "voice",
  started_at: null,
  ended_at: null,
  prompt_version: null,
  created_at: "2026-08-07T00:00:00Z",
  updated_at: "2026-08-07T00:00:00Z",
};

const capabilities = {
  text_dev_mode_enabled: true,
  realtime_configured: true,
  live_transcription_configured: true,
  final_transcription_configured: true,
  typed_answer_max_characters: 20_000,
  supported_durations: [15, 30, 45, 60],
};

function runtime(overrides: Partial<InterviewRuntime> = {}): InterviewRuntime {
  return {
    interview_id: interview.id,
    status: "SCORECARD_READY",
    input_mode: "voice",
    duration_minutes: 15,
    started_at: null,
    ends_at: null,
    server_now: "2026-08-07T00:00:00Z",
    typed_answer_max_characters: 20_000,
    turns: [],
    ...overrides,
  };
}

function mockInitialRequests(nextRuntime: InterviewRuntime) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(
      new Response(JSON.stringify(capabilities), { status: 200 }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify(nextRuntime), { status: 200 }),
    );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("Realtime practice room", () => {
  it("offers separate opt-in consent only for voice delivery coaching", async () => {
    mockInitialRequests(runtime());

    render(
      <PracticePage
        interview={interview}
        onBack={vi.fn()}
        onInterviewUpdated={vi.fn()}
      />,
    );

    const consent = await screen.findByRole("checkbox", {
      name: /Add speaking-delivery coaching/,
    });
    expect(consent).not.toBeChecked();
    await userEvent.click(
      screen.getByRole("button", { name: "Developer text" }),
    );
    expect(
      screen.queryByRole("checkbox", {
        name: /Add speaking-delivery coaching/,
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/Speaking-delivery coaching is unavailable/),
    ).toBeInTheDocument();
  });

  it("restores an active interview directly into a reconnectable room", async () => {
    mockInitialRequests(
      runtime({
        status: "IN_PROGRESS",
        input_mode: "text_dev",
        started_at: "2026-08-07T00:00:00Z",
        ends_at: "2026-08-07T00:15:00Z",
      }),
    );

    render(
      <PracticePage
        interview={interview}
        onBack={vi.fn()}
        onInterviewUpdated={vi.fn()}
      />,
    );

    expect(await screen.findByText("Reconnect required")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Reconnect" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Your interview answer" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Ready your audio")).not.toBeInTheDocument();
  });

  it("stops an acquired microphone track when text mode is selected", async () => {
    mockInitialRequests(runtime());
    const stop = vi.fn();
    const stream = {
      active: true,
      getTracks: () => [{ stop }],
      getAudioTracks: () => [{ stop, label: "QA microphone" }],
    } as unknown as MediaStream;
    const getUserMedia = vi.fn().mockResolvedValue(stream);
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });

    render(
      <PracticePage
        interview={interview}
        onBack={vi.fn()}
        onInterviewUpdated={vi.fn()}
      />,
    );

    await userEvent.click(
      await screen.findByRole("checkbox", {
        name: /Allow microphone access for this interview/,
      }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Check microphone" }),
    );
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce());
    expect(screen.getByText("QA microphone")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Developer text" }),
    );

    expect(stop).toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Developer text" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Microphone off")).toBeInTheDocument();
  });
});
