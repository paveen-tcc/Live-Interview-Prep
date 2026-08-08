const ALLOWED_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
] as const;

const DEFAULT_PREBUFFER_CHUNKS = 6;
const DEFAULT_TAIL_MS = 300;
const DEFAULT_MAX_QUEUED = 2;

export interface RecordedUtterance {
  itemId: string;
  blob: Blob;
  mediaType: string;
  startedAt?: string;
  endedAt?: string;
}

interface Segment {
  chunks: Blob[];
  startedAt: string;
  stopped: boolean;
  tailTimer?: ReturnType<typeof setTimeout>;
}

interface RecorderCallbacks {
  onUtterance: (utterance: RecordedUtterance) => void;
  onError: (message: string) => void;
}

interface RecorderOptions {
  prebufferChunks?: number;
  tailMs?: number;
  maxQueued?: number;
}

export function selectRecorderMimeType(): string | null {
  if (typeof MediaRecorder === "undefined") {
    return null;
  }

  return (
    ALLOWED_MIME_TYPES.find((mimeType) =>
      MediaRecorder.isTypeSupported(mimeType),
    ) ?? null
  );
}

export class BufferedUtteranceRecorder {
  private readonly prebufferChunks: number;
  private readonly tailMs: number;
  private readonly maxQueued: number;
  private readonly prebuffer: Blob[] = [];
  private readonly segments = new Map<string, Segment>();

  private recorder: MediaRecorder | null = null;
  private mediaType: string | null = null;
  private finishing = false;
  private finishResolve: (() => void) | null = null;
  private finishPromise: Promise<void> | null = null;

  constructor(
    private readonly stream: MediaStream,
    private readonly callbacks: RecorderCallbacks,
    options: RecorderOptions = {},
  ) {
    this.prebufferChunks = options.prebufferChunks ?? DEFAULT_PREBUFFER_CHUNKS;
    this.tailMs = options.tailMs ?? DEFAULT_TAIL_MS;
    this.maxQueued = options.maxQueued ?? DEFAULT_MAX_QUEUED;
  }

  start(): void {
    if (this.recorder) {
      return;
    }

    const mediaType = selectRecorderMimeType();
    if (!mediaType) {
      this.callbacks.onError("Audio capture is not supported in this browser.");
      return;
    }

    try {
      const recorder = new MediaRecorder(this.stream, { mimeType: mediaType });
      recorder.ondataavailable = (event) => this.captureChunk(event.data);
      recorder.start(250);
      this.recorder = recorder;
      this.mediaType = mediaType;
    } catch {
      this.callbacks.onError("Audio capture is not supported in this browser.");
    }
  }

  speechStarted(itemId: string): void {
    if (!this.recorder || this.finishing || this.segments.has(itemId)) {
      return;
    }

    if (this.segments.size >= this.maxQueued) {
      this.callbacks.onError(
        "Too many candidate utterances are waiting to be captured.",
      );
      return;
    }

    const chunks = this.prebuffer.splice(0);
    this.segments.set(itemId, {
      chunks,
      startedAt: new Date().toISOString(),
      stopped: false,
    });
  }

  speechStopped(itemId: string): void {
    const segment = this.segments.get(itemId);
    if (!segment || segment.stopped) {
      return;
    }

    this.closeSegment(itemId, segment);
  }

  finish(): Promise<void> {
    if (this.finishPromise) {
      return this.finishPromise;
    }

    if (!this.recorder) {
      return Promise.resolve();
    }

    this.finishing = true;
    const finishPromise = new Promise<void>((resolve) => {
      this.finishResolve = resolve;
    });
    this.finishPromise = finishPromise;

    for (const [itemId, segment] of this.segments) {
      if (!segment.stopped) {
        this.closeSegment(itemId, segment);
      }
    }

    if (this.recorder.state === "recording") {
      this.recorder.requestData();
    }

    if (this.segments.size === 0) {
      this.stopRecording();
    }

    return finishPromise;
  }

  stop(): void {
    this.stopRecording();
  }

  private captureChunk(chunk: Blob): void {
    if (!this.recorder || chunk.size === 0) {
      return;
    }

    if (this.segments.size === 0) {
      this.prebuffer.push(chunk);
      if (this.prebuffer.length > this.prebufferChunks) {
        this.prebuffer.shift();
      }
      return;
    }

    for (const segment of this.segments.values()) {
      segment.chunks.push(chunk);
    }
  }

  private closeSegment(itemId: string, segment: Segment): void {
    segment.stopped = true;
    segment.tailTimer = setTimeout(() => {
      this.emitSegment(itemId, segment);
    }, this.tailMs);
  }

  private emitSegment(itemId: string, segment: Segment): void {
    if (this.segments.get(itemId) !== segment) {
      return;
    }

    if (segment.tailTimer) {
      clearTimeout(segment.tailTimer);
    }
    this.segments.delete(itemId);

    const chunks = segment.chunks;
    segment.chunks = [];
    const mediaType = this.mediaType ?? "audio/webm";
    const utterance: RecordedUtterance = {
      itemId,
      blob: new Blob(chunks, { type: mediaType }),
      mediaType,
      startedAt: segment.startedAt,
      endedAt: new Date().toISOString(),
    };
    chunks.length = 0;

    try {
      this.callbacks.onUtterance(utterance);
    } finally {
      if (this.finishing && this.segments.size === 0) {
        this.stopRecording();
      }
    }
  }

  private stopRecording(): void {
    const recorder = this.recorder;
    this.recorder = null;
    this.mediaType = null;

    for (const segment of this.segments.values()) {
      if (segment.tailTimer) {
        clearTimeout(segment.tailTimer);
      }
      segment.chunks.length = 0;
    }
    this.segments.clear();
    this.prebuffer.length = 0;
    this.finishing = false;

    const resolve = this.finishResolve;
    this.finishResolve = null;
    this.finishPromise = null;

    if (recorder) {
      recorder.ondataavailable = null;
      recorder.onstop = null;
      if (recorder.state !== "inactive") {
        recorder.stop();
      }
    }

    resolve?.();
  }
}
