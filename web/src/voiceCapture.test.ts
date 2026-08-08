import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BufferedUtteranceRecorder,
  initSegmentLength,
  selectRecorderMimeType,
} from "./voiceCapture";

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];

  static supportedMimeTypes = new Set<string>([
    "audio/webm;codecs=opus",
    "audio/mp4",
    "audio/ogg;codecs=opus",
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
  onerror: ((event: Event) => void) | null = null;
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

  fail(): void {
    this.onerror?.(new Event("error"));
  }
}

const stream = {} as MediaStream;

/**
 * A WebM-shaped first chunk: six header bytes, then the Cluster ID that marks
 * where media begins. `MediaRecorder` only ever emits this header once, so the
 * recorder must retain it and prepend it to every later utterance.
 */
const HEADER_CHUNK = new Uint8Array([
  0x1a, 0x45, 0xdf, 0xa3, 0x01, 0x02, 0x1f, 0x43, 0xb6, 0x75, 0xaa, 0xbb,
]);
const INIT_BYTES = 6;

function callbacks() {
  return { onUtterance: vi.fn(), onError: vi.fn() };
}

function emitHeader(recorder: FakeMediaRecorder): void {
  recorder.emit(new Blob([HEADER_CHUNK], { type: "audio/webm" }));
}

async function settle(ms = 300): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
}

/** jsdom Blobs have no arrayBuffer, and Response stringifies them. */
async function readBytes(blob: Blob): Promise<Uint8Array> {
  return new Promise<Uint8Array>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

describe("BufferedUtteranceRecorder", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeMediaRecorder.instances = [];
    FakeMediaRecorder.supportedMimeTypes = new Set([
      "audio/webm;codecs=opus",
      "audio/mp4",
      "audio/ogg;codecs=opus",
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

  it("falls back to Opus Ogg after WebM and MP4 are unavailable", () => {
    FakeMediaRecorder.supportedMimeTypes = new Set(["audio/ogg;codecs=opus"]);

    expect(selectRecorderMimeType()).toBe("audio/ogg;codecs=opus");
  });

  it("reports an unsupported browser when no allowlisted MIME type is available", () => {
    FakeMediaRecorder.supportedMimeTypes = new Set(["audio/webm"]);
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);

    expect(selectRecorderMimeType()).toBeNull();
    recorder.start();

    expect(handlers.onError).toHaveBeenCalledWith(
      "Audio capture is not supported in this browser.",
    );
    expect(FakeMediaRecorder.instances).toHaveLength(0);
  });

  it("uses one recorder for the supplied stream and sends candidate audio after its tail", async () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);

    recorder.start();
    emitHeader(FakeMediaRecorder.instances[0]);
    recorder.speechStarted("voice-item-1");
    FakeMediaRecorder.instances[0].emit(
      new Blob(["answer"], { type: "audio/webm" }),
    );
    recorder.speechStopped("voice-item-1");
    await settle();

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

  it("prepends the retained header to every utterance, not just the first", async () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const fakeRecorder = FakeMediaRecorder.instances[0];
    emitHeader(fakeRecorder);

    for (const itemId of ["voice-item-1", "voice-item-2"]) {
      recorder.speechStarted(itemId);
      fakeRecorder.emit(new Blob(["answer"], { type: "audio/webm" }));
      recorder.speechStopped(itemId);
      await settle();
    }

    expect(handlers.onUtterance).toHaveBeenCalledTimes(2);
    const [first, second] = handlers.onUtterance.mock.calls.map(
      (call) => call[0],
    );
    // Both carry the six header bytes plus their own six bytes of audio. The
    // second utterance is the one that used to ship headerless and undecodable.
    expect(first.blob.size).toBe(INIT_BYTES + 6);
    expect(second.blob.size).toBe(INIT_BYTES + 6);
    // FileReader is driven by real timers; the fake clock would never fire it.
    vi.useRealTimers();
    const leading = (await readBytes(second.blob)).slice(0, INIT_BYTES);
    expect(Array.from(leading)).toEqual(
      Array.from(HEADER_CHUNK.slice(0, INIT_BYTES)),
    );
  });

  it("includes retained pre-speech audio in the candidate utterance", async () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const fakeRecorder = FakeMediaRecorder.instances[0];
    emitHeader(fakeRecorder);

    fakeRecorder.emit(new Blob(["before"], { type: "audio/webm" }));
    recorder.speechStarted("voice-item-1");
    fakeRecorder.emit(new Blob(["during"], { type: "audio/webm" }));
    recorder.speechStopped("voice-item-1");
    await settle();

    const utterance = handlers.onUtterance.mock.calls[0][0];
    expect(utterance.blob.size).toBe(INIT_BYTES + 12);
  });

  it("keeps no more than six idle chunks before speech begins", async () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const fakeRecorder = FakeMediaRecorder.instances[0];
    emitHeader(fakeRecorder);

    for (const word of ["one", "two", "six", "ten", "red", "ink", "last"]) {
      fakeRecorder.emit(new Blob([word], { type: "audio/webm" }));
    }
    recorder.speechStarted("voice-item-1");
    recorder.speechStopped("voice-item-1");
    await settle();

    expect(handlers.onUtterance.mock.calls[0][0].blob.size).toBe(
      INIT_BYTES + 19,
    );
  });

  it("ignores a stop notification for another VAD item", async () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const fakeRecorder = FakeMediaRecorder.instances[0];
    emitHeader(fakeRecorder);

    recorder.speechStarted("voice-item-1");
    fakeRecorder.emit(new Blob(["answer"], { type: "audio/webm" }));
    recorder.speechStopped("other-item");
    await settle();

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
    emitHeader(fakeRecorder);
    recorder.speechStarted("voice-item-1");
    fakeRecorder.emit(new Blob(["answer"], { type: "audio/webm" }));

    const finished = recorder.finish();
    await settle();
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

  it("cancels delayed and stale chunks when stopped before a tail completes", async () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const firstRecorder = FakeMediaRecorder.instances[0];
    emitHeader(firstRecorder);
    recorder.speechStarted("voice-item-1");
    firstRecorder.emit(new Blob(["discard"], { type: "audio/webm" }));
    recorder.speechStopped("voice-item-1");
    recorder.stop();
    firstRecorder.emit(new Blob(["also-discard"], { type: "audio/webm" }));
    await settle();

    recorder.start();
    const secondRecorder = FakeMediaRecorder.instances[1];
    emitHeader(secondRecorder);
    recorder.speechStarted("voice-item-2");
    secondRecorder.emit(new Blob(["keep"], { type: "audio/webm" }));
    recorder.speechStopped("voice-item-2");
    await settle();

    expect(firstRecorder.stop).toHaveBeenCalledOnce();
    expect(handlers.onUtterance).toHaveBeenCalledTimes(1);
    expect(handlers.onUtterance.mock.calls[0][0]).toMatchObject({
      itemId: "voice-item-2",
    });
    // A restarted recorder emits a fresh header, so the new utterance is
    // self-contained rather than inheriting the stopped recorder's.
    expect(handlers.onUtterance.mock.calls[0][0].blob.size).toBe(
      INIT_BYTES + 4,
    );
  });

  it("clears buffered audio and stale handlers before reporting a recorder error", async () => {
    const handlers = callbacks();
    const recorder = new BufferedUtteranceRecorder(stream, handlers);
    recorder.start();
    const firstRecorder = FakeMediaRecorder.instances[0];
    emitHeader(firstRecorder);

    firstRecorder.emit(new Blob(["prebuffer"], { type: "audio/webm" }));
    recorder.speechStarted("voice-item-1");
    firstRecorder.emit(new Blob(["discard"], { type: "audio/webm" }));
    recorder.speechStopped("voice-item-1");
    firstRecorder.fail();
    firstRecorder.emit(new Blob(["stale"], { type: "audio/webm" }));
    await settle();

    recorder.start();
    const secondRecorder = FakeMediaRecorder.instances[1];
    emitHeader(secondRecorder);
    recorder.speechStarted("voice-item-2");
    secondRecorder.emit(new Blob(["keep"], { type: "audio/webm" }));
    recorder.speechStopped("voice-item-2");
    await settle();

    expect(firstRecorder.stop).toHaveBeenCalledOnce();
    expect(handlers.onError).toHaveBeenCalledWith("Audio capture failed.");
    expect(handlers.onUtterance).toHaveBeenCalledTimes(1);
    expect(handlers.onUtterance.mock.calls[0][0]).toMatchObject({
      itemId: "voice-item-2",
    });
    expect(handlers.onUtterance.mock.calls[0][0].blob.size).toBe(
      INIT_BYTES + 4,
    );
  });
});

describe("initSegmentLength", () => {
  it("ends a WebM header at the first Cluster", () => {
    const bytes = new Uint8Array([
      0x1a, 0x45, 0xdf, 0xa3, 0x99, 0x1f, 0x43, 0xb6, 0x75, 0x01,
    ]);

    expect(initSegmentLength("audio/webm;codecs=opus", bytes)).toBe(5);
  });

  it("ends an MP4 header at the first fragment box", () => {
    const box = (size: number, type: string, filler = 0) => {
      const bytes = new Uint8Array(size).fill(filler);
      new DataView(bytes.buffer).setUint32(0, size);
      for (let index = 0; index < 4; index += 1) {
        bytes[4 + index] = type.charCodeAt(index);
      }
      return bytes;
    };
    const bytes = new Uint8Array([
      ...box(16, "ftyp"),
      ...box(24, "moov"),
      ...box(12, "moof"),
    ]);

    expect(initSegmentLength("audio/mp4", bytes)).toBe(40);
  });

  it("ends an Ogg header at the third page", () => {
    const page = (marker: string) => [
      0x4f,
      0x67,
      0x67,
      0x53,
      marker.charCodeAt(0),
      0x00,
    ];
    const bytes = new Uint8Array([...page("h"), ...page("t"), ...page("a")]);

    expect(initSegmentLength("audio/ogg;codecs=opus", bytes)).toBe(12);
  });

  it("reports no header for an unrecognised container", () => {
    expect(initSegmentLength("audio/aiff", new Uint8Array([1, 2, 3]))).toBe(0);
  });
});
