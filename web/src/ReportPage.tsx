import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError } from "./api";
import { ArrowLeftIcon, CheckIcon, ClockIcon, FileIcon } from "./icons";
import type {
  CompetencyResult,
  InterviewReport,
  InterviewSession,
} from "./types";

interface ReportPageProps {
  interview: InterviewSession;
  onBack: () => void;
  onInterviewUpdated: (interview: InterviewSession) => void;
}

const PROCESSING_STATES = new Set(["TRANSCRIPT_FINALIZING", "EVALUATING"]);

function processingCopy(status: string) {
  if (status === "TRANSCRIPT_FINALIZING") {
    return {
      eyebrow: "Preparing evaluation",
      title: "Finalizing your transcript",
      body: "We’re reconciling the last interview turns before evaluation begins.",
    };
  }
  return {
    eyebrow: "Evaluation in progress",
    title: "Reviewing your evidence",
    body: "Your answers are being scored against the frozen role scorecard. This page updates automatically.",
  };
}

function scoreLabel(score: number | null): string {
  if (score === null) return "Not assessed";
  return `${score.toFixed(1)} / 5`;
}

function percentageLabel(value: number): string {
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)}%`;
}

function resultMeta(result: CompetencyResult): string {
  if (result.score === null) {
    return (
      result.not_assessed_reason ??
      "The interview did not collect enough evidence."
    );
  }
  const confidence = result.rating_confidence
    ? `${result.rating_confidence} confidence`
    : "confidence unavailable";
  return `${result.weight}% weight · ${confidence}`;
}

export function ReportPage({
  interview,
  onBack,
  onInterviewUpdated,
}: ReportPageProps) {
  const [report, setReport] = useState<InterviewReport | null>(null);
  const [loading, setLoading] = useState(interview.status === "REPORT_READY");
  const [error, setError] = useState<ApiError | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const [deletingDelivery, setDeletingDelivery] = useState(false);
  const [disablingDelivery, setDisablingDelivery] = useState(false);
  const evaluationRequestedRef = useRef<string | null>(null);
  const onInterviewUpdatedRef = useRef(onInterviewUpdated);
  onInterviewUpdatedRef.current = onInterviewUpdated;

  const loadReport = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReport(await api.report(interview.id));
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The interview report could not be loaded."),
      );
    } finally {
      setLoading(false);
    }
  }, [interview.id]);

  useEffect(() => {
    if (interview.status === "REPORT_READY") void loadReport();
  }, [interview.status, loadReport, reloadKey]);

  // Keyed on id + status, not the whole interview: the 2-second poller below
  // republishes a new interview object on every tick, and depending on the
  // object (or on an unmemoized callback) re-ran this effect — and so re-posted
  // /evaluate — roughly every two seconds until the status changed.
  useEffect(() => {
    if (interview.status !== "TRANSCRIPT_FINALIZING") return;
    if (evaluationRequestedRef.current === interview.id) return;
    evaluationRequestedRef.current = interview.id;
    let active = true;
    api
      .evaluate(interview.id)
      .then(() => {
        if (active)
          onInterviewUpdatedRef.current({ ...interview, status: "EVALUATING" });
      })
      .catch((caught: unknown) => {
        if (!active) return;
        evaluationRequestedRef.current = null;
        setError(
          caught instanceof ApiError
            ? caught
            : new ApiError("The evaluation could not be started."),
        );
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interview.id, interview.status]);

  useEffect(() => {
    if (!PROCESSING_STATES.has(interview.status)) return;

    let active = true;
    let timeout: number | undefined;

    const poll = async () => {
      try {
        const updated = await api.interview(interview.id);
        if (active) onInterviewUpdated(updated);
      } catch {
        // A transient polling failure should not replace the processing state.
      } finally {
        if (active) timeout = window.setTimeout(poll, 2000);
      }
    };

    timeout = window.setTimeout(poll, 2000);

    return () => {
      active = false;
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [interview.id, interview.status, onInterviewUpdated]);

  const assessedCount = useMemo(
    () =>
      report?.competency_results.filter((result) => result.score !== null)
        .length ?? 0,
    [report],
  );

  async function retryEvaluation() {
    setRetrying(true);
    setError(null);
    evaluationRequestedRef.current = interview.id;
    try {
      await api.evaluate(interview.id);
      onInterviewUpdated({ ...interview, status: "EVALUATING" });
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The evaluation could not be restarted."),
      );
    } finally {
      setRetrying(false);
    }
  }

  async function deleteDeliveryMetrics() {
    if (!report) return;
    setDeletingDelivery(true);
    setError(null);
    try {
      const deliveryCoaching = await api.deleteDeliveryMetrics(interview.id);
      setReport({ ...report, delivery_coaching: deliveryCoaching });
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The delivery metrics could not be deleted."),
      );
    } finally {
      setDeletingDelivery(false);
    }
  }

  async function disableDeliveryCoaching() {
    if (!report) return;
    setDisablingDelivery(true);
    setError(null);
    try {
      const deliveryCoaching = await api.updateDeliveryConsent(
        interview.id,
        false,
      );
      setReport({ ...report, delivery_coaching: deliveryCoaching });
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("Delivery coaching could not be disabled."),
      );
    } finally {
      setDisablingDelivery(false);
    }
  }

  if (interview.status === "FAILED_RECOVERABLE") {
    return (
      <main className="canvas report-canvas">
        <button
          className="btn btn--ghost back-button"
          type="button"
          onClick={onBack}
        >
          <ArrowLeftIcon />
          Practice sessions
        </button>
        <section className="card report-processing" role="alert">
          <p className="section__eyebrow">Evaluation paused</p>
          <h1>Your transcript is safe</h1>
          <p>
            We couldn’t finish the evidence report. Retry uses the same frozen
            transcript and scorecard, and never creates a duplicate report.
          </p>
          {error ? <p className="field-error">{error.message}</p> : null}
          <button
            className="btn btn--primary"
            type="button"
            disabled={retrying}
            onClick={() => void retryEvaluation()}
          >
            {retrying ? "Retrying…" : "Retry evaluation"}
          </button>
        </section>
      </main>
    );
  }

  if (PROCESSING_STATES.has(interview.status)) {
    const copy = processingCopy(interview.status);
    return (
      <main className="canvas report-canvas">
        <button
          className="btn btn--ghost back-button"
          type="button"
          onClick={onBack}
        >
          <ArrowLeftIcon />
          Practice sessions
        </button>
        <section
          className="card report-processing"
          role="status"
          aria-live="polite"
        >
          <div className="report-processing__mark" aria-hidden="true">
            <ClockIcon size={24} />
          </div>
          <p className="section__eyebrow">{copy.eyebrow}</p>
          <h1>{copy.title}</h1>
          <p>{copy.body}</p>
          <div
            className="report-processing__steps"
            aria-label="Report progress"
          >
            <span className="is-complete">
              <CheckIcon size={16} /> Interview complete
            </span>
            <span
              className={
                interview.status === "EVALUATING" ? "is-complete" : "is-active"
              }
            >
              <CheckIcon size={16} /> Transcript final
            </span>
            <span
              className={interview.status === "EVALUATING" ? "is-active" : ""}
            >
              <ClockIcon size={16} /> Evidence report
            </span>
          </div>
        </section>
      </main>
    );
  }

  if (
    loading ||
    (interview.status === "REPORT_READY" && report === null && error === null)
  ) {
    return (
      <main className="canvas report-canvas">
        <div
          className="card report-skeleton skeleton"
          aria-label="Loading interview report"
        />
      </main>
    );
  }

  if (error || !report) {
    return (
      <main className="canvas report-canvas">
        <button
          className="btn btn--ghost back-button"
          type="button"
          onClick={onBack}
        >
          <ArrowLeftIcon />
          Practice sessions
        </button>
        <div className="error-state" role="alert">
          <div>
            <strong>
              {error?.message ?? "The interview report is not available yet."}
            </strong>
            {error?.errorId ? <p>Error ID: {error.errorId}</p> : null}
          </div>
          <button
            className="btn btn--sm"
            type="button"
            onClick={() => setReloadKey((key) => key + 1)}
          >
            Retry
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="canvas report-canvas">
      <button
        className="btn btn--ghost back-button"
        type="button"
        onClick={onBack}
      >
        <ArrowLeftIcon />
        Practice sessions
      </button>

      <header className="page-header report-header">
        <div>
          <p className="section__eyebrow">Evidence report</p>
          <h1 className="section__title">{interview.title}</h1>
          <p className="section__lede">
            Feedback is based only on what you demonstrated in this interview.
            It is coaching guidance, not a hiring decision.
          </p>
        </div>
        <span className="report-ready-badge">
          <CheckIcon size={16} /> Report ready
        </span>
      </header>

      <section className="report-summary-grid" aria-label="Evaluation summary">
        <article className="card report-score-card">
          <span>Weighted evidence score</span>
          <strong>{scoreLabel(report.overall_score)}</strong>
          <p>Calculated from assessed scorecard competencies only.</p>
        </article>
        <article className="card report-stat-card">
          <span>Evidence coverage</span>
          <strong>{percentageLabel(report.coverage_percentage)}</strong>
          <p>
            {assessedCount} of {report.competency_results.length} competencies
            assessed
            {` · ${report.assessed_weight} of ${report.total_weight} weight points`}
          </p>
        </article>
        <article className="card report-stat-card">
          <span>Delivery coaching</span>
          <strong>Separate</strong>
          <p>Speaking metrics never change this evidence score.</p>
        </article>
      </section>

      <div className="report-layout">
        <section
          className="report-main"
          aria-labelledby="competency-results-title"
        >
          <div className="section-heading report-section-heading">
            <div>
              <p className="section__eyebrow">Scorecard evidence</p>
              <h2 id="competency-results-title">Competency results</h2>
            </div>
            <span className="section-heading__count">
              {report.competency_results.length} competencies
            </span>
          </div>

          <div className="report-competency-list">
            {report.competency_results.map((result) => (
              <article
                className="card report-competency"
                key={result.competency_id}
              >
                <div className="report-competency__heading">
                  <div>
                    <span className="badge">{result.classification}</span>
                    <h3>{result.name}</h3>
                    <p>{resultMeta(result)}</p>
                  </div>
                  <strong
                    className={result.score === null ? "is-unassessed" : ""}
                  >
                    {scoreLabel(result.score)}
                  </strong>
                </div>

                {result.evidence_summary ? (
                  <p className="report-evidence-summary">
                    {result.evidence_summary}
                  </p>
                ) : null}

                {result.evidence.length > 0 ? (
                  <details className="report-evidence">
                    <summary>
                      View {result.evidence.length} transcript{" "}
                      {result.evidence.length === 1 ? "excerpt" : "excerpts"}
                    </summary>
                    <div>
                      {result.evidence.map((evidence) => (
                        <blockquote
                          key={`${evidence.turn_id}-${evidence.sequence}`}
                        >
                          <p>“{evidence.quote}”</p>
                          <cite>
                            Candidate answer · Turn {evidence.sequence}
                          </cite>
                        </blockquote>
                      ))}
                    </div>
                  </details>
                ) : null}

                {result.gaps.length > 0 || result.recommendations.length > 0 ? (
                  <div className="report-competency__coaching">
                    {result.gaps.length > 0 ? (
                      <div>
                        <h4>Evidence gaps</h4>
                        <ul>
                          {result.gaps.map((gap) => (
                            <li key={gap}>{gap}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {result.recommendations.length > 0 ? (
                      <div>
                        <h4>Next practice</h4>
                        <ul>
                          {result.recommendations.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        </section>

        <aside className="report-sidebar" aria-label="Coaching summary">
          <section className="card report-coaching-card">
            <p className="section__eyebrow">What worked</p>
            <h2>Strengths</h2>
            {report.strengths.length > 0 ? (
              <ul>
                {report.strengths.map((strength) => (
                  <li key={strength}>{strength}</li>
                ))}
              </ul>
            ) : (
              <p className="report-empty-copy">
                No strength was claimed without transcript evidence.
              </p>
            )}
          </section>

          <section className="card report-coaching-card">
            <p className="section__eyebrow">Focus next</p>
            <h2>Growth areas</h2>
            {report.gaps.length > 0 ? (
              <ul>
                {report.gaps.map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            ) : (
              <p className="report-empty-copy">
                No additional cross-competency gaps were identified.
              </p>
            )}
          </section>

          <section className="card report-coaching-card">
            <p className="section__eyebrow">Practice plan</p>
            <h2>Exercises</h2>
            {report.practice_exercises.length > 0 ? (
              <div className="report-exercise-list">
                {report.practice_exercises.map((exercise, index) => (
                  <article key={`${exercise.title}-${index}`}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <h3>{exercise.title}</h3>
                      <p>{exercise.instruction}</p>
                      <small>Success criteria</small>
                      <ul>
                        {exercise.success_criteria.map((criterion) => (
                          <li key={criterion}>{criterion}</li>
                        ))}
                      </ul>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="report-empty-copy">
                Exercises require at least one assessed competency.
              </p>
            )}
          </section>

          {report.uncertainty.length > 0 ? (
            <section className="card report-coaching-card report-uncertainty">
              <p className="section__eyebrow">Limits</p>
              <h2>What remains uncertain</h2>
              <ul>
                {report.uncertainty.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          ) : null}

          <footer className="report-meta">
            <FileIcon size={16} />
            <span>
              Evaluator {report.evaluator_version}
              {` · ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(report.completed_at))}`}
            </span>
          </footer>
        </aside>
      </div>

      {report.delivery_coaching ? (
        <section
          className="card delivery-report"
          aria-labelledby="delivery-report-title"
        >
          <div className="delivery-report__header">
            <div>
              <p className="section__eyebrow">Separate coaching dimension</p>
              <h2 id="delivery-report-title">Speaking delivery</h2>
              <p>
                Observable speaking patterns only. They never change your
                evidence score and do not infer confidence, emotion, stress,
                personality, or honesty.
              </p>
            </div>
            {report.delivery_coaching.status === "available" ||
            report.delivery_coaching.status === "disabled" ? (
              <div className="delivery-report__actions">
                {report.delivery_coaching.status === "available" ? (
                  <button
                    className="btn btn--ghost btn--sm"
                    type="button"
                    disabled={disablingDelivery || deletingDelivery}
                    onClick={() => void disableDeliveryCoaching()}
                  >
                    {disablingDelivery
                      ? "Disabling…"
                      : "Disable delivery coaching"}
                  </button>
                ) : null}
                <button
                  className="btn btn--sm"
                  type="button"
                  disabled={deletingDelivery || disablingDelivery}
                  onClick={() => void deleteDeliveryMetrics()}
                >
                  {deletingDelivery ? "Deleting…" : "Delete delivery metrics"}
                </button>
              </div>
            ) : null}
          </div>

          {report.delivery_coaching.status === "available" ? (
            <>
              {report.delivery_coaching.baseline ? (
                <div
                  className="delivery-baseline"
                  aria-label="Individual speaking baseline"
                >
                  <div>
                    <span>Baseline pace</span>
                    <strong>
                      {report.delivery_coaching.baseline.words_per_minute} wpm
                    </strong>
                  </div>
                  <div>
                    <span>Filler phrases</span>
                    <strong>
                      {
                        report.delivery_coaching.baseline
                          .filler_words_per_100_words
                      }
                      /100 words
                    </strong>
                  </div>
                  <div>
                    <span>Baseline answers</span>
                    <strong>
                      {report.delivery_coaching.baseline.turn_count}
                    </strong>
                  </div>
                </div>
              ) : (
                <p className="report-empty-copy">
                  Two observed voice answers are required to establish your
                  individual baseline.
                </p>
              )}
              <div className="delivery-report__columns">
                <div>
                  <h3>Observations</h3>
                  <ul>
                    {report.delivery_coaching.observations.map(
                      (observation) => (
                        <li
                          key={`${observation.turn_id}-${observation.category}`}
                        >
                          {observation.text}
                        </li>
                      ),
                    )}
                  </ul>
                </div>
                <div>
                  <h3>Practice suggestions</h3>
                  <ul>
                    {report.delivery_coaching.suggestions.map((suggestion) => (
                      <li key={suggestion}>{suggestion}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </>
          ) : (
            <p className="delivery-status-copy" role="status">
              {report.delivery_coaching.status === "deleted"
                ? "Delivery metrics deleted. Your role-fit report is unchanged."
                : report.delivery_coaching.status === "disabled"
                  ? "Delivery coaching is disabled. Existing metrics remain private until you delete them."
                  : report.delivery_coaching.unavailable_reason ===
                      "text_input_mode"
                    ? "Unavailable for developer text input; no delivery score was assigned."
                    : "No speaking-delivery metrics were collected for this session."}
            </p>
          )}
        </section>
      ) : null}
    </main>
  );
}
