import { useEffect, useState } from "react";

import { api, ApiError } from "./api";
import { ConfirmationDialog } from "./ConfirmationDialog";
import type { UsageSummary } from "./types";

interface PrivacyPageProps {
  onBack: () => void;
}

export function PrivacyPage({ onBack }: PrivacyPageProps) {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .usageSummary()
      .then((summary) => {
        if (active) setUsage(summary);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setError(
          caught instanceof ApiError
            ? caught
            : new ApiError("Privacy and usage details could not be loaded."),
        );
      });
    return () => {
      active = false;
    };
  }, []);

  async function deleteAccount() {
    setDeleting(true);
    setError(null);
    try {
      await api.deleteAccount();
      setDeleted(true);
      setConfirming(false);
      window.sessionStorage.clear();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The account could not be deleted."),
      );
    } finally {
      setDeleting(false);
    }
  }

  if (deleted) {
    return (
      <main className="canvas privacy-canvas">
        <section className="card privacy-empty" role="status">
          <p className="section__eyebrow">Deletion complete</p>
          <h1>Your account data was deleted</h1>
          <p>
            Sessions, transcripts, reports, delivery metrics, and retained setup
            data are no longer available.
          </p>
        </section>
      </main>
    );
  }

  const sameRetention =
    usage?.transcript_retention_days === usage?.delivery_metrics_retention_days;

  return (
    <main className="canvas privacy-canvas">
      <section className="page-header privacy-header">
        <div>
          <p className="section__eyebrow">Private alpha controls</p>
          <h1 className="section__title">Privacy &amp; usage</h1>
          <p className="section__lede">
            Review limits and retention without exposing interview content in
            operational telemetry.
          </p>
        </div>
        <button className="btn btn--ghost" type="button" onClick={onBack}>
          Back to sessions
        </button>
      </section>

      {error ? (
        <div className="error-state" role="alert">
          <strong>{error.message}</strong>
        </div>
      ) : null}

      <section className="privacy-grid" aria-label="Private alpha usage">
        <article className="card privacy-metric">
          <span>Today’s sessions</span>
          <strong>
            {usage
              ? `${usage.daily_interviews_used} of ${usage.daily_interview_quota}`
              : "—"}
          </strong>
          <p>Quota resets at midnight UTC.</p>
        </article>
        <article className="card privacy-metric">
          <span>Evaluations started</span>
          <strong>{usage?.events.evaluation_started ?? "—"}</strong>
          <p>Last 30 days, content-free count.</p>
        </article>
        <article className="card privacy-metric">
          <span>Estimated AI cost</span>
          <strong>
            {usage?.cost_status === "estimated"
              ? `$${usage.estimated_cost_usd}`
              : "Not measured"}
          </strong>
          <p>
            {usage?.cost_status === "unavailable"
              ? "Provider cost telemetry is not available yet."
              : "Estimate from recorded provider usage."}
          </p>
        </article>
      </section>

      <section
        className="card privacy-policy"
        aria-labelledby="retention-title"
      >
        <div>
          <p className="section__eyebrow">Data lifecycle</p>
          <h2 id="retention-title">Retention and deletion</h2>
          {usage ? (
            sameRetention ? (
              <p>
                Transcripts and optional delivery metrics are retained for{" "}
                {usage.transcript_retention_days} days. Raw résumé files are not
                retained.
              </p>
            ) : (
              <p>
                Transcripts are retained for {usage.transcript_retention_days}{" "}
                days; optional delivery metrics for{" "}
                {usage.delivery_metrics_retention_days} days. Raw résumé files
                are not retained.
              </p>
            )
          ) : (
            <p>Loading the active retention policy…</p>
          )}
          <p>
            Voice audio is held only in browser and API memory while the
            configured Azure provider transcribes it, once live during the
            interview and once for the higher-accuracy final transcript. It is
            never written to disk and is not retained by this application. Only
            the resulting text is stored.
          </p>
        </div>
        <div className="privacy-policy__danger">
          <div>
            <h3>Delete account data</h3>
            <p>
              Permanently removes every session, transcript, report, delivery
              metric, and retained setup record.
            </p>
          </div>
          <button
            className="btn btn--danger"
            type="button"
            onClick={() => setConfirming(true)}
          >
            Delete my account
          </button>
        </div>
      </section>

      {confirming ? (
        <ConfirmationDialog
          title="Delete your account?"
          body="This cannot be undone. All retained interview and setup data will be permanently removed."
          confirmationPhrase="DELETE MY ACCOUNT"
          confirmLabel="Delete account permanently"
          busy={deleting}
          onCancel={() => setConfirming(false)}
          onConfirm={() => void deleteAccount()}
        />
      ) : null}
    </main>
  );
}
