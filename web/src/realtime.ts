import type { InputMode } from "./types";

export interface RealtimeEvent {
  type: string;
  item_id?: string;
  content_index?: number;
  transcript?: string;
  delta?: string;
  audio_start_ms?: number;
  audio_end_ms?: number;
  item?: {
    id?: string;
    role?: string;
    content?: Array<{ type?: string; text?: string; transcript?: string }>;
  };
  error?: {
    type?: string;
    code?: string;
    message?: string;
    param?: string | null;
  };
}

export function absoluteSpeechSegment(
  startedAtMs: number,
  endedAtMs: number,
  connectionEpochMs: number,
) {
  return {
    started_at: new Date(connectionEpochMs + startedAtMs).toISOString(),
    ended_at: new Date(connectionEpochMs + endedAtMs).toISOString(),
  };
}

export interface RealtimeTransportCallbacks {
  onEvent: (event: RealtimeEvent) => void;
  onStateChange: (state: RTCPeerConnectionState) => void;
  onReady: () => void;
  onError: (message: string) => void;
}

export function countUnicodeCharacters(value: string): number {
  return Array.from(value).length;
}

export function typedAnswerError(
  value: string,
  maximum: number,
): string | null {
  if (!value.trim()) return "Write an answer before submitting.";
  const length = countUnicodeCharacters(value);
  if (length > maximum) {
    return `Your answer is ${length.toLocaleString()} characters. The limit is ${maximum.toLocaleString()}.`;
  }
  return null;
}

export function buildTypedAnswerEvents(text: string, itemId: string) {
  return [
    {
      type: "conversation.item.create",
      event_id: itemId,
      item: {
        type: "message",
        role: "user",
        content: [{ type: "input_text", text }],
      },
    },
    { type: "response.create" },
  ];
}

export function realtimeEventAcknowledgesTurn(
  event: RealtimeEvent,
  itemId: string,
  transcript: string,
): boolean {
  if (
    event.type !== "conversation.item.created" &&
    event.type !== "conversation.item.added"
  ) {
    return false;
  }
  if (event.item?.role && event.item.role !== "user") return false;
  if (event.item?.id === itemId) return true;
  return (
    event.item?.content?.some(
      (content) => content.type === "input_text" && content.text === transcript,
    ) ?? false
  );
}

export async function prepareInputMedia(
  inputMode: InputMode,
  existing?: MediaStream | null,
): Promise<MediaStream | null> {
  if (inputMode === "text_dev") {
    for (const track of existing?.getTracks() ?? []) track.stop();
    return null;
  }
  if (existing?.active) return existing;
  for (const track of existing?.getTracks() ?? []) track.stop();
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("This browser cannot access a microphone.");
  }
  return navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: false,
  });
}

export class RealtimeTransport {
  private peer: RTCPeerConnection | null = null;
  private channel: RTCDataChannel | null = null;

  constructor(private readonly callbacks: RealtimeTransportCallbacks) {}

  async connect(options: {
    token: string;
    callsUrl: string;
    inputMode: InputMode;
    audioElement: HTMLAudioElement;
    mediaStream: MediaStream | null;
  }): Promise<void> {
    this.close(false);
    const peer = new RTCPeerConnection();
    this.peer = peer;
    peer.onconnectionstatechange = () => {
      this.callbacks.onStateChange(peer.connectionState);
    };
    peer.ontrack = (event) => {
      const [stream] = event.streams;
      if (stream) options.audioElement.srcObject = stream;
      void options.audioElement.play().catch(() => {
        this.callbacks.onError(
          "Audio is ready but playback was blocked. Press Play audio.",
        );
      });
    };
    if (options.inputMode === "voice") {
      if (!options.mediaStream) {
        throw new Error("Microphone preflight is incomplete.");
      }
      for (const track of options.mediaStream.getAudioTracks()) {
        peer.addTrack(track, options.mediaStream);
      }
    } else {
      peer.addTransceiver("audio", { direction: "recvonly" });
    }

    const channel = peer.createDataChannel("realtime-channel");
    this.channel = channel;
    channel.addEventListener("open", this.callbacks.onReady);
    channel.addEventListener("message", (message) => {
      try {
        this.callbacks.onEvent(JSON.parse(message.data) as RealtimeEvent);
      } catch {
        this.callbacks.onError("A malformed Realtime event was ignored.");
      }
    });
    channel.addEventListener("error", () => {
      this.callbacks.onError("The Realtime data channel reported an error.");
    });

    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    const response = await fetch(options.callsUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${options.token}`,
        "Content-Type": "application/sdp",
      },
      body: offer.sdp,
    });
    if (!response.ok) {
      throw new Error(`Realtime connection failed (${response.status}).`);
    }
    await peer.setRemoteDescription({
      type: "answer",
      sdp: await response.text(),
    });
  }

  send(event: object): void {
    if (this.channel?.readyState !== "open") {
      throw new Error("The Realtime connection is not ready.");
    }
    this.channel.send(JSON.stringify(event));
  }

  sendTypedAnswer(text: string, itemId: string): void {
    for (const event of buildTypedAnswerEvents(text, itemId)) this.send(event);
  }

  startInterview(): void {
    this.send({ type: "response.create" });
  }

  setMicrophoneEnabled(enabled: boolean): void {
    for (const sender of this.peer?.getSenders() ?? []) {
      if (sender.track?.kind === "audio") sender.track.enabled = enabled;
    }
  }

  close(stopMedia = false): void {
    this.channel?.close();
    if (stopMedia && this.peer) {
      for (const sender of this.peer.getSenders()) sender.track?.stop();
    }
    this.peer?.close();
    this.channel = null;
    this.peer = null;
  }
}
