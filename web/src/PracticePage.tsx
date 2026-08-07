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
import type {
  Capabilities,
  InputMode,
  InterviewRuntime,
  InterviewSession,
  InterviewTurnInput,
  InterviewType,
  SpeechSegmentInput,
} from "./types";

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
      transportRef.current?.close(true);
      for (const track of mediaRef.current?.getTracks() ?? []) track.stop();
    },
    [],
  );

  const maximumCharacters = capabilities?.typed_answer_max_characters ?? 20_000;
  const characterCount = countUnicodeCharacters(draft);
  const canStart =
    capabilities?.realtime_configured &&
    headphonesReady &&
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

  async function persistTurn(item: InterviewTurnInput) {
    const nextRuntime = await api.saveTurns(interview.id, [item]);
    setRuntime(nextRuntime);
    return nextRuntime;
  }

  function handleRealtimeEvent(event: RealtimeEvent) {
    if (event.type === "error") {
      setError(event.error?.message ?? "Azure Realtime reported an error.");
      setResponseActive(false);
      return;
    }
    if (
      event.type === "input_audio_buffer.speech_started" &&
      event.item_id &&
      typeof event.audio_start_ms === "number"
    ) {
      speechStartsRef.current.set(event.item_id, event.audio_start_ms);
      return;
    }
    if (
      event.type === "input_audio_buffer.speech_stopped" &&
      event.item_id &&
      typeof event.audio_end_ms === "number"
    ) {
      const startedAt = speechStartsRef.current.get(event.item_id);
      if (startedAt !== undefined && event.audio_end_ms > startedAt) {
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
      void (async () => {
        const nextRuntime = await persistTurn({
          client_turn_id: clientTurnId,
          speaker: "user",
          transcript: event.transcript ?? "",
          delivery_status: "acknowledged",
        });
        const savedTurn = nextRuntime.turns.find(
          (turn) => turn.client_turn_id === clientTurnId,
        );
        const speechSegments =
          speechSegmentsRef.current.get(clientTurnId) ?? [];
        if (
          inputMode === "voice" &&
          deliveryConsent &&
          savedTurn &&
          speechSegments.length
        ) {
          await api.saveDeliveryObservations(interview.id, [
            { turn_id: savedTurn.id, speech_segments: speechSegments },
          ]);
          speechSegmentsRef.current.delete(clientTurnId);
        }
      })().catch(() => {
        setError(
          "The transcript was saved, but delivery observations need a retry.",
        );
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
        void persistTurn({
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
    if (!capabilities || !audioRef.current) return;
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
      if (runtime?.started_at && runtime.status === "IN_PROGRESS") {
        await api.connectionState(interview.id, "reconnecting");
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
            void api.connectionState(interview.id, "reconnecting");
          }
          if (state === "closed") setConnection("failed");
        },
        onReady: () => {
          void (async () => {
            const shouldStartInterview = initialConnectionRef.current;
            const connectedRuntime = await api.connectionState(
              interview.id,
              "connected",
            );
            setRuntime(connectedRuntime);
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
    stoppedRef.current = true;
    transportRef.current?.close(true);
    for (const track of mediaRef.current?.getTracks() ?? []) track.stop();
    mediaRef.current = null;
    setConnection("idle");
    try {
      await api.completeInterview(interview.id);
      onInterviewUpdated(await api.interview(interview.id));
      setPhase("ended");
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The interview did not stop.",
      );
    }
  }

  stopInterviewRef.current = stopInterview;

  const statusLabel = useMemo(() => {
    if (connection === "connected" && responseActive) return "AI speaking";
    if (connection === "connected") return "Listening";
    if (connection === "reconnecting") return "Connection interrupted";
    if (connection === "failed") return "Reconnect required";
    return connection === "connecting" ? "Connecting" : "Ready";
  }, [connection, responseActive]);

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
                        Audio goes directly to Azure Realtime and is not stored
                        by this app.
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
              {connection === "reconnecting" || connection === "failed" ? (
                <button className="btn btn--sm" type="button" onClick={connect}>
                  <RefreshIcon size={16} /> Reconnect
                </button>
              ) : null}
              <button
                className="btn btn--sm stop-button"
                type="button"
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
