import { useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError } from "./api";
import {
  ArrowLeftIcon,
  CheckIcon,
  ClockIcon,
  HeadphonesIcon,
  MicrophoneIcon,
  RefreshIcon,
  SendIcon,
  StopIcon,
} from "./icons";
import {
  absoluteSpeechSegment,
  countUnicodeCharacters,
  prepareInputMedia,
  realtimeEventAcknowledgesTurn,
  RealtimeTransport,
  typedAnswerError,
  type RealtimeEvent,
} from "./realtime";
import {
  COORDINATOR_FATAL_MESSAGE,
  TranscriptionCoordinatorError,
  TurnTranscriptionCoordinator,
} from "./transcription";
import type {
  Capabilities,
  InputMode,
  InterviewRuntime,
  InterviewSession,
  InterviewTurn,
  InterviewTurnInput,
  InterviewType,
  SpeechSegmentInput,
} from "./types";
import {
  BufferedUtteranceRecorder,
  selectRecorderMimeType,
} from "./voiceCapture";

interface PracticePageProps {
  interview: InterviewSession;
  onBack: () => void;
  onInterviewUpdated: (interview: InterviewSession) => void;
}

type PagePhase = "preflight" | "interview" | "ended";
type ConnectionStatus =
  "idle" | "connecting" | "connected" | "reconnecting" | "failed";

interface PendingAnswer {
  client_turn_id: string;
  transcript: string;
  started_at: string;
}

/**
 * One fallback is routine and stays a quiet status line. A run of them means
 * the final deployment is broken, which previously degraded an entire interview
 * to live-quality text without ever saying so.
 */
const SYSTEMATIC_FALLBACK_THRESHOLD = 3;

const RECORDER_UNSUPPORTED_MESSAGE =
  "This browser cannot record answer audio for final transcription. Use a recent Chrome, Edge, or Safari.";

function missingTranscriptionDeployments(
  capabilities: Capabilities | null,
): string | null {
  if (!capabilities) return null;
  const missing: string[] = [];
  if (!capabilities.live_transcription_configured) missing.push("live");
  if (!capabilities.final_transcription_configured) missing.push("final");
  if (missing.length === 0) return null;
  const names = missing
    .map((lane) => `${lane} transcription deployment`)
    .join(" and the ");
  return `The ${names} is not configured. Voice interviews need both.`;
}

function isAcceptedLiveFallback(turn: InterviewTurn): boolean {
  return (
    turn.speaker === "user" &&
    turn.transcription_source === "realtime_live" &&
    Boolean(turn.transcription_finalized_at)
  );
}

function sessionValue<T>(key: string, fallback: T): T {
  try {
    const value = window.sessionStorage.getItem(key);
    return value ? (JSON.parse(value) as T) : fallback;
  } catch {
    return fallback;
  }
}

function formatTimer(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function randomTurnId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
}

export function PracticePage({
  interview,
  onBack,
  onInterviewUpdated,
}: PracticePageProps) {
  const draftKey = `interview-draft:${interview.id}`;
  const pendingKey = `interview-pending:${interview.id}`;
  const [phase, setPhase] = useState<PagePhase>("preflight");
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [inputMode, setInputMode] = useState<InputMode>(
    interview.input_mode ?? "voice",
  );
  const [duration, setDuration] = useState(interview.duration_minutes || 15);
  const [interviewType, setInterviewType] = useState<InterviewType>(
    interview.interview_type ?? "technical_behavioral",
  );
  const [tonePlayed, setTonePlayed] = useState(false);
  const [headphonesReady, setHeadphonesReady] = useState(false);
  const [microphoneReady, setMicrophoneReady] = useState(false);
  const [microphoneLabel, setMicrophoneLabel] = useState("");
  const [microphoneConsent, setMicrophoneConsent] = useState(false);
  const [deliveryConsent, setDeliveryConsent] = useState(false);
  const [runtime, setRuntime] = useState<InterviewRuntime | null>(null);
  const [connection, setConnection] = useState<ConnectionStatus>("idle");
  const [responseActive, setResponseActive] = useState(false);
  const [draft, setDraft] = useState(() => sessionValue<string>(draftKey, ""));
  const [answerError, setAnswerError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [liveAssistant, setLiveAssistant] = useState("");
  const [finalizing, setFinalizing] = useState(false);
  const [interviewEnded, setInterviewEnded] = useState(false);
  const [liveFallbackCount, setLiveFallbackCount] = useState(0);
  const [remainingSeconds, setRemainingSeconds] = useState(duration * 60);
  const audioRef = useRef<HTMLAudioElement>(null);
  const transcriptStreamRef = useRef<HTMLDivElement>(null);
  const mediaRef = useRef<MediaStream | null>(null);
  const transportRef = useRef<RealtimeTransport | null>(null);
  const pendingRef = useRef<PendingAnswer | null>(
    sessionValue<PendingAnswer | null>(pendingKey, null),
  );
  const assistantTranscriptRef = useRef("");
  const stoppedRef = useRef(false);
  const expirationHandledRef = useRef(false);
  const stopInterviewRef = useRef<() => Promise<void>>(async () => undefined);
  const initialConnectionRef = useRef(true);
  const responseTranscriptDoneRef = useRef(false);
  const responseAudioStoppedRef = useRef(false);
  const waitingForTurnAckRef = useRef(false);
  const connectionEpochRef = useRef(Date.now());
  const speechStartsRef = useRef(new Map<string, number>());
  const speechSegmentsRef = useRef(new Map<string, SpeechSegmentInput[]>());
  const recorderRef = useRef<BufferedUtteranceRecorder | null>(null);
  const recorderStreamRef = useRef<MediaStream | null>(null);
  const coordinatorRef = useRef<TurnTranscriptionCoordinator | null>(null);
  const retainedAssistantRef = useRef<InterviewTurnInput[]>([]);

  useEffect(() => {
    let active = true;
    Promise.all([api.capabilities(), api.runtime(interview.id)])
      .then(([nextCapabilities, nextRuntime]) => {
        if (!active) return;
        setCapabilities(nextCapabilities);
        setRuntime(nextRuntime);
        if (nextRuntime.started_at && !interview.ended_at) {
          setInputMode(nextRuntime.input_mode);
          setDuration(nextRuntime.duration_minutes);
          initialConnectionRef.current = false;
          setConnection("failed");
          setPhase("interview");
        }
        if (nextRuntime.status === "TRANSCRIPT_FINALIZING") setPhase("ended");
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(
            caught instanceof ApiError
              ? caught.message
              : "Interview preflight could not be loaded.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [interview.ended_at, interview.id]);

  useEffect(() => {
    window.sessionStorage.setItem(draftKey, JSON.stringify(draft));
  }, [draft, draftKey]);

  useEffect(() => {
    const stream = transcriptStreamRef.current;
    if (stream) stream.scrollTop = stream.scrollHeight;
  }, [liveAssistant, runtime?.turns]);

  useEffect(() => {
    if (!runtime?.ends_at || !runtime.server_now) return;
    const remainingAtSync = Math.max(
      0,
      new Date(runtime.ends_at).getTime() -
        new Date(runtime.server_now).getTime(),
    );
    const syncedAt = performance.now();
    const update = () => {
      const seconds = Math.max(
        0,
        Math.ceil((remainingAtSync - (performance.now() - syncedAt)) / 1000),
      );
      setRemainingSeconds(seconds);
      if (
        seconds === 0 &&
        phase === "interview" &&
        !expirationHandledRef.current
      ) {
        expirationHandledRef.current = true;
        void stopInterviewRef.current();
      }
    };
    update();
    const interval = window.setInterval(update, 1000);
    return () => window.clearInterval(interval);
  }, [phase, runtime?.ends_at, runtime?.server_now]);

  useEffect(
    () => () => {
      recorderRef.current?.stop();
      coordinatorRef.current?.dispose();
      transportRef.current?.close(true);
      for (const track of mediaRef.current?.getTracks() ?? []) track.stop();
    },
    [],
  );

  const maximumCharacters = capabilities?.typed_answer_max_characters ?? 20_000;
  const characterCount = countUnicodeCharacters(draft);
  const transcriptionWarning =
    inputMode === "voice"
      ? missingTranscriptionDeployments(capabilities)
      : null;
  const canStart =
    capabilities?.realtime_configured &&
    headphonesReady &&
    !transcriptionWarning &&
    (inputMode === "text_dev" || (microphoneConsent && microphoneReady));
  const transcript = runtime?.turns ?? [];

  async function playTestTone() {
    setError(null);
    try {
      const AudioContextClass =
        window.AudioContext ??
        (window as typeof window & { webkitAudioContext?: typeof AudioContext })
          .webkitAudioContext;
      if (!AudioContextClass) throw new Error("Audio output is unavailable.");
      const context = new AudioContextClass();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      gain.gain.value = 0.06;
      oscillator.frequency.value = 440;
      oscillator.connect(gain).connect(context.destination);
      oscillator.start();
      oscillator.stop(context.currentTime + 0.35);
      oscillator.addEventListener("ended", () => void context.close());
      setTonePlayed(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Audio test failed.");
    }
  }

  async function checkMicrophone() {
    setError(null);
    if (!selectRecorderMimeType()) {
      setMicrophoneReady(false);
      setError(RECORDER_UNSUPPORTED_MESSAGE);
      return;
    }
    try {
      mediaRef.current = await prepareInputMedia("voice", mediaRef.current);
      const [track] = mediaRef.current?.getAudioTracks() ?? [];
      setMicrophoneLabel(track?.label || "Default microphone");
      setMicrophoneReady(Boolean(track));
    } catch (caught) {
      setMicrophoneReady(false);
      setError(
        caught instanceof Error
          ? caught.message
          : "Microphone permission was not granted.",
      );
    }
  }

  function storePending(value: PendingAnswer | null) {
    pendingRef.current = value;
    if (value) window.sessionStorage.setItem(pendingKey, JSON.stringify(value));
    else window.sessionStorage.removeItem(pendingKey);
  }

  function beginResponse(waitForTurnAck = false) {
    responseTranscriptDoneRef.current = false;
    responseAudioStoppedRef.current = false;
    waitingForTurnAckRef.current = waitForTurnAck;
    setResponseActive(true);
  }

  function settleResponseIfComplete() {
    if (responseTranscriptDoneRef.current && responseAudioStoppedRef.current) {
      setResponseActive(false);
    }
  }

  function selectInputMode(nextMode: InputMode) {
    if (nextMode === "text_dev") {
      for (const track of mediaRef.current?.getTracks() ?? []) track.stop();
      mediaRef.current = null;
      setMicrophoneReady(false);
      setMicrophoneLabel("");
      setDeliveryConsent(false);
    }
    setInputMode(nextMode);
  }

  function applyRuntime(nextRuntime: InterviewRuntime) {
    setRuntime(nextRuntime);
    setLiveFallbackCount(
      nextRuntime.turns.filter(isAcceptedLiveFallback).length,
    );
    void flushDeliveryObservations(nextRuntime);
  }

  async function flushDeliveryObservations(nextRuntime: InterviewRuntime) {
    if (inputMode !== "voice" || !deliveryConsent) return;
    const items: Array<{
      turn_id: string;
      speech_segments: SpeechSegmentInput[];
    }> = [];
    for (const [clientTurnId, segments] of speechSegmentsRef.current) {
      if (!segments.length) continue;
      const savedTurn = nextRuntime.turns.find(
        (turn) => turn.client_turn_id === clientTurnId,
      );
      if (!savedTurn) continue;
      items.push({ turn_id: savedTurn.id, speech_segments: segments });
      speechSegmentsRef.current.delete(clientTurnId);
    }
    if (!items.length) return;
    try {
      await api.saveDeliveryObservations(interview.id, items);
    } catch {
      setError(
        "The transcript was saved, but delivery observations need a retry.",
      );
    }
  }

  async function persistTurn(item: InterviewTurnInput) {
    const nextRuntime = await api.saveTurns(interview.id, [item]);
    applyRuntime(nextRuntime);
    return nextRuntime;
  }

  function ensureTranscriptionUnits(stream: MediaStream) {
    if (!coordinatorRef.current) {
      coordinatorRef.current = new TurnTranscriptionCoordinator(
        interview.id,
        {
          saveTurns: (interviewId, items) => api.saveTurns(interviewId, items),
          transcribeTurn: (interviewId, clientTurnId, utterance) =>
            api.transcribeTurn(interviewId, clientTurnId, utterance),
          acceptLiveTranscript: (interviewId, clientTurnId) =>
            api.acceptLiveTranscript(interviewId, clientTurnId),
          recordTranscriptionEvent: (interviewId, kind) =>
            api.recordTranscriptionEvent(interviewId, kind),
        },
        {
          onRuntime: applyRuntime,
          onFatal: () => pauseForTranscription(),
          onRecovered: () => setError(null),
        },
      );
    }
    if (recorderRef.current && recorderStreamRef.current === stream) return;
    recorderRef.current?.stop();
    const recorder = new BufferedUtteranceRecorder(stream, {
      onUtterance: (utterance) => coordinatorRef.current?.audioReady(utterance),
      onError: (message) =>
        pauseForTranscription(`${message} Reconnect to retry this answer.`),
    });
    recorderRef.current = recorder;
    recorderStreamRef.current = stream;
    recorder.start();
  }

  function pauseForTranscription(reason?: string) {
    transportRef.current?.setMicrophoneEnabled(false);
    transportRef.current?.close(false);
    setFinalizing(false);
    setConnection("reconnecting");
    setError(reason ?? COORDINATOR_FATAL_MESSAGE);
    void api
      .connectionState(interview.id, "reconnecting")
      .catch((caught: unknown) => markEndedIfServerRefused(caught));
  }

  /**
   * The server only accepts a "reconnecting" transition while the interview is
   * still live. Once the timer has expired it answers 409 forever, so treating
   * that as retryable left the room offering a Reconnect button that could
   * never succeed. Reconcile to the ended state instead.
   */
  function markEndedIfServerRefused(caught: unknown): boolean {
    if (!(caught instanceof ApiError) || caught.status !== 409) return false;
    stoppedRef.current = true;
    transportRef.current?.setMicrophoneEnabled(false);
    transportRef.current?.close(true);
    setConnection("idle");
    setFinalizing(false);
    setError(
      "This interview has already ended. Your transcript is saved — open the report to continue.",
    );
    setInterviewEnded(true);
    return true;
  }

  async function flushRetainedAssistant() {
    const retained = retainedAssistantRef.current;
    if (!retained.length) return;
    retainedAssistantRef.current = [];
    try {
      for (const item of retained) await persistTurn(item);
    } catch {
      retainedAssistantRef.current = [
        ...retained,
        ...retainedAssistantRef.current,
      ];
      setError("The interviewer transcript was not saved. Reconnect to retry.");
    }
  }

  function persistAssistantTurn(item: InterviewTurnInput) {
    const coordinator = coordinatorRef.current;
    if (!coordinator) {
      void persistTurn(item).catch(() => {
        setError("The interviewer transcript needs a retry.");
      });
      return;
    }
    void (async () => {
      try {
        await coordinator.awaitIdle();
      } catch {
        retainedAssistantRef.current.push(item);
        return;
      }
      await persistTurn(item);
    })().catch(() => {
      setError("The interviewer transcript needs a retry.");
    });
  }

  function releaseMedia() {
    recorderRef.current?.stop();
    recorderRef.current = null;
    recorderStreamRef.current = null;
    coordinatorRef.current?.dispose();
    coordinatorRef.current = null;
    for (const track of mediaRef.current?.getTracks() ?? []) track.stop();
    mediaRef.current = null;
  }

  function handleRealtimeEvent(event: RealtimeEvent) {
    if (event.type === "error") {
      setError(event.error?.message ?? "Azure Realtime reported an error.");
      setResponseActive(false);
      return;
    }
    if (event.type === "input_audio_buffer.speech_started" && event.item_id) {
      recorderRef.current?.speechStarted(event.item_id);
      if (typeof event.audio_start_ms === "number") {
        speechStartsRef.current.set(event.item_id, event.audio_start_ms);
      }
      return;
    }
    if (event.type === "input_audio_buffer.speech_stopped" && event.item_id) {
      recorderRef.current?.speechStopped(event.item_id);
      const startedAt = speechStartsRef.current.get(event.item_id);
      if (
        typeof event.audio_end_ms === "number" &&
        startedAt !== undefined &&
        event.audio_end_ms > startedAt
      ) {
        const segments = speechSegmentsRef.current.get(event.item_id) ?? [];
        segments.push(
          absoluteSpeechSegment(
            startedAt,
            event.audio_end_ms,
            connectionEpochRef.current,
          ),
        );
        speechSegmentsRef.current.set(event.item_id, segments);
      }
      speechStartsRef.current.delete(event.item_id);
      return;
    }
    if (
      event.type === "conversation.item.input_audio_transcription.failed" &&
      event.item_id
    ) {
      coordinatorRef.current?.liveFailed(event.item_id);
      return;
    }
    if (
      pendingRef.current &&
      realtimeEventAcknowledgesTurn(
        event,
        pendingRef.current.client_turn_id,
        pendingRef.current.transcript,
      )
    ) {
      const pending = pendingRef.current;
      storePending(null);
      waitingForTurnAckRef.current = false;
      if (pending) {
        void persistTurn({
          ...pending,
          speaker: "user",
          delivery_status: "acknowledged",
        }).catch(() => {
          setError(
            "The answer was accepted, but transcript sync needs a retry.",
          );
        });
      }
      return;
    }
    if (
      event.type === "conversation.item.input_audio_transcription.completed" &&
      event.transcript?.trim()
    ) {
      const clientTurnId = event.item_id ?? randomTurnId("voice");
      const coordinator = coordinatorRef.current;
      if (coordinator) {
        coordinator.liveCompleted(clientTurnId, event.transcript);
        return;
      }
      void persistTurn({
        client_turn_id: clientTurnId,
        speaker: "user",
        transcript: event.transcript ?? "",
        delivery_status: "acknowledged",
      }).catch(() => {
        setError("The transcript was not saved. Reconnect to retry.");
      });
      return;
    }
    if (event.type === "response.output_audio_transcript.delta") {
      if (waitingForTurnAckRef.current) return;
      setResponseActive(true);
      assistantTranscriptRef.current += event.delta ?? "";
      setLiveAssistant(assistantTranscriptRef.current);
      return;
    }
    if (event.type === "response.output_audio_transcript.done") {
      if (waitingForTurnAckRef.current) return;
      const text = event.transcript || assistantTranscriptRef.current;
      assistantTranscriptRef.current = "";
      setLiveAssistant("");
      if (text.trim()) {
        persistAssistantTurn({
          client_turn_id: event.item_id ?? randomTurnId("assistant"),
          speaker: "assistant",
          transcript: text,
          delivery_status: "acknowledged",
        });
      }
      responseTranscriptDoneRef.current = true;
      settleResponseIfComplete();
      return;
    }
    if (event.type === "output_audio_buffer.started") {
      if (waitingForTurnAckRef.current) return;
      responseAudioStoppedRef.current = false;
      setResponseActive(true);
    }
    if (event.type === "output_audio_buffer.stopped") {
      if (waitingForTurnAckRef.current) return;
      responseAudioStoppedRef.current = true;
      settleResponseIfComplete();
    }
  }

  async function connect() {
    if (!capabilities || !audioRef.current || interviewEnded) return;
    setError(null);
    setConnection(initialConnectionRef.current ? "connecting" : "reconnecting");
    setPhase("interview");
    stoppedRef.current = false;
    try {
      await api.updateDeliveryConsent(
        interview.id,
        inputMode === "voice" && deliveryConsent,
      );
      mediaRef.current = await prepareInputMedia(inputMode, mediaRef.current);
      if (inputMode === "voice") {
        if (!mediaRef.current) {
          throw new Error("Microphone preflight is incomplete.");
        }
        if (!selectRecorderMimeType()) {
          throw new Error(RECORDER_UNSUPPORTED_MESSAGE);
        }
        ensureTranscriptionUnits(mediaRef.current);
      }
      if (runtime?.started_at && runtime.status === "IN_PROGRESS") {
        try {
          await api.connectionState(interview.id, "reconnecting");
        } catch (caught) {
          if (markEndedIfServerRefused(caught)) return;
          throw caught;
        }
      }
      const secret = await api.realtimeClientSecret(
        interview.id,
        inputMode,
        duration,
        interviewType,
      );
      const transport = new RealtimeTransport({
        onEvent: handleRealtimeEvent,
        onError: setError,
        onStateChange: (state) => {
          if (stoppedRef.current) return;
          if (state === "failed" || state === "disconnected") {
            setConnection("reconnecting");
            void api
              .connectionState(interview.id, "reconnecting")
              .catch((caught: unknown) => markEndedIfServerRefused(caught));
          }
          if (state === "closed") setConnection("failed");
        },
        onReady: () => {
          void (async () => {
            const shouldStartInterview = initialConnectionRef.current;
            const coordinator = coordinatorRef.current;
            if (coordinator) {
              transport.setMicrophoneEnabled(false);
              await coordinator.retryRetained();
              await flushRetainedAssistant();
              transport.setMicrophoneEnabled(true);
            }
            const connectedRuntime = await api.connectionState(
              interview.id,
              "connected",
            );
            applyRuntime(connectedRuntime);
            setConnection("connected");
            const pending = pendingRef.current;
            const acknowledged = pending
              ? connectedRuntime.turns.some(
                  (turn) =>
                    turn.client_turn_id === pending.client_turn_id &&
                    turn.delivery_status === "acknowledged",
                )
              : false;
            if (acknowledged) storePending(null);
            else if (pending) {
              transport.sendTypedAnswer(
                pending.transcript,
                pending.client_turn_id,
              );
              beginResponse(true);
            } else if (shouldStartInterview) {
              transport.startInterview();
              beginResponse();
            }
            initialConnectionRef.current = false;
          })().catch((caught: unknown) => {
            if (caught instanceof TranscriptionCoordinatorError) {
              pauseForTranscription();
              return;
            }
            setError(
              caught instanceof Error
                ? caught.message
                : "The interview could not start.",
            );
          });
        },
      });
      connectionEpochRef.current = Date.now();
      transportRef.current = transport;
      await transport.connect({
        token: secret.client_secret,
        callsUrl: secret.calls_url,
        inputMode,
        audioElement: audioRef.current,
        mediaStream: mediaRef.current,
      });
    } catch (caught) {
      if (markEndedIfServerRefused(caught)) return;
      setConnection("failed");
      void api.connectionState(interview.id, "failed").catch(() => undefined);
      setError(
        caught instanceof ApiError || caught instanceof Error
          ? caught.message
          : "The Realtime interview could not connect.",
      );
    }
  }

  async function submitTypedAnswer() {
    const validation = typedAnswerError(draft, maximumCharacters);
    setAnswerError(validation);
    if (validation || responseActive || connection !== "connected") return;
    const pending: PendingAnswer = {
      client_turn_id: randomTurnId("item"),
      transcript: draft,
      started_at: new Date().toISOString(),
    };
    storePending(pending);
    beginResponse(true);
    try {
      await persistTurn({
        ...pending,
        speaker: "user",
        delivery_status: "pending",
      });
      transportRef.current?.sendTypedAnswer(
        pending.transcript,
        pending.client_turn_id,
      );
      setDraft("");
      window.sessionStorage.removeItem(draftKey);
    } catch (caught) {
      setResponseActive(false);
      setError(
        caught instanceof Error ? caught.message : "The answer was not sent.",
      );
    }
  }

  async function stopInterview() {
    if (finalizing) return;
    stoppedRef.current = true;
    transportRef.current?.setMicrophoneEnabled(false);
    setFinalizing(true);
    await recorderRef.current?.finish();
    transportRef.current?.close(false);
    const coordinator = coordinatorRef.current;
    if (coordinator) {
      try {
        await coordinator.awaitIdle();
      } catch {
        pauseForTranscription();
        return;
      }
      await flushRetainedAssistant();
    }
    setConnection("idle");
    try {
      await api.completeInterview(interview.id);
      onInterviewUpdated(await api.interview(interview.id));
      releaseMedia();
      setPhase("ended");
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The interview did not stop.",
      );
    } finally {
      setFinalizing(false);
    }
  }

  stopInterviewRef.current = stopInterview;

  const statusLabel = useMemo(() => {
    if (finalizing) return "Finalizing your transcript";
    if (connection === "connected" && responseActive) return "AI speaking";
    if (connection === "connected") return "Listening";
    if (connection === "reconnecting") return "Connection interrupted";
    if (connection === "failed") return "Reconnect required";
    return connection === "connecting" ? "Connecting" : "Ready";
  }, [connection, finalizing, responseActive]);

  return (
    <main className="canvas practice-canvas">
      <audio ref={audioRef} autoPlay aria-label="AI interviewer audio" />
      {phase === "preflight" ? (
        <>
          <button
            className="btn btn--ghost back-button"
            type="button"
            onClick={onBack}
          >
            <ArrowLeftIcon /> Back to setup
          </button>
          <section className="page-header practice-header">
            <div>
              <p className="section__eyebrow">Interview preflight</p>
              <h1 className="section__title">Ready your audio</h1>
              <p className="section__lede">
                A short check now prevents interruptions once the timer starts.
              </p>
            </div>
            <span className="badge">15–60 min</span>
          </section>

          {error ? (
            <div className="error-state" role="alert">
              <strong>{error}</strong>
            </div>
          ) : null}
          <section className="preflight-grid">
            <div className="card preflight-main">
              <div className="preflight-section">
                <div className="preflight-section__heading">
                  <span className="preflight-icon">
                    <HeadphonesIcon />
                  </span>
                  <div>
                    <h2>Headphones</h2>
                    <p>AI responses use audio in both modes.</p>
                  </div>
                  {headphonesReady ? <CheckIcon /> : null}
                </div>
                <div className="preflight-actions">
                  <button className="btn" type="button" onClick={playTestTone}>
                    Play test sound
                  </button>
                  {tonePlayed ? (
                    <button
                      className="btn btn--primary"
                      type="button"
                      onClick={() => setHeadphonesReady(true)}
                    >
                      I heard it
                    </button>
                  ) : null}
                </div>
              </div>

              <div className="preflight-section">
                <div className="preflight-section__heading">
                  <span className="preflight-icon">
                    <MicrophoneIcon />
                  </span>
                  <div>
                    <h2>
                      {inputMode === "text_dev"
                        ? "Microphone off"
                        : "Microphone"}
                    </h2>
                    <p>
                      {inputMode === "text_dev"
                        ? "No permission or media track will be requested."
                        : microphoneLabel ||
                          "Check permission and input device."}
                    </p>
                  </div>
                  {inputMode === "text_dev" || microphoneReady ? (
                    <CheckIcon />
                  ) : null}
                </div>
                {inputMode === "voice" ? (
                  <button
                    className="btn"
                    type="button"
                    disabled={!microphoneConsent}
                    onClick={checkMicrophone}
                  >
                    Check microphone
                  </button>
                ) : (
                  <span className="dev-mode-note">
                    Camera and microphone stay off
                  </span>
                )}
              </div>
            </div>

            <aside
              className="card preflight-config"
              aria-label="Interview settings"
            >
              <p className="section__eyebrow">Session</p>
              <fieldset className="field mode-fieldset">
                <legend className="field__label">Input mode</legend>
                <div className="mode-selector">
                  <button
                    className={`mode-option ${inputMode === "voice" ? "is-active" : ""}`}
                    type="button"
                    aria-pressed={inputMode === "voice"}
                    onClick={() => selectInputMode("voice")}
                  >
                    Voice
                  </button>
                  {capabilities?.text_dev_mode_enabled ? (
                    <button
                      className={`mode-option ${inputMode === "text_dev" ? "is-active" : ""}`}
                      type="button"
                      aria-pressed={inputMode === "text_dev"}
                      onClick={() => selectInputMode("text_dev")}
                    >
                      Developer text
                    </button>
                  ) : null}
                </div>
              </fieldset>
              <label className="field">
                <span className="field__label">Duration</span>
                <select
                  className="select"
                  value={duration}
                  onChange={(event) => setDuration(Number(event.target.value))}
                >
                  {(capabilities?.supported_durations ?? [15, 30, 45, 60]).map(
                    (minutes) => (
                      <option value={minutes} key={minutes}>
                        {minutes} minutes
                      </option>
                    ),
                  )}
                </select>
              </label>
              <label className="field">
                <span className="field__label">Interview type</span>
                <select
                  className="select"
                  value={interviewType}
                  onChange={(event) =>
                    setInterviewType(event.target.value as InterviewType)
                  }
                >
                  <option value="technical_behavioral">
                    Technical + behavioral
                  </option>
                  <option value="technical">Technical</option>
                  <option value="behavioral">Behavioral</option>
                </select>
              </label>
              {inputMode === "voice" ? (
                <div className="consent-stack">
                  <label className="consent-option">
                    <input
                      type="checkbox"
                      checked={microphoneConsent}
                      onChange={(event) =>
                        setMicrophoneConsent(event.target.checked)
                      }
                    />
                    <span>
                      <strong>
                        Allow microphone access for this interview
                      </strong>
                      <small>
                        Audio is processed in memory for live and final
                        transcription, sent to the configured Azure provider,
                        and not retained by this app.
                      </small>
                    </span>
                  </label>
                  <label className="consent-option">
                    <input
                      type="checkbox"
                      checked={deliveryConsent}
                      onChange={(event) =>
                        setDeliveryConsent(event.target.checked)
                      }
                    />
                    <span>
                      <strong>Add speaking-delivery coaching</strong>
                      <small>
                        Opt in to pace, pauses, fillers, response timing, and
                        answer-length observations. These never change your
                        role-fit score.
                      </small>
                    </span>
                  </label>
                </div>
              ) : (
                <p className="delivery-unavailable-note">
                  Speaking-delivery coaching is unavailable with developer text
                  input. No delivery score is assigned.
                </p>
              )}
              {!capabilities?.realtime_configured ? (
                <p className="preflight-warning">
                  Realtime deployment is not configured.
                </p>
              ) : null}
              {transcriptionWarning ? (
                <p className="preflight-warning">{transcriptionWarning}</p>
              ) : null}
              <button
                className="btn btn--primary preflight-start"
                type="button"
                disabled={!canStart}
                onClick={connect}
              >
                Start interview
              </button>
            </aside>
          </section>
        </>
      ) : null}

      {phase === "interview" ? (
        <section className="interview-room">
          <header className="interview-bar">
            <div role="status" aria-live="polite">
              <span className={`connection-dot is-${connection}`} />
              <strong>{statusLabel}</strong>
              {inputMode === "text_dev" ? (
                <span className="dev-mode-badge">Developer text input</span>
              ) : null}
            </div>
            <div className="interview-clock">
              <ClockIcon size={16} />
              <strong>{formatTimer(remainingSeconds)}</strong>
            </div>
            <div className="interview-controls">
              {!interviewEnded &&
              (connection === "reconnecting" || connection === "failed") ? (
                <button className="btn btn--sm" type="button" onClick={connect}>
                  <RefreshIcon size={16} /> Reconnect
                </button>
              ) : null}
              <button
                className="btn btn--sm stop-button"
                type="button"
                disabled={finalizing}
                onClick={stopInterview}
              >
                <StopIcon size={16} /> Stop
              </button>
            </div>
          </header>

          {error ? (
            <div className="error-state interview-error" role="alert">
              <strong>{error}</strong>
              <button
                className="btn btn--sm"
                type="button"
                onClick={() => void audioRef.current?.play()}
              >
                Play audio
              </button>
            </div>
          ) : null}
          {liveFallbackCount >= SYSTEMATIC_FALLBACK_THRESHOLD ? (
            <p className="transcription-notice is-alert" role="alert">
              Using live transcript for {liveFallbackCount} answers. Final
              transcription is failing repeatedly, so this transcript is less
              accurate than usual. Check the final transcription deployment
              before relying on the report.
            </p>
          ) : liveFallbackCount > 0 ? (
            <p className="transcription-notice" role="status">
              Using live transcript for{" "}
              {liveFallbackCount === 1 ? "one answer" : "some answers"}. The
              interview is still connected.
            </p>
          ) : null}
          <div className="interview-layout">
            <section
              className="card transcript-panel"
              aria-label="Live transcript"
            >
              <div className="transcript-heading">
                <div>
                  <p className="section__eyebrow">Live transcript</p>
                  <h1>{interview.title}</h1>
                </div>
                <span>{transcript.length} turns</span>
              </div>
              <div
                className="transcript-stream"
                aria-live="polite"
                ref={transcriptStreamRef}
              >
                {transcript.length === 0 && !liveAssistant ? (
                  <div className="transcript-empty">
                    The interviewer will begin when the connection is ready.
                  </div>
                ) : null}
                {transcript.map((turn) => (
                  <article
                    className={`transcript-turn is-${turn.speaker}`}
                    key={turn.id}
                  >
                    <span>
                      {turn.speaker === "assistant" ? "Interviewer" : "You"}
                    </span>
                    <p>{turn.transcript}</p>
                    {turn.delivery_status === "pending" ? (
                      <small>Sending…</small>
                    ) : null}
                  </article>
                ))}
                {liveAssistant ? (
                  <article className="transcript-turn is-assistant is-live">
                    <span>Interviewer</span>
                    <p>{liveAssistant}</p>
                  </article>
                ) : null}
              </div>
            </section>

            <aside className="card answer-panel">
              {inputMode === "text_dev" ? (
                <>
                  <div className="answer-heading">
                    <div>
                      <p className="section__eyebrow">Your answer</p>
                      <h2>Draft while listening</h2>
                    </div>
                    <span
                      className={
                        characterCount > maximumCharacters ? "is-over" : ""
                      }
                    >
                      {characterCount.toLocaleString()} /{" "}
                      {maximumCharacters.toLocaleString()}
                    </span>
                  </div>
                  <textarea
                    aria-label="Your interview answer"
                    className="textarea answer-editor"
                    value={draft}
                    onChange={(event) => {
                      setDraft(event.target.value);
                      setAnswerError(null);
                    }}
                    onKeyDown={(event) => {
                      if (
                        (event.ctrlKey || event.metaKey) &&
                        event.key === "Enter"
                      ) {
                        event.preventDefault();
                        void submitTypedAnswer();
                      }
                    }}
                    placeholder="Write a detailed answer. Enter adds a new line…"
                    aria-describedby="answer-help answer-error"
                  />
                  <div id="answer-help" className="answer-help">
                    <span>Ctrl/Cmd + Enter to submit</span>
                    <span>Paragraphs, code, and Unicode are preserved</span>
                  </div>
                  {answerError ? (
                    <p id="answer-error" className="weight-error" role="alert">
                      {answerError}
                    </p>
                  ) : null}
                  <button
                    className="btn btn--primary answer-submit"
                    type="button"
                    disabled={
                      responseActive ||
                      connection !== "connected" ||
                      characterCount > maximumCharacters ||
                      !draft.trim()
                    }
                    onClick={submitTypedAnswer}
                  >
                    <SendIcon size={16} />{" "}
                    {responseActive ? "Wait for interviewer" : "Submit answer"}
                  </button>
                  <p className="delivery-note">
                    Speaking and video delivery metrics are unavailable in
                    developer mode.
                  </p>
                </>
              ) : (
                <div className="voice-active">
                  <span className="voice-orb">
                    <MicrophoneIcon />
                  </span>
                  <h2>
                    {responseActive ? "Interviewer speaking" : "Your turn"}
                  </h2>
                  <p>
                    Speak naturally. Server voice activity detection sends the
                    answer when you pause.
                  </p>
                </div>
              )}
            </aside>
          </div>
        </section>
      ) : null}

      {phase === "ended" ? (
        <section className="card interview-ended">
          <span className="preflight-icon">
            <CheckIcon />
          </span>
          <p className="section__eyebrow">Interview complete</p>
          <h1>Transcript saved</h1>
          <p>
            Evidence-backed evaluation and the coaching report arrive in M4.
          </p>
          <button className="btn btn--primary" type="button" onClick={onBack}>
            Return to session
          </button>
        </section>
      ) : null}
    </main>
  );
}
