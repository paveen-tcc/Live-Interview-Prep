import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "./api";
import { AccountButton } from "./auth";
import { clerkEnabled } from "./authConfig";
import { ConfirmationDialog } from "./ConfirmationDialog";
import {
  ClockIcon,
  FileIcon,
  HomeIcon,
  MoonIcon,
  PlusIcon,
  ShieldIcon,
  SunIcon,
  TrashIcon,
} from "./icons";
import { PrivacyPage } from "./PrivacyPage";
import { SetupPage } from "./SetupPage";
import { PracticePage } from "./PracticePage";
import { ReportPage } from "./ReportPage";
import { statusPill, statusPillClass } from "./status";
import type { InterviewSession, User } from "./types";

type Theme = "light" | "dark";
type WorkspaceView = "sessions" | "privacy";

function initialTheme(): Theme {
  const saved = window.localStorage.getItem("interview-coach-theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [interviews, setInterviews] = useState<InterviewSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [selectedInterview, setSelectedInterview] =
    useState<InterviewSession | null>(null);
  const [practiceMode, setPracticeMode] = useState(false);
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView>("sessions");
  const [pendingDelete, setPendingDelete] = useState<InterviewSession | null>(
    null,
  );
  const [deletingSession, setDeletingSession] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("interview-coach-theme", theme);
  }, [theme]);

  useEffect(() => {
    let active = true;
    Promise.all([api.currentUser(), api.interviews()])
      .then(([currentUser, result]) => {
        if (!active) return;
        setUser(currentUser);
        setInterviews(result.items);
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(
            caught instanceof ApiError
              ? caught
              : new ApiError("The workspace could not be loaded."),
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const draftCount = useMemo(
    () => interviews.filter((interview) => interview.status === "DRAFT").length,
    [interviews],
  );
  const reportCount = useMemo(
    () =>
      interviews.filter((interview) => interview.status === "REPORT_READY")
        .length,
    [interviews],
  );

  async function createSession() {
    setCreating(true);
    setError(null);
    try {
      const interview = await api.createInterview();
      setInterviews((current) => [interview, ...current]);
      setSelectedInterview(interview);
      setPracticeMode(false);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The practice session could not be created."),
      );
    } finally {
      setCreating(false);
    }
  }

  // Stable identity: child effects depend on this callback, and a new function
  // every render re-ran them on every poll tick.
  const updateInterview = useCallback((updated: InterviewSession) => {
    setInterviews((current) =>
      current.map((interview) =>
        interview.id === updated.id ? updated : interview,
      ),
    );
    setSelectedInterview(updated);
  }, []);

  async function deleteSession() {
    if (!pendingDelete) return;
    setDeletingSession(true);
    setError(null);
    try {
      await api.deleteInterview(pendingDelete.id);
      setInterviews((current) =>
        current.filter((interview) => interview.id !== pendingDelete.id),
      );
      if (selectedInterview?.id === pendingDelete.id) {
        setSelectedInterview(null);
        setPracticeMode(false);
      }
      setPendingDelete(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The practice session could not be deleted."),
      );
    } finally {
      setDeletingSession(false);
    }
  }

  if (!loading && error?.status === 401) {
    return (
      <main className="auth-page">
        <section className="card auth-card" aria-labelledby="sign-in-title">
          <div className="topbar__brand auth-card__brand">
            <div className="topbar__brand-mark" aria-hidden="true">
              IC
            </div>
            <span>AI Interview Coach</span>
          </div>
          <p className="section__eyebrow">Private workspace</p>
          <h1 id="sign-in-title">Your session has expired</h1>
          <p className="section__lede">
            Reload the page to sign in again and pick up where you left off.
          </p>
          <button
            className="btn btn--primary"
            type="button"
            onClick={() => window.location.reload()}
          >
            Reload
          </button>
        </section>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar__brand">
          <div className="topbar__brand-mark" aria-hidden="true">
            IC
          </div>
          <span>AI Interview Coach</span>
        </div>
        <div className="topbar__spacer" />
        <div className="topbar__status" aria-label="Application status">
          <span className="dot dot--online" />
          Private workspace
        </div>
        <button
          className="icon-btn"
          type="button"
          aria-label={`Switch to ${theme === "light" ? "dark" : "light"} theme`}
          onClick={() => setTheme(theme === "light" ? "dark" : "light")}
        >
          {theme === "light" ? <MoonIcon /> : <SunIcon />}
        </button>
        {clerkEnabled ? (
          <AccountButton />
        ) : (
          <div
            className="avatar"
            title={user?.email}
            aria-label="Signed-in user"
          >
            {user?.display_name.slice(0, 1).toUpperCase() ?? "…"}
          </div>
        )}
      </header>

      <aside className="sidebar" aria-label="Primary navigation">
        <div className="sidebar__group">
          <div className="sidebar__label">Workspace</div>
          <button
            className={`sidebar__link ${workspaceView === "sessions" && !selectedInterview ? "is-active" : ""}`}
            type="button"
            onClick={() => {
              setSelectedInterview(null);
              setPracticeMode(false);
              setWorkspaceView("sessions");
            }}
          >
            <HomeIcon />
            Practice sessions
          </button>
          <button
            className={`sidebar__link ${workspaceView === "privacy" ? "is-active" : ""}`}
            type="button"
            onClick={() => {
              setSelectedInterview(null);
              setPracticeMode(false);
              setWorkspaceView("privacy");
            }}
          >
            <ShieldIcon />
            Privacy &amp; usage
          </button>
        </div>
        <div className="sidebar__footer">
          <p>Phase M6</p>
          <span>Private alpha hardening</span>
        </div>
      </aside>

      {workspaceView === "privacy" ? (
        <PrivacyPage onBack={() => setWorkspaceView("sessions")} />
      ) : selectedInterview &&
        (["TRANSCRIPT_FINALIZING", "EVALUATING", "REPORT_READY"].includes(
          selectedInterview.status,
        ) ||
          (selectedInterview.status === "FAILED_RECOVERABLE" &&
            selectedInterview.ended_at)) ? (
        <ReportPage
          interview={selectedInterview}
          onBack={() => {
            setSelectedInterview(null);
            setPracticeMode(false);
          }}
          onInterviewUpdated={updateInterview}
        />
      ) : selectedInterview && practiceMode ? (
        <PracticePage
          interview={selectedInterview}
          onBack={() => setPracticeMode(false)}
          onInterviewUpdated={updateInterview}
        />
      ) : selectedInterview ? (
        <SetupPage
          interview={selectedInterview}
          onBack={() => {
            setSelectedInterview(null);
            setPracticeMode(false);
          }}
          onInterviewUpdated={updateInterview}
          onBeginInterview={() => setPracticeMode(true)}
        />
      ) : (
        <main className="canvas" id="dashboard">
          <section className="page-header">
            <div>
              <p className="section__eyebrow">Practice workspace</p>
              <h1 className="section__title">
                {user
                  ? `Welcome back, ${user.display_name}`
                  : "Your interview practice"}
              </h1>
              <p className="section__lede">
                Create a source-backed candidate profile and scorecard for your
                target role.
              </p>
            </div>
            <button
              className="btn btn--primary"
              type="button"
              disabled={creating || loading}
              onClick={createSession}
            >
              <PlusIcon />
              {creating ? "Creating…" : "New practice session"}
            </button>
          </section>

          {error ? (
            <div className="error-state" role="alert">
              <div>
                <strong>{error.message}</strong>
                {error.errorId ? <p>Error ID: {error.errorId}</p> : null}
              </div>
              <button
                className="btn btn--sm"
                onClick={() => window.location.reload()}
              >
                Retry
              </button>
            </div>
          ) : null}

          <section className="stat-grid" aria-label="Workspace summary">
            <article className="card stat-card">
              <span className="stat-card__label">Total sessions</span>
              <strong className="stat-card__value">
                {loading ? "—" : interviews.length}
              </strong>
              <span className="stat-card__meta">Saved to your workspace</span>
            </article>
            <article className="card stat-card">
              <span className="stat-card__label">Drafts</span>
              <strong className="stat-card__value">
                {loading ? "—" : draftCount}
              </strong>
              <span className="stat-card__meta">Still being prepared</span>
            </article>
            <article className="card stat-card">
              <span className="stat-card__label">Reports</span>
              <strong className="stat-card__value">
                {loading ? "—" : reportCount}
              </strong>
              <span className="stat-card__meta">Evidence-backed coaching</span>
            </article>
          </section>

          <section
            className="sessions-section"
            aria-labelledby="sessions-title"
          >
            <div className="section-heading">
              <div>
                <p className="section__eyebrow">Recent activity</p>
                <h2 id="sessions-title">Practice sessions</h2>
              </div>
              <span className="section-heading__count">
                {interviews.length}{" "}
                {interviews.length === 1 ? "session" : "sessions"}
              </span>
            </div>

            {loading ? (
              <div className="session-list" aria-label="Loading sessions">
                <div className="card session-card skeleton" />
                <div className="card session-card skeleton" />
              </div>
            ) : interviews.length === 0 ? (
              <div className="card empty-state">
                <div className="empty-state__icon">
                  <FileIcon />
                </div>
                <h3>No practice sessions yet</h3>
                <p>
                  Create an empty session to verify that your private workspace
                  persists across refreshes.
                </p>
                <button className="btn" type="button" onClick={createSession}>
                  <PlusIcon />
                  Create your first session
                </button>
              </div>
            ) : (
              <div className="session-list">
                {interviews.map((interview) => (
                  <article className="card session-card" key={interview.id}>
                    <div className="session-card__icon">
                      <FileIcon />
                    </div>
                    <div className="session-card__content">
                      <div className="session-card__title-row">
                        <h3>{interview.title}</h3>
                        <span className={statusPillClass(interview.status)}>
                          {statusPill(interview.status).label}
                        </span>
                      </div>
                      <p className="session-card__meta">
                        <ClockIcon size={16} />
                        Created {formatDate(interview.created_at)}
                      </p>
                    </div>
                    <div className="session-card__actions">
                      <button
                        className="icon-btn session-card__delete"
                        type="button"
                        aria-label={`Delete ${interview.title}`}
                        title="Delete session"
                        onClick={() => setPendingDelete(interview)}
                      >
                        <TrashIcon />
                      </button>
                      <button
                        className="btn btn--sm"
                        type="button"
                        onClick={() => {
                          setSelectedInterview(interview);
                          setPracticeMode(
                            [
                              "CONNECTING",
                              "IN_PROGRESS",
                              "RECONNECTING",
                              "FAILED_RECOVERABLE",
                            ].includes(interview.status),
                          );
                        }}
                      >
                        {[
                          "CONNECTING",
                          "IN_PROGRESS",
                          "RECONNECTING",
                          "FAILED_RECOVERABLE",
                        ].includes(interview.status)
                          ? "Resume interview"
                          : interview.status === "TRANSCRIPT_FINALIZING"
                            ? "View progress"
                            : interview.status === "EVALUATING"
                              ? "View progress"
                              : interview.status === "REPORT_READY"
                                ? "View report"
                                : interview.status === "SCORECARD_READY"
                                  ? "Review setup"
                                  : "Continue setup"}
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        </main>
      )}

      {pendingDelete ? (
        <ConfirmationDialog
          title="Delete practice session?"
          body="This permanently removes its setup, transcript, report, and delivery metrics. This action cannot be undone."
          confirmLabel="Delete permanently"
          busy={deletingSession}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => void deleteSession()}
        />
      ) : null}
    </div>
  );
}
