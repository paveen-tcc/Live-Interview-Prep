import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PracticePage } from "./PracticePage";
import type {
  Capabilities,
  InterviewRuntime,
  InterviewSession,
  InterviewTurn,
} from "./types";

const mocks = vi.hoisted(() => {
  const timeline: string[] = [];
  const state = { mimeType: "audio/webm;codecs=opus" as string | null };
  const fatalMessage =
    "Candidate transcription is paused. Reconnect to retry this answer.";

  class CoordinatorError extends Error {
    constructor() {
      super(fatalMessage);
      this.name = "TranscriptionCoordinatorError";
    }
  }

  class MockRecorder {
    started = false;
    stopped = false;
    finishCount = 0;
    readonly speechStartedIds: string[] = [];
    readonly speechStoppedIds: string[] = [];

    constructor(
      readonly stream: MediaStream,
      readonly callbacks: {
        onUtterance: (utterance: unknown) => void;
        onError: (message: string) => void;
      },
    ) {
      recorders.push(this);
    }

    start(): void {
      this.started = true;
      timeline.push("recorder:start");
    }

    speechStarted(itemId: string): void {
      this.speechStartedIds.push(itemId);
    }

    speechStopped(itemId: string): void {
      this.speechStoppedIds.push(itemId);
    }

    finish(): Promise<void> {
      this.finishCount += 1;
      timeline.push("recorder:finish");
      return Promise.resolve();
    }

    stop(): void {
      this.stopped = true;
      timeline.push("recorder:stop");
    }
  }

  class MockCoordinator {
    disposed = false;
    retryCount = 0;
    idle: () => Promise<void> = () => Promise.resolve();
    retry: () => Promise<void> = () => Promise.resolve();
    readonly liveCompletedCalls: Array<{
      itemId: string;
      transcript: string;
    }> = [];
    readonly liveFailedCalls: string[] = [];
    readonly audioReadyCalls: unknown[] = [];

    constructor(
      readonly interviewId: string,
      readonly operations: Record<string, unknown>,
      readonly callbacks: {
        onRuntime?: (runtime: InterviewRuntime) => void;
        onFatal?: (error: Error) => void;
        onRecovered?: () => void;
      },
    ) {
      coordinators.push(this);
    }

    liveCompleted(itemId: string, transcript: string): void {
      this.liveCompletedCalls.push({ itemId, transcript });
    }

    liveFailed(itemId: string): void {
      this.liveFailedCalls.push(itemId);
    }

    audioReady(utterance: unknown): void {
      this.audioReadyCalls.push(utterance);
    }

    awaitIdle(): Promise<void> {
      timeline.push("coordinator:awaitIdle");
      return this.idle();
    }

    retryRetained(): Promise<void> {
      this.retryCount += 1;
      timeline.push("coordinator:retryRetained");
      return this.retry();
    }

    dispose(): void {
      this.disposed = true;
      timeline.push("coordinator:dispose");
    }
  }

  const recorders: MockRecorder[] = [];
  const coordinators: MockCoordinator[] = [];

  return {
    timeline,
    state,
    fatalMessage,
    recorders,
    coordinators,
    CoordinatorError,
    MockRecorder,
    MockCoordinator,
  };
});

const FATAL_MESSAGE = mocks.fatalMessage;

vi.mock("./voiceCapture", () => ({
  BufferedUtteranceRecorder: mocks.MockRecorder,
  selectRecorderMimeType: () => mocks.state.mimeType,
}));

vi.mock("./transcription", () => ({
  TurnTranscriptionCoordinator: mocks.MockCoordinator,
  TranscriptionCoordinatorError: mocks.CoordinatorError,
  COORDINATOR_FATAL_MESSAGE: mocks.fatalMessage,
}));

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

const capabilities: Capabilities = {
  text_dev_mode_enabled: true,
  realtime_configured: true,
  live_transcription_configured: true,
  final_transcription_configured: true,
  typed_answer_max_characters: 20_000,
  supported_durations: [15, 30, 45, 60],
};

const CALLS_URL = "https://calls.test/rtc";

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

function userTurn(overrides: Partial<InterviewTurn> = {}): InterviewTurn {
  return {
    id: "turn-1",
    client_turn_id: "item_1",
    sequence: 1,
    speaker: "user",
    transcript: "I built a FastAPI service.",
    transcription_source: "final_model",
    transcription_model: "gpt-4o-transcribe",
    transcription_finalized_at: "2026-08-07T00:01:00Z",
    delivery_status: "acknowledged",
    started_at: "2026-08-07T00:00:30Z",
    ended_at: "2026-08-07T00:00:45Z",
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

interface FetchState {
  capabilities: Capabilities;
  runtime: InterviewRuntime;
  connectionStateStatus: number;
  calls: Array<{ url: string; method: string; body: string | null }>;
}

function installFetch(overrides: Partial<FetchState> = {}): FetchState {
  const state: FetchState = {
    capabilities,
    runtime: runtime(),
    connectionStateStatus: 200,
    calls: [],
    ...overrides,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      state.calls.push({
        url,
        method,
        body: typeof init?.body === "string" ? init.body : null,
      });
      const json = (value: unknown) =>
        new Response(JSON.stringify(value), { status: 200 });
      if (url === CALLS_URL) return new Response("answer-sdp", { status: 200 });
      if (url === "/api/capabilities") return json(state.capabilities);
      if (url.endsWith("/realtime-client-secret")) {
        return json({
          client_secret: "ephemeral",
          expires_at: 0,
          calls_url: CALLS_URL,
          input_mode: "voice",
          prompt_version: "v1",
        });
      }
      if (url.endsWith("/delivery-consent")) {
        timelinePush("POST /delivery-consent");
        return json({ interview_id: interview.id, consented: true });
      }
      if (url.endsWith("/transcription-events")) {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/turns:batch")) {
        timelinePush("POST /turns:batch");
        return json(state.runtime);
      }
      if (url.endsWith("/complete")) {
        timelinePush("POST /complete");
        return json(state.runtime);
      }
      if (url.endsWith("/connection-state")) {
        if (state.connectionStateStatus !== 200) {
          return new Response(
            JSON.stringify({
              error: {
                message:
                  "Connection state 'reconnecting' is invalid while the interview is TRANSCRIPT_FINALIZING.",
              },
            }),
            { status: state.connectionStateStatus },
          );
        }
        return json(state.runtime);
      }
      if (url.endsWith("/runtime")) return json(state.runtime);
      if (url === `/api/interviews/${interview.id}`) return json(interview);
      return json({});
    },
  );
  return state;
}

function timelinePush(entry: string) {
  mocks.timeline.push(entry);
}

interface FakeTrack {
  kind: string;
  label: string;
  enabled: boolean;
  stop: () => void;
}

class FakeDataChannel {
  readyState = "connecting";
  private readonly listeners = new Map<string, Set<(event: never) => void>>();

  addEventListener(type: string, handler: (event: never) => void): void {
    const handlers = this.listeners.get(type) ?? new Set();
    handlers.add(handler);
    this.listeners.set(type, handlers);
  }

  send(): void {}

  close(): void {
    this.readyState = "closed";
  }

  open(): void {
    this.readyState = "open";
    for (const handler of this.listeners.get("open") ?? []) {
      (handler as () => void)();
    }
  }

  deliver(event: object): void {
    for (const handler of this.listeners.get("message") ?? []) {
      (handler as (message: { data: string }) => void)({
        data: JSON.stringify(event),
      });
    }
  }
}

class FakePeerConnection {
  connectionState: RTCPeerConnectionState = "new";
  channel: FakeDataChannel | null = null;
  closed = false;
  readonly senders: Array<{ track: FakeTrack | null }> = [];
  readonly addedStreams: MediaStream[] = [];
  onconnectionstatechange: (() => void) | null = null;
  ontrack: ((event: unknown) => void) | null = null;

  constructor() {
    peers.push(this);
  }

  addTrack(track: FakeTrack, stream: MediaStream) {
    this.senders.push({ track });
    this.addedStreams.push(stream);
    return { track };
  }

  addTransceiver(): void {}

  getSenders() {
    return this.senders;
  }

  createDataChannel(): FakeDataChannel {
    this.channel = new FakeDataChannel();
    return this.channel;
  }

  createOffer() {
    return Promise.resolve({ type: "offer", sdp: "offer-sdp" });
  }

  setLocalDescription() {
    return Promise.resolve();
  }

  setRemoteDescription() {
    return Promise.resolve();
  }

  close(): void {
    this.closed = true;
    this.connectionState = "closed";
    mocks.timeline.push("transport:close");
  }
}

let peers: FakePeerConnection[] = [];

function installMedia() {
  const track: FakeTrack = {
    kind: "audio",
    label: "QA microphone",
    stop: vi.fn(),
    get enabled() {
      return enabled;
    },
    set enabled(value: boolean) {
      enabled = value;
      mocks.timeline.push(`mic:${value}`);
    },
  } as FakeTrack;
  let enabled = true;
  const stream = {
    active: true,
    getTracks: () => [track],
    getAudioTracks: () => [track],
  } as unknown as MediaStream;
  const getUserMedia = vi.fn().mockResolvedValue(stream);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  return { track, stream, getUserMedia };
}

function installAudioContext() {
  const gain = { gain: {}, connect: (node: unknown) => node };
  const oscillator = {
    frequency: {},
    connect: () => gain,
    start: () => undefined,
    stop: () => undefined,
    addEventListener: () => undefined,
  };
  class FakeAudioContext {
    currentTime = 0;
    destination = {};
    createOscillator() {
      return oscillator;
    }
    createGain() {
      return gain;
    }
    close() {
      return Promise.resolve();
    }
  }
  Object.defineProperty(window, "AudioContext", {
    configurable: true,
    writable: true,
    value: FakeAudioContext,
  });
}

function renderPage(props: Partial<React.ComponentProps<typeof PracticePage>>) {
  return render(
    <PracticePage
      interview={interview}
      onBack={vi.fn()}
      onInterviewUpdated={vi.fn()}
      {...props}
    />,
  );
}

async function passPreflight() {
  await userEvent.click(
    await screen.findByRole("button", { name: "Play test sound" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "I heard it" }));
  await userEvent.click(
    screen.getByRole("checkbox", {
      name: /Allow microphone access for this interview/,
    }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Check microphone" }),
  );
  await screen.findByText("QA microphone");
}

async function openChannel(peer: FakePeerConnection) {
  await waitFor(() => expect(peer.channel).not.toBeNull());
  await act(async () => {
    peer.channel?.open();
  });
}

async function waitForConnected() {
  await waitFor(() =>
    expect(
      document.querySelector(".connection-dot.is-connected"),
    ).not.toBeNull(),
  );
}

async function startVoiceInterview(): Promise<FakePeerConnection> {
  await passPreflight();
  await userEvent.click(
    screen.getByRole("button", { name: "Start interview" }),
  );
  await waitFor(() => expect(peers).toHaveLength(1));
  const peer = peers[0];
  await openChannel(peer);
  await waitForConnected();
  return peer;
}

function deliver(peer: FakePeerConnection, event: object) {
  return act(async () => {
    peer.channel?.deliver(event);
  });
}

beforeEach(() => {
  peers = [];
  mocks.timeline.length = 0;
  mocks.recorders.length = 0;
  mocks.coordinators.length = 0;
  mocks.state.mimeType = "audio/webm;codecs=opus";
  Object.defineProperty(window, "RTCPeerConnection", {
    configurable: true,
    writable: true,
    value: FakePeerConnection,
  });
  globalThis.RTCPeerConnection =
    FakePeerConnection as unknown as typeof RTCPeerConnection;
  installAudioContext();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("Realtime practice room", () => {
  it("offers separate opt-in consent only for voice delivery coaching", async () => {
    mockInitialRequests(runtime());

    renderPage({});

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

    renderPage({});

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
    const { track, getUserMedia } = installMedia();

    renderPage({});

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

    expect(track.stop).toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Developer text" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Microphone off")).toBeInTheDocument();
  });
});

describe("Dual transcription preflight", () => {
  it("refuses a voice interview and names each missing transcription deployment", async () => {
    installFetch({
      capabilities: {
        ...capabilities,
        final_transcription_configured: false,
      },
    });
    installMedia();

    renderPage({});

    expect(
      await screen.findByText(
        /final transcription deployment is not configured/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/live transcription deployment is not configured/i),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start interview" }),
    ).toBeDisabled();
  });

  it("names the live deployment when only live transcription is missing", async () => {
    installFetch({
      capabilities: {
        ...capabilities,
        live_transcription_configured: false,
      },
    });
    installMedia();

    renderPage({});

    expect(
      await screen.findByText(
        /live transcription deployment is not configured/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start interview" }),
    ).toBeDisabled();
  });

  it("describes transient in-memory audio processing instead of Realtime-only delivery", async () => {
    installFetch();
    installMedia();

    renderPage({});

    expect(
      await screen.findByText(
        /Audio is processed in memory for live and final transcription, sent to the configured Azure provider, and not retained by this app/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/goes directly to Azure Realtime/i),
    ).not.toBeInTheDocument();
  });

  it("refuses microphone capture when no allowlisted recorder MIME type exists", async () => {
    installFetch();
    const { getUserMedia } = installMedia();
    mocks.state.mimeType = null;

    renderPage({});

    await userEvent.click(
      await screen.findByRole("checkbox", {
        name: /Allow microphone access for this interview/,
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Check microphone" }),
    );

    expect(
      await screen.findByText(/cannot record answer audio/i),
    ).toBeInTheDocument();
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Start interview" }),
    ).toBeDisabled();
  });
});

describe("Dual transcription capture", () => {
  it("passes one acquired media stream to both the transport and the recorder", async () => {
    installFetch();
    const { stream, getUserMedia } = installMedia();

    renderPage({});
    const peer = await startVoiceInterview();

    expect(getUserMedia).toHaveBeenCalledOnce();
    expect(peer.addedStreams[0]).toBe(stream);
    expect(mocks.recorders).toHaveLength(1);
    expect(mocks.recorders[0].stream).toBe(stream);
    expect(mocks.recorders[0].started).toBe(true);
    expect(mocks.coordinators).toHaveLength(1);
  });

  it("forwards voice activity boundaries to the recorder using the same item id", async () => {
    installFetch();
    installMedia();

    renderPage({});
    const peer = await startVoiceInterview();

    await deliver(peer, {
      type: "input_audio_buffer.speech_started",
      item_id: "item_7",
      audio_start_ms: 1000,
    });
    await deliver(peer, {
      type: "input_audio_buffer.speech_stopped",
      item_id: "item_7",
      audio_end_ms: 4000,
    });

    expect(mocks.recorders[0].speechStartedIds).toEqual(["item_7"]);
    expect(mocks.recorders[0].speechStoppedIds).toEqual(["item_7"]);
  });

  it("routes live transcription results and failures to the coordinator", async () => {
    installFetch();
    installMedia();

    renderPage({});
    const peer = await startVoiceInterview();

    await deliver(peer, {
      type: "conversation.item.input_audio_transcription.completed",
      item_id: "item_7",
      transcript: "I built a FastAPI service.",
    });
    await deliver(peer, {
      type: "conversation.item.input_audio_transcription.failed",
      item_id: "item_8",
    });

    const coordinator = mocks.coordinators[0];
    expect(coordinator.liveCompletedCalls).toEqual([
      { itemId: "item_7", transcript: "I built a FastAPI service." },
    ]);
    expect(coordinator.liveFailedCalls).toEqual(["item_8"]);
    expect(
      mocks.timeline.filter((entry) => entry === "POST /turns:batch"),
    ).toHaveLength(0);
  });

  it("hands recorded utterances to the coordinator", async () => {
    installFetch();
    installMedia();

    renderPage({});
    await startVoiceInterview();

    const utterance = { itemId: "item_7", blob: new Blob(["x"]) };
    act(() => mocks.recorders[0].callbacks.onUtterance(utterance));

    expect(mocks.coordinators[0].audioReadyCalls).toEqual([utterance]);
  });
});

describe("Dual transcription ordering and status", () => {
  it("persists the assistant turn only after the candidate queue is idle", async () => {
    installFetch();
    installMedia();

    renderPage({});
    const peer = await startVoiceInterview();

    let releaseIdle: (() => void) | null = null;
    mocks.coordinators[0].idle = () =>
      new Promise<void>((resolve) => {
        releaseIdle = resolve;
      });

    await deliver(peer, {
      type: "response.output_audio_transcript.done",
      item_id: "assistant_1",
      transcript: "Tell me about a system you designed.",
    });

    expect(mocks.timeline).toContain("coordinator:awaitIdle");
    expect(mocks.timeline).not.toContain("POST /turns:batch");

    await act(async () => {
      releaseIdle?.();
    });

    await waitFor(() => expect(mocks.timeline).toContain("POST /turns:batch"));
    expect(mocks.timeline.indexOf("coordinator:awaitIdle")).toBeLessThan(
      mocks.timeline.indexOf("POST /turns:batch"),
    );
  });

  it("shows a nonblocking live-transcript status and stays connected after final-only failure", async () => {
    installFetch();
    installMedia();

    renderPage({});
    await startVoiceInterview();

    act(() =>
      mocks.coordinators[0].callbacks.onRuntime?.(
        runtime({
          turns: [
            userTurn({
              transcription_source: "realtime_live",
              transcription_model: "gpt-realtime-whisper",
              transcription_finalized_at: "2026-08-07T00:01:05Z",
            }),
          ],
        }),
      ),
    );

    expect(
      await screen.findByText(/Using live transcript/),
    ).toBeInTheDocument();
    expect(
      document.querySelector(".connection-dot.is-connected"),
    ).not.toBeNull();
    expect(
      screen.queryByRole("button", { name: "Reconnect" }),
    ).not.toBeInTheDocument();
  });
});

describe("Dual transcription pause and recovery", () => {
  it("pauses the microphone, closes the transport, and offers Reconnect on double failure", async () => {
    installFetch();
    installMedia();

    renderPage({});
    const peer = await startVoiceInterview();

    act(() =>
      mocks.coordinators[0].callbacks.onFatal?.(new mocks.CoordinatorError()),
    );

    expect(await screen.findByText(FATAL_MESSAGE)).toBeInTheDocument();
    expect(mocks.timeline).toContain("mic:false");
    expect(peer.closed).toBe(true);
    expect(
      screen.getByRole("button", { name: "Reconnect" }),
    ).toBeInTheDocument();
  });

  it("uses the same pause path when the recorder reports overflow or capture failure", async () => {
    installFetch();
    installMedia();

    renderPage({});
    const peer = await startVoiceInterview();

    act(() =>
      mocks.recorders[0].callbacks.onError(
        "Too many candidate utterances are waiting to be captured.",
      ),
    );

    expect(
      await screen.findByText(/Too many candidate utterances/),
    ).toBeInTheDocument();
    expect(mocks.timeline).toContain("mic:false");
    expect(peer.closed).toBe(true);
    expect(
      screen.getByRole("button", { name: "Reconnect" }),
    ).toBeInTheDocument();
  });

  it("stops offering Reconnect once the server says the interview has ended", async () => {
    const state = installFetch();
    installMedia();

    renderPage({});
    await startVoiceInterview();

    // The timer expired server-side: every "reconnecting" transition is now 409.
    state.connectionStateStatus = 409;
    act(() =>
      mocks.coordinators[0].callbacks.onFatal?.(new mocks.CoordinatorError()),
    );

    expect(
      await screen.findByText(/This interview has already ended/),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Reconnect" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(FATAL_MESSAGE)).not.toBeInTheDocument();
  });

  it("escalates from a quiet status to an alert once fallback is systematic", async () => {
    installFetch();
    installMedia();

    renderPage({});
    await startVoiceInterview();

    const fallback = (index: number) =>
      userTurn({
        id: `turn-${index}`,
        client_turn_id: `item_${index}`,
        sequence: index,
        transcription_source: "realtime_live",
        transcription_model: "gpt-realtime-whisper",
        transcription_finalized_at: "2026-08-07T00:01:05Z",
      });

    act(() =>
      mocks.coordinators[0].callbacks.onRuntime?.(
        runtime({ turns: [fallback(1)] }),
      ),
    );
    expect(
      await screen.findByText(/Using live transcript/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    act(() =>
      mocks.coordinators[0].callbacks.onRuntime?.(
        runtime({ turns: [fallback(1), fallback(2), fallback(3)] }),
      ),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Using live transcript for 3 answers/);
    expect(alert).toHaveTextContent(
      /Final transcription is failing repeatedly/,
    );
  });

  it("retries retained audio before re-enabling the microphone on reconnect", async () => {
    installFetch();
    installMedia();

    renderPage({});
    await startVoiceInterview();

    act(() =>
      mocks.coordinators[0].callbacks.onFatal?.(new mocks.CoordinatorError()),
    );
    await screen.findByText(FATAL_MESSAGE);
    mocks.timeline.length = 0;
    mocks.coordinators[0].retryCount = 0;

    await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() => expect(peers).toHaveLength(2));
    await openChannel(peers[1]);
    await waitForConnected();

    expect(mocks.coordinators).toHaveLength(1);
    expect(mocks.recorders).toHaveLength(1);
    expect(mocks.coordinators[0].retryCount).toBe(1);
    expect(mocks.timeline.indexOf("coordinator:retryRetained")).toBeLessThan(
      mocks.timeline.lastIndexOf("mic:true"),
    );
    expect(mocks.timeline).toContain("mic:true");
    expect(screen.queryByText(FATAL_MESSAGE)).not.toBeInTheDocument();
  });

  it("retains assistant text through a fatal candidate state until reconnect succeeds", async () => {
    installFetch();
    installMedia();

    renderPage({});
    const peer = await startVoiceInterview();

    mocks.coordinators[0].idle = () =>
      Promise.reject(new mocks.CoordinatorError());
    await deliver(peer, {
      type: "response.output_audio_transcript.done",
      item_id: "assistant_1",
      transcript: "Tell me about a system you designed.",
    });
    act(() =>
      mocks.coordinators[0].callbacks.onFatal?.(new mocks.CoordinatorError()),
    );
    await screen.findByText(FATAL_MESSAGE);
    expect(mocks.timeline).not.toContain("POST /turns:batch");

    mocks.coordinators[0].idle = () => Promise.resolve();
    await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() => expect(peers).toHaveLength(2));
    await openChannel(peers[1]);

    await waitFor(() => expect(mocks.timeline).toContain("POST /turns:batch"));
    const state = vi.mocked(globalThis.fetch).mock.calls;
    const assistantCall = state.find(
      ([, init]) =>
        typeof init?.body === "string" &&
        init.body.includes("Tell me about a system you designed."),
    );
    expect(assistantCall).toBeDefined();
  });
});

describe("Dual transcription completion", () => {
  it("finalizes the recorder and queue before completing the interview once", async () => {
    const onInterviewUpdated = vi.fn();
    installFetch();
    installMedia();

    renderPage({ onInterviewUpdated });
    await startVoiceInterview();
    mocks.timeline.length = 0;

    let releaseIdle: (() => void) | null = null;
    mocks.coordinators[0].idle = () =>
      new Promise<void>((resolve) => {
        releaseIdle = resolve;
      });

    await userEvent.click(screen.getByRole("button", { name: /Stop/ }));

    expect(
      await screen.findByText("Finalizing your transcript"),
    ).toBeInTheDocument();
    expect(mocks.timeline).not.toContain("POST /complete");

    await act(async () => {
      releaseIdle?.();
    });

    await waitFor(() => expect(onInterviewUpdated).toHaveBeenCalledOnce());
    expect(
      mocks.timeline.filter((entry) => entry === "POST /complete"),
    ).toHaveLength(1);
    expect(mocks.timeline.indexOf("recorder:finish")).toBeLessThan(
      mocks.timeline.indexOf("coordinator:awaitIdle"),
    );
    expect(mocks.timeline.indexOf("coordinator:awaitIdle")).toBeLessThan(
      mocks.timeline.indexOf("POST /complete"),
    );
  });

  it("refuses to complete while candidate audio is unresolved", async () => {
    const onInterviewUpdated = vi.fn();
    installFetch();
    installMedia();

    renderPage({ onInterviewUpdated });
    await startVoiceInterview();
    mocks.coordinators[0].idle = () =>
      Promise.reject(new mocks.CoordinatorError());

    await userEvent.click(screen.getByRole("button", { name: /Stop/ }));

    expect(await screen.findByText(FATAL_MESSAGE)).toBeInTheDocument();
    expect(mocks.timeline).not.toContain("POST /complete");
    expect(onInterviewUpdated).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Reconnect" }),
    ).toBeInTheDocument();
  });

  it("releases the recorder, coordinator, transport, and microphone on unmount", async () => {
    installFetch();
    const { track } = installMedia();

    const view = renderPage({});
    const peer = await startVoiceInterview();

    view.unmount();

    expect(mocks.recorders[0].stopped).toBe(true);
    expect(mocks.coordinators[0].disposed).toBe(true);
    expect(peer.closed).toBe(true);
    expect(track.stop).toHaveBeenCalled();
  });

  it("creates no microphone, recorder, or coordinator in developer text mode", async () => {
    installFetch();
    const { getUserMedia } = installMedia();

    renderPage({});
    await userEvent.click(
      await screen.findByRole("button", { name: "Developer text" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Play test sound" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "I heard it" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Start interview" }),
    );
    await waitFor(() => expect(peers).toHaveLength(1));
    await openChannel(peers[0]);
    await waitForConnected();

    expect(getUserMedia).not.toHaveBeenCalled();
    expect(mocks.recorders).toHaveLength(0);
    expect(mocks.coordinators).toHaveLength(0);
  });
});
