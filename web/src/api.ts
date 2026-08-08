import type {
  ApiErrorPayload,
  CandidateProfile,
  Capabilities,
  DeliveryCoaching,
  DeliveryConsent,
  InterviewList,
  InterviewReport,
  EvaluationStatus,
  InterviewRuntime,
  InterviewSession,
  InterviewSetup,
  InterviewTurnInput,
  InterviewType,
  InputMode,
  JobTarget,
  RealtimeClientSecret,
  ResumeUpload,
  Scorecard,
  ScorecardCompetency,
  Seniority,
  SpeechSegmentInput,
  User,
  UsageSummary,
} from "./types";
import type { RecordedUtterance } from "./voiceCapture";

export type TranscriptionEventKind =
  | "live_transcription_completed"
  | "live_transcription_failed"
  | "double_transcription_failure";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly errorId?: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Supplies the bearer token for API calls. Registered by the Clerk provider at
 * startup; left unset in local development, where the backend derives identity
 * from configuration instead of a token.
 */
export type AuthTokenProvider = () => Promise<string | null>;

let authTokenProvider: AuthTokenProvider | null = null;

export function setAuthTokenProvider(provider: AuthTokenProvider | null): void {
  authTokenProvider = provider;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const token = authTokenProvider ? await authTokenProvider() : null;
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body && !isFormData
        ? { "Content-Type": "application/json" }
        : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const payload = (await response
      .json()
      .catch(() => ({}))) as ApiErrorPayload;
    throw new ApiError(
      payload.error?.message ?? "The request could not be completed.",
      payload.error?.id ?? response.headers.get("X-Error-ID") ?? undefined,
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  capabilities: () => request<Capabilities>("/api/capabilities"),
  currentUser: () => request<User>("/api/auth/me"),
  interviews: () => request<InterviewList>("/api/interviews"),
  createInterview: () =>
    request<InterviewSession>("/api/interviews", {
      method: "POST",
      body: JSON.stringify({ title: "Untitled practice session" }),
    }),
  deleteInterview: (interviewId: string) =>
    request<void>(`/api/interviews/${interviewId}`, {
      method: "DELETE",
      body: JSON.stringify({ confirmation: "DELETE" }),
    }),
  usageSummary: () => request<UsageSummary>("/api/operations/usage"),
  deleteAccount: () =>
    request<void>("/api/account", {
      method: "DELETE",
      body: JSON.stringify({ confirmation: "DELETE MY ACCOUNT" }),
    }),
  interview: (interviewId: string) =>
    request<InterviewSession>(`/api/interviews/${interviewId}`),
  setup: (interviewId: string) =>
    request<InterviewSetup>(`/api/interviews/${interviewId}/setup`),
  uploadResume: (interviewId: string, file: File) => {
    const body = new FormData();
    body.append("interview_id", interviewId);
    body.append("file", file);
    return request<ResumeUpload>("/api/uploads/resume", {
      method: "POST",
      body,
    });
  },
  extractProfile: (
    interviewId: string,
    uploadId: string,
    replaceExisting = false,
  ) =>
    request<CandidateProfile>("/api/candidate-profiles/extract", {
      method: "POST",
      body: JSON.stringify({
        interview_id: interviewId,
        upload_id: uploadId,
        replace_existing: replaceExisting,
      }),
    }),
  updateProfile: (profile: CandidateProfile) =>
    request<CandidateProfile>(`/api/candidate-profiles/${profile.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        headline: profile.headline,
        claims: profile.claims.map(({ id, text }) => ({ id, text })),
      }),
    }),
  createJobTarget: (
    interviewId: string,
    title: string,
    seniority: Seniority,
    rawDescription: string,
  ) =>
    request<JobTarget>("/api/job-targets", {
      method: "POST",
      body: JSON.stringify({
        interview_id: interviewId,
        title,
        seniority,
        raw_description: rawDescription,
      }),
    }),
  generateScorecard: (interviewId: string, jobTargetId: string) =>
    request<Scorecard>("/api/scorecards/generate", {
      method: "POST",
      body: JSON.stringify({
        interview_id: interviewId,
        job_target_id: jobTargetId,
      }),
    }),
  updateScorecard: (scorecardId: string, competencies: ScorecardCompetency[]) =>
    request<Scorecard>(`/api/scorecards/${scorecardId}`, {
      method: "PATCH",
      body: JSON.stringify({
        competencies: competencies.map((competency) => ({
          id: competency.id,
          name: competency.name,
          description: competency.description,
          weight: competency.weight,
          classification: competency.classification,
          seniority_expectation: competency.seniority_expectation,
          evidence_to_collect: competency.evidence_to_collect,
          question_families: competency.question_families,
        })),
      }),
    }),
  realtimeClientSecret: (
    interviewId: string,
    inputMode: InputMode,
    durationMinutes: number,
    interviewType: InterviewType,
  ) =>
    request<RealtimeClientSecret>(
      `/api/interviews/${interviewId}/realtime-client-secret`,
      {
        method: "POST",
        body: JSON.stringify({
          input_mode: inputMode,
          duration_minutes: durationMinutes,
          interview_type: interviewType,
        }),
      },
    ),
  connectionState: (
    interviewId: string,
    state: "connected" | "reconnecting" | "failed",
  ) =>
    request<InterviewRuntime>(
      `/api/interviews/${interviewId}/connection-state`,
      {
        method: "POST",
        body: JSON.stringify({ state }),
      },
    ),
  runtime: (interviewId: string) =>
    request<InterviewRuntime>(`/api/interviews/${interviewId}/runtime`),
  updateDeliveryConsent: (interviewId: string, enabled: boolean) =>
    request<DeliveryConsent>(
      `/api/interviews/${interviewId}/delivery-consent`,
      {
        method: "POST",
        body: JSON.stringify({
          enabled,
          consent_version: "delivery-v1",
        }),
      },
    ),
  deliveryCoaching: (interviewId: string) =>
    request<DeliveryCoaching>(
      `/api/interviews/${interviewId}/delivery-coaching`,
    ),
  saveDeliveryObservations: (
    interviewId: string,
    items: Array<{ turn_id: string; speech_segments: SpeechSegmentInput[] }>,
  ) =>
    request<DeliveryCoaching>(
      `/api/interviews/${interviewId}/delivery-observations`,
      {
        method: "POST",
        body: JSON.stringify({ items }),
      },
    ),
  deleteDeliveryMetrics: (interviewId: string) =>
    request<DeliveryCoaching>(
      `/api/interviews/${interviewId}/delivery-metrics`,
      { method: "DELETE" },
    ),
  report: (interviewId: string) =>
    request<InterviewReport>(`/api/interviews/${interviewId}/report`),
  evaluate: (interviewId: string) =>
    request<EvaluationStatus>(`/api/interviews/${interviewId}/evaluate`, {
      method: "POST",
    }),
  saveTurns: (interviewId: string, items: InterviewTurnInput[]) =>
    request<InterviewRuntime>(`/api/interviews/${interviewId}/turns:batch`, {
      method: "POST",
      body: JSON.stringify({ items }),
    }),
  transcribeTurn: async (
    interviewId: string,
    clientTurnId: string,
    utterance: RecordedUtterance,
  ) => {
    const body = new FormData();
    body.append("file", utterance.blob);
    if (utterance.startedAt) body.append("started_at", utterance.startedAt);
    if (utterance.endedAt) body.append("ended_at", utterance.endedAt);
    return request<InterviewRuntime>(
      `/api/interviews/${interviewId}/turns/${clientTurnId}:transcribe`,
      { method: "POST", body },
    );
  },
  acceptLiveTranscript: async (interviewId: string, clientTurnId: string) => {
    return request<InterviewRuntime>(
      `/api/interviews/${interviewId}/turns/${clientTurnId}:accept-live`,
      { method: "POST" },
    );
  },
  recordTranscriptionEvent: (
    interviewId: string,
    kind: TranscriptionEventKind,
  ) =>
    request<void>(`/api/interviews/${interviewId}/transcription-events`, {
      method: "POST",
      body: JSON.stringify({ kind }),
    }),
  completeInterview: (interviewId: string) =>
    request<InterviewRuntime>(`/api/interviews/${interviewId}/complete`, {
      method: "POST",
    }),
};
