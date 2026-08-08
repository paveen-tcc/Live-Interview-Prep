export interface User {
  id: string;
  email: string;
  display_name: string;
}

export interface UsageSummary {
  period_started_at: string;
  daily_interview_quota: number;
  daily_interviews_used: number;
  events: Record<string, number>;
  estimated_cost_usd: string;
  cost_status: "estimated" | "unavailable";
  transcript_retention_days: number;
  delivery_metrics_retention_days: number;
}

export interface InterviewSession {
  id: string;
  title: string;
  status: string;
  profile_id: string | null;
  scorecard_id: string | null;
  duration_minutes: number;
  interview_type: InterviewType;
  input_mode: InputMode;
  started_at: string | null;
  ended_at: string | null;
  prompt_version: string | null;
  created_at: string;
  updated_at: string;
}

export type InputMode = "voice" | "text_dev";
export type InterviewType = "technical_behavioral" | "technical" | "behavioral";

export interface Capabilities {
  text_dev_mode_enabled: boolean;
  realtime_configured: boolean;
  live_transcription_configured: boolean;
  final_transcription_configured: boolean;
  typed_answer_max_characters: number;
  supported_durations: number[];
}

export type DeliveryCoachingStatus =
  "collecting" | "available" | "unavailable" | "disabled" | "deleted";

export interface DeliveryConsent {
  interview_id: string;
  status: DeliveryCoachingStatus;
  consented: boolean;
  consent_version: string | null;
  unavailable_reason:
    "consent_required" | "text_input_mode" | "no_observations" | null;
  baseline: DeliveryBaseline | null;
  metrics: DeliveryMetric[];
  observations: DeliveryObservation[];
  suggestions: string[];
}

export interface DeliveryBaseline {
  turn_count: number;
  turn_ids: string[];
  words_per_minute: number;
  filler_words_per_100_words: number;
  average_pause_ms: number | null;
  average_response_delay_ms: number | null;
}

export interface DeliveryMetric {
  turn_id: string;
  sequence: number;
  word_count: number;
  speaking_duration_ms: number;
  answer_duration_ms: number;
  words_per_minute: number;
  pause_count: number;
  total_pause_ms: number;
  longest_pause_ms: number;
  filler_count: number;
  fillers_per_100_words: number;
  response_delay_ms: number | null;
  interruption_count: number | null;
}

export interface DeliveryObservation {
  turn_id: string;
  category:
    | "pace"
    | "pauses"
    | "fillers"
    | "response_delay"
    | "interruptions"
    | "answer_length";
  text: string;
}

export interface SpeechSegmentInput {
  started_at: string;
  ended_at: string;
}

export type DeliveryCoaching = DeliveryConsent;

export interface RealtimeClientSecret {
  client_secret: string;
  expires_at: number;
  calls_url: string;
  input_mode: InputMode;
  prompt_version: string;
}

export interface InterviewTurn {
  id: string;
  client_turn_id: string;
  sequence: number;
  speaker: "user" | "assistant";
  transcript: string;
  transcription_source:
    "typed" | "realtime_live" | "final_model" | "assistant" | "legacy";
  transcription_model: string | null;
  transcription_finalized_at: string | null;
  delivery_status: "pending" | "acknowledged";
  started_at: string;
  ended_at: string | null;
}

export interface InterviewTurnInput {
  client_turn_id: string;
  speaker: "user" | "assistant";
  transcript: string;
  delivery_status: "pending" | "acknowledged";
  started_at?: string;
  ended_at?: string;
}

export interface InterviewRuntime {
  interview_id: string;
  status: string;
  input_mode: InputMode;
  duration_minutes: number;
  started_at: string | null;
  ends_at: string | null;
  server_now: string;
  typed_answer_max_characters: number;
  turns: InterviewTurn[];
}

export type Seniority = "junior" | "mid" | "senior";
export type RequirementClass = "must-have" | "trainable" | "nice-to-have";

export interface SourceReference {
  source_id: string;
  label: string;
  excerpt: string;
}

export interface ResumeUpload {
  id: string;
  original_filename: string;
  file_type: "pdf" | "docx";
  media_type: string;
  size: number;
  sha256: string;
  raw_deleted_at: string;
  created_at: string;
}

export interface ProfileClaim {
  id: string;
  category: "summary" | "skill" | "experience" | "education" | "other";
  text: string;
  source: SourceReference;
  edited: boolean;
  original_text: string | null;
}

export interface CandidateProfile {
  id: string;
  source_resume_id: string;
  headline: string;
  claims: ProfileClaim[];
  extractor_version: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface JobRequirement {
  id: string;
  name: string;
  classification: RequirementClass;
  source: SourceReference;
}

export interface JobTarget {
  id: string;
  title: string;
  seniority: Seniority;
  raw_description: string;
  structured_requirements: JobRequirement[];
  created_at: string;
  updated_at: string;
}

export interface ScorecardCompetency {
  id: string;
  name: string;
  description: string;
  weight: number;
  classification: RequirementClass;
  seniority_expectation: string;
  evidence_to_collect: string[];
  question_families: string[];
  source_references: SourceReference[];
}

export interface Scorecard {
  id: string;
  job_target_id: string;
  version: number;
  competencies: ScorecardCompetency[];
  total_weight: number;
  created_at: string;
  updated_at: string;
}

export interface InterviewSetup {
  upload: ResumeUpload | null;
  profile: CandidateProfile | null;
  job_target: JobTarget | null;
  scorecard: Scorecard | null;
}

export interface InterviewList {
  items: InterviewSession[];
}

export type RatingConfidence = "low" | "medium" | "high";

export interface ReportEvidence {
  turn_id: string;
  sequence: number;
  quote: string;
}

export interface CompetencyResult {
  competency_id: string;
  name: string;
  weight: number;
  classification: RequirementClass;
  assessment: "scored" | "not_assessed";
  score: number | null;
  rating_confidence: RatingConfidence | null;
  evidence: ReportEvidence[];
  evidence_summary: string | null;
  gaps: string[];
  recommendations: string[];
  not_assessed_reason?: string | null;
}

export interface PracticeExercise {
  title: string;
  competency_ids: string[];
  instruction: string;
  success_criteria: string[];
}

export interface InterviewReport {
  interview_id: string;
  status: "REPORT_READY";
  evaluator_version: string;
  prompt_version: string;
  overall_score: number | null;
  assessed_weight: number;
  total_weight: number;
  coverage_percentage: number;
  competency_results: CompetencyResult[];
  strengths: string[];
  gaps: string[];
  practice_exercises: PracticeExercise[];
  uncertainty: string[];
  delivery_coaching?: DeliveryCoaching;
  completed_at: string;
}

export interface EvaluationStatus {
  interview_id: string;
  status: string;
}

export interface ApiErrorPayload {
  error?: {
    id?: string;
    code?: string;
    message?: string;
  };
}
