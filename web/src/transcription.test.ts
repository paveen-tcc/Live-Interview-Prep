import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import {
  COORDINATOR_FATAL_MESSAGE,
  TurnTranscriptionCoordinator,
  type TurnTranscriptionOperations,
} from "./transcription";
import type { InterviewRuntime } from "./types";
import type { RecordedUtterance } from "./voiceCapture";

function runtime(label: string): InterviewRuntime {
  return {
    interview_id: "interview-1",
    status: label,
    input_mode: "voice",
    duration_minutes: 30,
    started_at: "2026-08-08T10:00:00.000Z",
    ends_at: "2026-08-08T10:30:00.000Z",
    server_now: "2026-08-08T10:01:00.000Z",
    typed_answer_max_characters: 20_000,
    turns: [],
  };
}

function utterance(itemId: string): RecordedUtterance {
  return {
    itemId,
    blob: new Blob([`audio-${itemId}`], { type: "audio/webm;codecs=opus" }),
    mediaType: "audio/webm;codecs=opus",
    startedAt: "2026-08-08T10:00:01.000Z",
    endedAt: "2026-08-08T10:00:03.000Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function setup(
  overrides: Partial<TurnTranscriptionOperations> = {},
  maxPendingTurns = 2,
) {
  const operations: TurnTranscriptionOperations = {
    saveTurns: vi.fn().mockResolvedValue(runtime("live")),
    transcribeTurn: vi.fn().mockResolvedValue(runtime("final")),
    acceptLiveTranscript: vi.fn().mockResolvedValue(runtime("fallback")),
    recordTranscriptionEvent: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  const onRuntime = vi.fn();
  const onFatal = vi.fn();
  const onRecovered = vi.fn();
  const coordinator = new TurnTranscriptionCoordinator(
    "interview-1",
    operations,
    { onRuntime, onFatal, onRecovered },
    { maxPendingTurns },
  );
  return {
    coordinator,
    operations,
    onRuntime,
    onFatal,
    onRecovered,
  };
}

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("transcription API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uploads the Blob and optional timestamps before returning fresh runtime", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify(runtime("fresh")), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetch);
    const audio = utterance("item-1");

    await expect(
      api.transcribeTurn("interview-1", "item-1", audio),
    ).resolves.toMatchObject({ status: "fresh" });

    const [path, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/interviews/interview-1/turns/item-1:transcribe");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const form = init.body as FormData;
    const uploadedFile = form.get("file");
    expect(uploadedFile).toBeInstanceOf(Blob);
    expect((uploadedFile as Blob).size).toBe(audio.blob.size);
    expect((uploadedFile as Blob).type).toBe("audio/webm;codecs=opus");
    expect(form.get("started_at")).toBe("2026-08-08T10:00:01.000Z");
    expect(form.get("ended_at")).toBe("2026-08-08T10:00:03.000Z");
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("accepts a live fallback and sends content-free allowlisted telemetry", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(runtime("fallback")), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);

    await expect(
      api.acceptLiveTranscript("interview-1", "item-1"),
    ).resolves.toMatchObject({ status: "fallback" });
    await api.recordTranscriptionEvent(
      "interview-1",
      "double_transcription_failure",
    );

    expect(fetch.mock.calls[0]?.[0]).toBe(
      "/api/interviews/interview-1/turns/item-1:accept-live",
    );
    expect(fetch.mock.calls[0]?.[1]).toMatchObject({ method: "POST" });
    const telemetryInit = fetch.mock.calls[1]?.[1] as RequestInit;
    expect(fetch.mock.calls[1]?.[0]).toBe(
      "/api/interviews/interview-1/transcription-events",
    );
    expect(JSON.parse(String(telemetryInit.body))).toEqual({
      kind: "double_transcription_failure",
    });
  });
});

describe("TurnTranscriptionCoordinator", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("converges live-first and releases the turn after final persistence", async () => {
    const final = deferred<InterviewRuntime>();
    const { coordinator, operations, onRuntime } = setup({
      transcribeTurn: vi.fn().mockReturnValue(final.promise),
    });

    coordinator.liveCompleted("item-1", "live answer");
    await flush();
    coordinator.audioReady(utterance("item-1"));
    final.resolve(runtime("final"));
    await coordinator.awaitIdle();

    expect(operations.saveTurns).toHaveBeenCalledWith("interview-1", [
      expect.objectContaining({
        client_turn_id: "item-1",
        speaker: "user",
        transcript: "live answer",
      }),
    ]);
    expect(operations.transcribeTurn).toHaveBeenCalledOnce();
    expect(operations.acceptLiveTranscript).not.toHaveBeenCalled();
    expect(onRuntime).toHaveBeenLastCalledWith(runtime("final"));
  });

  it("keeps a completed final-first turn when live events arrive later", async () => {
    const final = deferred<InterviewRuntime>();
    const { coordinator, operations, onRuntime } = setup({
      transcribeTurn: vi.fn().mockReturnValue(final.promise),
    });

    coordinator.audioReady(utterance("item-1"));
    final.resolve(runtime("final"));
    await coordinator.awaitIdle();

    coordinator.liveCompleted("item-1", "live answer");
    coordinator.liveCompleted("item-1", "duplicate answer");
    coordinator.audioReady(utterance("item-1"));
    coordinator.liveFailed("item-1");
    await flush();

    expect(operations.saveTurns).not.toHaveBeenCalled();
    expect(operations.transcribeTurn).toHaveBeenCalledOnce();
    expect(operations.recordTranscriptionEvent).not.toHaveBeenCalled();
    expect(onRuntime).toHaveBeenCalledTimes(1);
    expect(onRuntime).toHaveBeenLastCalledWith(runtime("final"));
  });

  it("accepts persisted live text when final transcription fails", async () => {
    const { coordinator, operations, onRuntime, onFatal } = setup({
      transcribeTurn: vi.fn().mockRejectedValue(new Error("provider body")),
    });

    coordinator.liveCompleted("item-1", "usable live answer");
    coordinator.audioReady(utterance("item-1"));
    await coordinator.awaitIdle();

    expect(operations.acceptLiveTranscript).toHaveBeenCalledWith(
      "interview-1",
      "item-1",
    );
    expect(onRuntime).toHaveBeenLastCalledWith(runtime("fallback"));
    expect(onFatal).not.toHaveBeenCalled();
  });

  it("keeps final success when live persistence fails and reports that lane", async () => {
    const live = deferred<InterviewRuntime>();
    const final = deferred<InterviewRuntime>();
    const { coordinator, operations, onRuntime, onFatal } = setup({
      saveTurns: vi.fn().mockReturnValue(live.promise),
      transcribeTurn: vi.fn().mockReturnValue(final.promise),
    });

    coordinator.liveCompleted("item-1", "live answer");
    coordinator.audioReady(utterance("item-1"));
    live.reject(new Error("network failed"));
    final.resolve(runtime("final"));
    await coordinator.awaitIdle();

    expect(operations.recordTranscriptionEvent).toHaveBeenCalledWith(
      "interview-1",
      "live_transcription_failed",
    );
    expect(onRuntime).toHaveBeenLastCalledWith(runtime("final"));
    expect(onFatal).not.toHaveBeenCalled();
  });

  it("keeps final success after an explicit live-lane failure", async () => {
    const final = deferred<InterviewRuntime>();
    const { coordinator, operations, onRuntime, onFatal } = setup({
      transcribeTurn: vi.fn().mockReturnValue(final.promise),
    });

    coordinator.liveFailed("item-1");
    coordinator.audioReady(utterance("item-1"));
    final.resolve(runtime("final"));
    await coordinator.awaitIdle();

    expect(operations.saveTurns).not.toHaveBeenCalled();
    expect(onRuntime).toHaveBeenLastCalledWith(runtime("final"));
    expect(onFatal).not.toHaveBeenCalled();
  });

  it("waits 3 seconds for a late live result before declaring double failure", async () => {
    const { coordinator, operations, onFatal } = setup({
      transcribeTurn: vi.fn().mockRejectedValue(new Error("secret provider")),
    });
    const audio = utterance("item-1");

    coordinator.audioReady(audio);
    await flush();
    expect(onFatal).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2_999);
    expect(onFatal).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);

    expect(onFatal).toHaveBeenCalledOnce();
    expect(onFatal.mock.calls[0]?.[0]).toMatchObject({
      message: COORDINATOR_FATAL_MESSAGE,
    });
    expect(operations.recordTranscriptionEvent).toHaveBeenCalledWith(
      "interview-1",
      "double_transcription_failure",
    );
    await expect(coordinator.awaitIdle()).rejects.toThrow(
      COORDINATOR_FATAL_MESSAGE,
    );
  });

  it("retries retained audio and only then announces recovery", async () => {
    const retry = deferred<InterviewRuntime>();
    const transcribeTurn = vi
      .fn()
      .mockRejectedValueOnce(new Error("first failure"))
      .mockReturnValueOnce(retry.promise);
    const { coordinator, onRecovered, onRuntime } = setup({ transcribeTurn });

    coordinator.liveFailed("item-1");
    coordinator.audioReady(utterance("item-1"));
    await flush();
    await expect(coordinator.awaitIdle()).rejects.toThrow(
      COORDINATOR_FATAL_MESSAGE,
    );

    const reconnect = coordinator.retryRetained();
    expect(onRecovered).not.toHaveBeenCalled();
    retry.resolve(runtime("retried"));
    await reconnect;

    expect(transcribeTurn).toHaveBeenCalledTimes(2);
    expect(onRuntime).toHaveBeenLastCalledWith(runtime("retried"));
    expect(onRecovered).toHaveBeenCalledOnce();
    await expect(coordinator.awaitIdle()).resolves.toBeUndefined();
  });

  it("keeps fatal audio retained when a retry fails", async () => {
    const { coordinator, onRecovered } = setup({
      transcribeTurn: vi.fn().mockRejectedValue(new Error("still down")),
    });
    coordinator.liveFailed("item-1");
    coordinator.audioReady(utterance("item-1"));
    await flush();

    await expect(coordinator.retryRetained()).rejects.toThrow(
      COORDINATOR_FATAL_MESSAGE,
    );
    expect(onRecovered).not.toHaveBeenCalled();
    await expect(coordinator.awaitIdle()).rejects.toThrow(
      COORDINATOR_FATAL_MESSAGE,
    );
  });

  it("retries live fallback acceptance without retrying a failed final provider", async () => {
    const acceptLiveTranscript = vi
      .fn()
      .mockRejectedValueOnce(new Error("accept response lost"))
      .mockResolvedValueOnce(runtime("accepted-on-reconnect"));
    const transcribeTurn = vi
      .fn()
      .mockRejectedValue(new Error("final provider remains unavailable"));
    const { coordinator, onRecovered, onRuntime } = setup({
      acceptLiveTranscript,
      transcribeTurn,
    });

    coordinator.liveCompleted("item-1", "persisted live answer");
    coordinator.audioReady(utterance("item-1"));
    await flush();
    await expect(coordinator.awaitIdle()).rejects.toThrow(
      COORDINATOR_FATAL_MESSAGE,
    );

    await coordinator.retryRetained();

    expect(transcribeTurn).toHaveBeenCalledOnce();
    expect(acceptLiveTranscript).toHaveBeenCalledTimes(2);
    expect(onRuntime).toHaveBeenLastCalledWith(
      runtime("accepted-on-reconnect"),
    );
    expect(onRecovered).toHaveBeenCalledOnce();
  });

  it("recovers a committed final whose final and first accept responses were lost", async () => {
    const acceptLiveTranscript = vi
      .fn()
      .mockRejectedValueOnce(new Error("first accept response lost"))
      .mockResolvedValueOnce(runtime("already-final-runtime"));
    const transcribeTurn = vi
      .fn()
      .mockRejectedValueOnce(new Error("final response lost"));
    const { coordinator, onFatal, onRecovered, onRuntime } = setup({
      acceptLiveTranscript,
      transcribeTurn,
    });

    coordinator.liveCompleted("item-1", "late live no-op");
    coordinator.audioReady(utterance("item-1"));
    await flush();
    expect(onFatal).toHaveBeenCalledOnce();
    await expect(coordinator.awaitIdle()).rejects.toThrow(
      COORDINATOR_FATAL_MESSAGE,
    );

    await coordinator.retryRetained();

    expect(transcribeTurn).toHaveBeenCalledOnce();
    expect(acceptLiveTranscript).toHaveBeenCalledTimes(2);
    expect(onRuntime).toHaveBeenLastCalledWith(
      runtime("already-final-runtime"),
    );
    expect(onRecovered).toHaveBeenCalledOnce();
    await expect(coordinator.awaitIdle()).resolves.toBeUndefined();
    const owned = coordinator as unknown as {
      retained: Map<string, unknown>;
      turns: Map<string, unknown>;
    };
    expect(owned.retained.size).toBe(0);
    expect(owned.turns.size).toBe(0);
  });

  it("does not announce recovery when another turn becomes fatal during retry", async () => {
    const retry = deferred<InterviewRuntime>();
    const transcribeTurn = vi
      .fn()
      .mockRejectedValueOnce(new Error("item 1 failed"))
      .mockReturnValueOnce(retry.promise)
      .mockRejectedValueOnce(new Error("item 2 failed"));
    const { coordinator, onRecovered } = setup({ transcribeTurn });
    coordinator.liveFailed("item-1");
    coordinator.audioReady(utterance("item-1"));
    await flush();

    const reconnect = coordinator.retryRetained();
    coordinator.liveFailed("item-2");
    coordinator.audioReady(utterance("item-2"));
    await flush();
    retry.resolve(runtime("retried-item-1"));

    await expect(reconnect).rejects.toThrow(COORDINATOR_FATAL_MESSAGE);
    expect(onRecovered).not.toHaveBeenCalled();
    await expect(coordinator.awaitIdle()).rejects.toThrow(
      COORDINATOR_FATAL_MESSAGE,
    );
  });

  it("runs final uploads one at a time in bounded FIFO order", async () => {
    const uploads = [
      deferred<InterviewRuntime>(),
      deferred<InterviewRuntime>(),
    ];
    const transcribeTurn = vi
      .fn()
      .mockReturnValueOnce(uploads[0].promise)
      .mockReturnValueOnce(uploads[1].promise);
    const { coordinator, onFatal, onRuntime } = setup({ transcribeTurn }, 2);

    coordinator.audioReady(utterance("item-1"));
    coordinator.audioReady(utterance("item-2"));

    expect(transcribeTurn).toHaveBeenCalledOnce();
    expect(transcribeTurn).toHaveBeenNthCalledWith(
      1,
      "interview-1",
      "item-1",
      expect.objectContaining({ itemId: "item-1" }),
    );
    uploads[0].resolve(runtime("final-1"));
    await flush();
    expect(transcribeTurn).toHaveBeenCalledTimes(2);
    expect(transcribeTurn).toHaveBeenNthCalledWith(
      2,
      "interview-1",
      "item-2",
      expect.objectContaining({ itemId: "item-2" }),
    );
    uploads[1].resolve(runtime("final-2"));
    await coordinator.awaitIdle();

    expect(onRuntime.mock.calls.map(([value]) => value.status)).toEqual([
      "final-1",
      "final-2",
    ]);
    expect(onFatal).not.toHaveBeenCalled();
  });

  it("drains the FIFO before retrying retained overflow audio", async () => {
    const uploads = [
      deferred<InterviewRuntime>(),
      deferred<InterviewRuntime>(),
      deferred<InterviewRuntime>(),
    ];
    const transcribeTurn = vi
      .fn()
      .mockReturnValueOnce(uploads[0].promise)
      .mockReturnValueOnce(uploads[1].promise)
      .mockReturnValueOnce(uploads[2].promise);
    const { coordinator, onFatal, onRecovered } = setup({ transcribeTurn }, 2);

    coordinator.audioReady(utterance("item-1"));
    coordinator.audioReady(utterance("item-2"));
    coordinator.audioReady(utterance("item-3"));
    const reconnect = coordinator.retryRetained();

    expect(transcribeTurn).toHaveBeenCalledOnce();
    expect(onFatal).toHaveBeenCalledOnce();
    uploads[0].resolve(runtime("final-1"));
    await flush();
    expect(transcribeTurn).toHaveBeenNthCalledWith(
      2,
      "interview-1",
      "item-2",
      expect.objectContaining({ itemId: "item-2" }),
    );
    uploads[1].resolve(runtime("final-2"));
    await flush();
    expect(transcribeTurn).toHaveBeenNthCalledWith(
      3,
      "interview-1",
      "item-3",
      expect.objectContaining({ itemId: "item-3" }),
    );
    uploads[2].resolve(runtime("final-3"));
    await reconnect;
    expect(onRecovered).toHaveBeenCalledOnce();
  });

  it("waits for active work and rejects existing idle waiters on fatal state", async () => {
    const final = deferred<InterviewRuntime>();
    const { coordinator } = setup({
      transcribeTurn: vi.fn().mockReturnValue(final.promise),
    });
    coordinator.liveFailed("item-1");
    coordinator.audioReady(utterance("item-1"));

    const idle = coordinator.awaitIdle();
    let settled = false;
    void idle.then(
      () => {
        settled = true;
      },
      () => {
        settled = true;
      },
    );
    await flush();
    expect(settled).toBe(false);
    final.reject(new Error("final failed"));

    await expect(idle).rejects.toThrow(COORDINATOR_FATAL_MESSAGE);
  });

  it("disposal clears timers, active and retained Blob references", async () => {
    const hanging = deferred<InterviewRuntime>();
    const { coordinator, onFatal, onRuntime } = setup({
      transcribeTurn: vi
        .fn()
        .mockRejectedValueOnce(new Error("failed"))
        .mockReturnValueOnce(hanging.promise),
    });
    coordinator.liveFailed("retained");
    coordinator.audioReady(utterance("retained"));
    coordinator.audioReady(utterance("active"));
    await flush();

    coordinator.dispose();

    const owned = coordinator as unknown as {
      turns: Map<string, { utterance?: RecordedUtterance }>;
      retained: Map<string, RecordedUtterance>;
    };
    expect(owned.turns.size).toBe(0);
    expect(owned.retained.size).toBe(0);
    await vi.runAllTimersAsync();
    hanging.resolve(runtime("too-late"));
    await flush();
    expect(onRuntime).not.toHaveBeenCalled();
    expect(onFatal).toHaveBeenCalledOnce();
  });
});
