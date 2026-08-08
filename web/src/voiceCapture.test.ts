import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BufferedUtteranceRecorder,
  selectRecorderMimeType,
} from "./voiceCapture";

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];

  static supportedMimeTypes = new Set<string>([
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
  ]);

  static isTypeSupported(mimeType: string): boolean {
    return this.supportedMimeTypes.has(mimeType);
  }

  readonly start = vi.fn((timeslice?: number) => {
    this.state = "recording";
    this.timeslice = timeslice;
  });

  readonly stop = vi.fn(() => {
    this.state = "inactive";
    this.onstop?.(new Event("stop"));
  });

  readonly requestData = vi.fn();

  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onstop: ((event: Event) => void) | null = null;
  state: RecordingState = "inactive";
  timeslice?: number;

  constructor(
    readonly stream: MediaStream,
    readonly options?: MediaRecorderOptions,
  ) {
    FakeMediaRecorder.instances.push(this);
  }

  emit(blob: Blob): void {
    this.ondataavailable?.({ data: blob } as BlobEvent);
  }
}

const stream = {} as MediaStream;

function callbacks() {
  return { onUtterance: vi.fn(), onError: vi.fn() };
}

describe("BufferedUtteranceRecorder", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeMediaRecorder.instances = [];
    FakeMediaRecorder.supportedMimeTypes = new Set([
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/mp4",
    ]);
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("prefers Opus WebM when several recording MIME types are available", () => {
    expect(selectRecorderMimeType()).toBe("audio/webm;codecs=opus");
  });

  it("reports an unsupported browser when no allowlisted MIME type is available", () => {
    FakeMediaRecorder.supportedMimeTypes.clear();
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);

    expect(selectRecorderMimeType()).toBeNull();
    recorder.start();

    expect(handlers.onError).toHaveBeenCalledWith(
      "Audio capture is not supported in this browser.",
    );
    expect(FakeMediaRecorder.instances).toHaveLength(0);
  });

  it("uses one recorder for the supplied stream and sends candidate audio after its tail", () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);

    recorder.start();
    recorder.speechStarted("voice-item-1");
    FakeMediaRecorder.instances[0].emit(
      new Blob(["answer"], { type: "audio/webm" }),
    );
    recorder.speechStopped("voice-item-1");
    vi.advanceTimersByTime(300);

    expect(FakeMediaRecorder.instances).toHaveLength(1);
    expect(FakeMediaRecorder.instances[0].stream).toBe(stream);
    expect(FakeMediaRecorder.instances[0].start).toHaveBeenCalledWith(250);
    expect(handlers.onUtterance).toHaveBeenCalledWith(
      expect.objectContaining({
        itemId: "voice-item-1",
        mediaType: "audio/webm;codecs=opus",
      }),
    );
  });

  it("includes retained pre-speech audio in the candidate utterance", () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const fakeRecorder = FakeMediaRecorder.instances[0];

    fakeRecorder.emit(new Blob(["before"], { type: "audio/webm" }));
    recorder.speechStarted("voice-item-1");
    fakeRecorder.emit(new Blob(["during"], { type: "audio/webm" }));
    recorder.speechStopped("voice-item-1");
    vi.advanceTimersByTime(300);

    const utterance = handlers.onUtterance.mock.calls[0][0];
    expect(utterance.blob.size).toBe(12);
  });

  it("keeps no more than six idle chunks before speech begins", () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const fakeRecorder = FakeMediaRecorder.instances[0];

    for (const word of ["one", "two", "six", "ten", "red", "ink", "last"]) {
      fakeRecorder.emit(new Blob([word], { type: "audio/webm" }));
    }
    recorder.speechStarted("voice-item-1");
    recorder.speechStopped("voice-item-1");
    vi.advanceTimersByTime(300);

    expect(handlers.onUtterance.mock.calls[0][0].blob.size).toBe(19);
  });

  it("ignores a stop notification for another VAD item", () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const fakeRecorder = FakeMediaRecorder.instances[0];

    recorder.speechStarted("voice-item-1");
    fakeRecorder.emit(new Blob(["answer"], { type: "audio/webm" }));
    recorder.speechStopped("other-item");
    vi.advanceTimersByTime(300);

    expect(handlers.onUtterance).not.toHaveBeenCalled();
  });

  it("rejects a new VAD item when the bounded segment queue is full", () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers, {
      maxQueued: 1,
    });
    recorder.start();

    recorder.speechStarted("voice-item-1");
    recorder.speechStarted("voice-item-2");

    expect(handlers.onError).toHaveBeenCalledWith(
      "Too many candidate utterances are waiting to be captured.",
    );
  });

  it("finish emits an active item once before stopping the recorder", async () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const fakeRecorder = FakeMediaRecorder.instances[0];
    recorder.speechStarted("voice-item-1");
    fakeRecorder.emit(new Blob(["answer"], { type: "audio/webm" }));

    const finished = recorder.finish();
    vi.advanceTimersByTime(300);
    await finished;

    expect(handlers.onUtterance).toHaveBeenCalledTimes(1);
    expect(fakeRecorder.stop).toHaveBeenCalledOnce();
  });

  it("finish returns a resolving promise and stops an idle recorder", async () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const fakeRecorder = FakeMediaRecorder.instances[0];

    await expect(recorder.finish()).resolves.toBeUndefined();

    expect(fakeRecorder.stop).toHaveBeenCalledOnce();
  });

  it("cancels delayed and stale chunks when stopped before a tail completes", () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const firstRecorder = FakeMediaRecorder.instances[0];
    recorder.speechStarted("voice-item-1");
    firstRecorder.emit(new Blob(["discard"], { type: "audio/webm" }));
    recorder.speechStopped("voice-item-1");
    recorder.stop();
    firstRecorder.emit(new Blob(["also-discard"], { type: "audio/webm" }));
    vi.advanceTimersByTime(300);

    recorder.start();
    const secondRecorder = FakeMediaRecorder.instances[1];
    recorder.speechStarted("voice-item-2");
    secondRecorder.emit(new Blob(["keep"], { type: "audio/webm" }));
    recorder.speechStopped("voice-item-2");
    vi.advanceTimersByTime(300);

    expect(firstRecorder.stop).toHaveBeenCalledOnce();
    expect(handlers.onUtterance).toHaveBeenCalledTimes(1);
    expect(handlers.onUtterance.mock.calls[0][0]).toMatchObject({
      itemId: "voice-item-2",
    });
    expect(handlers.onUtterance.mock.calls[0][0].blob.size).toBe(4);
  });
});
