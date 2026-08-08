const ALLOWED_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/mp4",
  "audio/ogg;codecs=opus",
] as const;

/**
 * Every container these recorders produce is a header followed by a stream of
 * independently-framed media units. `MediaRecorder` emits that header exactly
 * once, in its first chunk. An utterance assembled from later chunks is
 * therefore headerless and undecodable — which is why final transcription
 * rejected every candidate answer while the recorder's own tests passed: they
 * asserted which chunks landed in a segment, never that the result decoded.
 *
 * These readers locate where the header ends so it can be retained once and
 * prepended to every utterance, without duplicating any audio.
 */
function findWebmInitLength(bytes: Uint8Array): number {
  // The header runs until the first Cluster element (ID 0x1F43B675).
  for (let index = 0; index + 3 < bytes.length; index += 1) {
    if (
      bytes[index] === 0x1f &&
      bytes[index + 1] === 0x43 &&
      bytes[index + 2] === 0xb6 &&
      bytes[index + 3] === 0x75
    ) {
      return index;
    }
  }
  return 0;
}

function findMp4InitLength(bytes: Uint8Array): number {
  // Walk top-level boxes; ftyp + moov are the header, the first moof starts media.
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 0;
  while (offset + 8 <= bytes.length) {
    let size = view.getUint32(offset);
    const type = String.fromCharCode(
      bytes[offset + 4],
      bytes[offset + 5],
      bytes[offset + 6],
      bytes[offset + 7],
    );
    if (type === "moof" || type === "mdat") return offset;
    if (size === 1) {
      if (offset + 16 > bytes.length) return 0;
      size = Number(view.getBigUint64(offset + 8));
    }
    if (size <= 0) return 0;
    offset += size;
  }
  return 0;
}

function findOggInitLength(bytes: Uint8Array): number {
  // OpusHead and OpusTags occupy the first two pages; audio begins at the third.
  let pages = 0;
  for (let index = 0; index + 3 < bytes.length; index += 1) {
    if (
      bytes[index] === 0x4f &&
      bytes[index + 1] === 0x67 &&
      bytes[index + 2] === 0x67 &&
      bytes[index + 3] === 0x53
    ) {
      pages += 1;
      if (pages === 3) return index;
    }
  }
  return 0;
}

/** jsdom's Blob predates `arrayBuffer`, so fall back to FileReader there. */
async function readBlobBytes(blob: Blob): Promise<Uint8Array> {
  if (typeof blob.arrayBuffer === "function") {
    return new Uint8Array(await blob.arrayBuffer());
  }
  return new Promise<Uint8Array>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

export function initSegmentLength(
  mediaType: string,
  bytes: Uint8Array,
): number {
  const type = mediaType.toLowerCase();
  if (type.includes("webm")) return findWebmInitLength(bytes);
  if (type.includes("mp4")) return findMp4InitLength(bytes);
  if (type.includes("ogg")) return findOggInitLength(bytes);
  return 0;
}

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
  private initSegment: Blob | null = null;
  private initPending: Promise<void> | null = null;
  private pendingDeliveries = 0;

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
      recorder.onerror = () => {
        this.stopRecording();
        this.callbacks.onError("Audio capture failed.");
      };
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

    if (this.segments.size === 0 && this.pendingDeliveries === 0) {
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

    if (this.initPending === null) {
      // The first chunk carries the container header. Its ~250ms of audio
      // precedes any speech, so only the header is kept.
      this.initPending = this.retainInitSegment(chunk);
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
    this.pendingDeliveries += 1;
    void this.deliverUtterance(itemId, chunks, segment.startedAt);
  }

  private async retainInitSegment(chunk: Blob): Promise<void> {
    const mediaType = this.mediaType ?? ALLOWED_MIME_TYPES[0];
    try {
      const bytes = await readBlobBytes(chunk);
      const length = initSegmentLength(mediaType, bytes);
      // A container this reader does not recognise keeps the whole first chunk:
      // a decodable utterance with a duplicated opening beats an undecodable one.
      this.initSegment = length > 0 ? chunk.slice(0, length) : chunk;
    } catch {
      this.initSegment = chunk;
    }
  }

  private async deliverUtterance(
    itemId: string,
    chunks: Blob[],
    startedAt: string,
  ): Promise<void> {
    try {
      await this.initPending;
      const mediaType = this.mediaType ?? ALLOWED_MIME_TYPES[0];
      const parts = this.initSegment ? [this.initSegment, ...chunks] : chunks;
      const utterance: RecordedUtterance = {
        itemId,
        blob: new Blob(parts, { type: mediaType }),
        mediaType,
        startedAt,
        endedAt: new Date().toISOString(),
      };
      chunks.length = 0;
      this.callbacks.onUtterance(utterance);
    } finally {
      this.pendingDeliveries -= 1;
      if (
        this.finishing &&
        this.segments.size === 0 &&
        this.pendingDeliveries === 0
      ) {
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
    this.initSegment = null;
    this.initPending = null;
    this.finishing = false;

    const resolve = this.finishResolve;
    this.finishResolve = null;
    this.finishPromise = null;

    if (recorder) {
      recorder.ondataavailable = null;
      recorder.onerror = null;
      recorder.onstop = null;
      if (recorder.state !== "inactive") {
        recorder.stop();
      }
    }

    resolve?.();
  }
}
