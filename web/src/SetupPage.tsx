import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "./api";
import {
  ArrowLeftIcon,
  CheckIcon,
  ChevronDownIcon,
  FileIcon,
  UploadIcon,
} from "./icons";
import { statusPill, statusPillClass } from "./status";
import type {
  CandidateProfile,
  InterviewSession,
  InterviewSetup,
  ProfileClaim,
  RequirementClass,
  Scorecard,
  Seniority,
} from "./types";

interface SetupPageProps {
  interview: InterviewSession;
  onBack: () => void;
  onInterviewUpdated: (interview: InterviewSession) => void;
  onBeginInterview: () => void;
}

type SetupTab = "profile" | "role" | "scorecard";
type ClaimCategory = ProfileClaim["category"];

const emptySetup: InterviewSetup = {
  upload: null,
  profile: null,
  job_target: null,
  scorecard: null,
};

const claimCategories: Array<{
  value: ClaimCategory;
  label: string;
}> = [
  { value: "summary", label: "Summary" },
  { value: "experience", label: "Experience" },
  { value: "skill", label: "Skills" },
  { value: "education", label: "Education" },
  { value: "other", label: "Other" },
];
const claimsPerPage = 5;

function readableBytes(value: number): string {
  return value < 1_000_000
    ? `${Math.ceil(value / 1_000)} KB`
    : `${(value / 1_000_000).toFixed(1)} MB`;
}

function ErrorNotice({ error }: { error: ApiError | null }) {
  if (!error) return null;
  return (
    <div className="error-state" role="alert">
      <div>
        <strong>{error.message}</strong>
        {error.errorId ? <p>Error ID: {error.errorId}</p> : null}
      </div>
    </div>
  );
}

export function SetupPage({
  interview,
  onBack,
  onInterviewUpdated,
  onBeginInterview,
}: SetupPageProps) {
  const [setup, setSetup] = useState<InterviewSetup>(emptySetup);
  const [profileDraft, setProfileDraft] = useState<CandidateProfile | null>(
    null,
  );
  const [scorecardDraft, setScorecardDraft] = useState<Scorecard | null>(null);
  const [resume, setResume] = useState<File | null>(null);
  const [roleTitle, setRoleTitle] = useState("");
  const [seniority, setSeniority] = useState<Seniority>("mid");
  const [jobDescription, setJobDescription] = useState("");
  const [activeTab, setActiveTab] = useState<SetupTab>("profile");
  const [claimCategory, setClaimCategory] = useState<ClaimCategory>("summary");
  const [claimPage, setClaimPage] = useState(0);
  const [expandedCompetency, setExpandedCompetency] = useState<string | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<
    "resume" | "improve" | "scorecard" | "profile" | "save" | "start" | null
  >(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .setup(interview.id)
      .then((result) => {
        if (!active) return;
        setSetup(result);
        setProfileDraft(result.profile);
        setScorecardDraft(result.scorecard);
        if (result.job_target) {
          setRoleTitle(result.job_target.title);
          setSeniority(result.job_target.seniority);
          setJobDescription(result.job_target.raw_description);
        }
        if (result.scorecard) setActiveTab("scorecard");
        else if (result.profile) setActiveTab("role");
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(
            caught instanceof ApiError
              ? caught
              : new ApiError("The session setup could not be loaded."),
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [interview.id]);

  const totalWeight = useMemo(
    () =>
      scorecardDraft?.competencies.reduce(
        (total, competency) => total + competency.weight,
        0,
      ) ?? 0,
    [scorecardDraft],
  );
  const scorecardComplete = useMemo(
    () =>
      scorecardDraft?.competencies.every(
        (competency) =>
          competency.name.trim() &&
          competency.description.trim() &&
          competency.seniority_expectation.trim() &&
          competency.evidence_to_collect.length > 0 &&
          competency.question_families.length > 0,
      ) ?? false,
    [scorecardDraft],
  );
  const availableClaimCategories = useMemo(
    () =>
      claimCategories
        .map((category) => ({
          ...category,
          count:
            profileDraft?.claims.filter(
              (claim) => claim.category === category.value,
            ).length ?? 0,
        }))
        .filter((category) => category.count > 0),
    [profileDraft],
  );
  const visibleClaimCategory = availableClaimCategories.some(
    (category) => category.value === claimCategory,
  )
    ? claimCategory
    : (availableClaimCategories[0]?.value ?? "summary");
  const categoryClaims =
    profileDraft?.claims.filter(
      (claim) => claim.category === visibleClaimCategory,
    ) ?? [];
  const claimPageCount = Math.max(
    1,
    Math.ceil(categoryClaims.length / claimsPerPage),
  );
  const visibleClaimPage = Math.min(claimPage, claimPageCount - 1);
  const visibleClaims = categoryClaims.slice(
    visibleClaimPage * claimsPerPage,
    (visibleClaimPage + 1) * claimsPerPage,
  );
  const editedClaimCount =
    profileDraft?.claims.filter((claim) => claim.edited).length ?? 0;

  async function extractResume() {
    if (!resume) {
      setError(new ApiError("Choose a PDF or DOCX resume first."));
      return;
    }
    setWorking("resume");
    setError(null);
    setNotice(null);
    try {
      const upload = await api.uploadResume(interview.id, resume);
      const profile = await api.extractProfile(interview.id, upload.id);
      setSetup((current) => ({ ...current, upload, profile }));
      setProfileDraft(profile);
      setClaimCategory(profile.claims[0]?.category ?? "summary");
      setClaimPage(0);
      setActiveTab("profile");
      setNotice("Resume extracted. Review each claim against its source.");
      onInterviewUpdated(await api.interview(interview.id));
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The resume could not be processed."),
      );
    } finally {
      setWorking(null);
    }
  }

  async function buildScorecard() {
    if (!profileDraft) {
      setError(
        new ApiError(
          "Extract and review a resume before creating the scorecard.",
        ),
      );
      return;
    }
    setWorking("scorecard");
    setError(null);
    setNotice(null);
    try {
      const target = await api.createJobTarget(
        interview.id,
        roleTitle,
        seniority,
        jobDescription,
      );
      const scorecard = await api.generateScorecard(interview.id, target.id);
      setSetup((current) => ({ ...current, job_target: target, scorecard }));
      setScorecardDraft(scorecard);
      setExpandedCompetency(null);
      setActiveTab("scorecard");
      setNotice("Draft scorecard created. Open a competency to review it.");
      onInterviewUpdated(await api.interview(interview.id));
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The scorecard could not be generated."),
      );
    } finally {
      setWorking(null);
    }
  }

  async function saveProfile() {
    if (!profileDraft) return;
    setWorking("profile");
    setError(null);
    try {
      const profile = await api.updateProfile(profileDraft);
      setSetup((current) => ({ ...current, profile }));
      setProfileDraft(profile);
      setNotice("Candidate profile saved.");
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The candidate profile could not be saved."),
      );
    } finally {
      setWorking(null);
    }
  }

  async function improveProfile() {
    if (!setup.upload || !profileDraft) return;
    setWorking("improve");
    setError(null);
    setNotice(null);
    try {
      const profile = await api.extractProfile(
        interview.id,
        setup.upload.id,
        true,
      );
      setSetup((current) => ({ ...current, profile }));
      setProfileDraft(profile);
      setClaimCategory(profile.claims[0]?.category ?? "summary");
      setClaimPage(0);
      setNotice(
        "AI extraction complete. Review the condensed evidence against its sources.",
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The profile could not be improved with AI."),
      );
    } finally {
      setWorking(null);
    }
  }

  async function saveScorecard() {
    if (!scorecardDraft || totalWeight !== 100 || !scorecardComplete) return;
    setWorking("save");
    setError(null);
    try {
      const scorecard = await api.updateScorecard(
        scorecardDraft.id,
        scorecardDraft.competencies,
      );
      setSetup((current) => ({ ...current, scorecard }));
      setScorecardDraft(scorecard);
      setNotice("Scorecard saved with a total weight of 100%.");
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The scorecard could not be saved."),
      );
    } finally {
      setWorking(null);
    }
  }

  async function continueToPreflight() {
    if (!scorecardDraft || totalWeight !== 100 || !scorecardComplete) return;
    setWorking("start");
    setError(null);
    try {
      const scorecard = await api.updateScorecard(
        scorecardDraft.id,
        scorecardDraft.competencies,
      );
      setSetup((current) => ({ ...current, scorecard }));
      setScorecardDraft(scorecard);
      onInterviewUpdated(await api.interview(interview.id));
      onBeginInterview();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The interview preflight could not be opened."),
      );
    } finally {
      setWorking(null);
    }
  }

  function updateClaim(claimId: string, text: string) {
    setProfileDraft((current) =>
      current
        ? {
            ...current,
            claims: current.claims.map((claim) =>
              claim.id === claimId ? { ...claim, text } : claim,
            ),
          }
        : current,
    );
  }

  function updateCompetency(
    competencyId: string,
    field:
      | "name"
      | "description"
      | "weight"
      | "classification"
      | "seniority_expectation",
    value: string | number | RequirementClass,
  ) {
    setScorecardDraft((current) =>
      current
        ? {
            ...current,
            competencies: current.competencies.map((competency) =>
              competency.id === competencyId
                ? { ...competency, [field]: value }
                : competency,
            ),
          }
        : current,
    );
  }

  function updateCompetencyList(
    competencyId: string,
    field: "evidence_to_collect" | "question_families",
    value: string,
  ) {
    const items = value
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
    setScorecardDraft((current) =>
      current
        ? {
            ...current,
            competencies: current.competencies.map((competency) =>
              competency.id === competencyId
                ? { ...competency, [field]: items }
                : competency,
            ),
          }
        : current,
    );
  }

  const tabs: Array<{
    id: SetupTab;
    index: string;
    label: string;
    meta: string;
    disabled: boolean;
    complete: boolean;
  }> = [
    {
      id: "profile",
      index: "01",
      label: "Profile",
      meta: profileDraft
        ? `${profileDraft.claims.length} evidence items`
        : "Add résumé",
      disabled: false,
      complete: profileDraft !== null,
    },
    {
      id: "role",
      index: "02",
      label: "Target role",
      meta: setup.job_target?.title ?? "Add job",
      disabled: !profileDraft,
      complete: setup.job_target !== null,
    },
    {
      id: "scorecard",
      index: "03",
      label: "Scorecard",
      meta: scorecardDraft
        ? `${scorecardDraft.competencies.length} competencies`
        : "Generate",
      disabled: !scorecardDraft,
      complete: scorecardDraft !== null,
    },
  ];

  return (
    <main className="canvas setup-canvas" id="session-setup">
      <button
        className="btn btn--ghost back-button"
        type="button"
        onClick={onBack}
      >
        <ArrowLeftIcon />
        Practice sessions
      </button>

      <section className="page-header setup-header">
        <div>
          <p className="section__eyebrow">Practice setup</p>
          <h1 className="section__title">{interview.title}</h1>
          <p className="section__lede">
            Review the evidence, define the role, then tune the interview focus.
          </p>
        </div>
        <span className={statusPillClass(interview.status)}>
          {statusPill(interview.status).label}
        </span>
      </section>

      {loading ? (
        <div
          className="card setup-loading skeleton"
          aria-label="Loading session setup"
        />
      ) : null}
      <ErrorNotice error={error} />
      {notice ? (
        <div className="success-state" role="status">
          <CheckIcon />
          {notice}
        </div>
      ) : null}

      {!loading ? (
        <div className="setup-workspace">
          <div className="setup-tabs" role="tablist" aria-label="Session setup">
            {tabs.map((tab) => (
              <button
                className={`setup-tab ${activeTab === tab.id ? "is-active" : ""}`}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.id}
                aria-controls={`${tab.id}-panel`}
                disabled={tab.disabled}
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
              >
                <span className="setup-tab__index">{tab.index}</span>
                <span className="setup-tab__copy">
                  <strong>{tab.label}</strong>
                  <small>{tab.meta}</small>
                </span>
                {tab.complete ? <CheckIcon size={16} /> : null}
              </button>
            ))}
          </div>

          {activeTab === "profile" ? (
            <section
              className="card setup-panel"
              id="profile-panel"
              role="tabpanel"
              aria-labelledby="profile-title"
            >
              <header className="panel-heading">
                <div>
                  <p className="section__eyebrow">Candidate evidence</p>
                  <h2 id="profile-title">Résumé profile</h2>
                  <p>
                    Only source-backed evidence is used during the interview.
                  </p>
                </div>
                {profileDraft ? (
                  <div className="panel-heading__actions">
                    {profileDraft.extractor_version === "local-rules-v1" &&
                    setup.upload ? (
                      <button
                        className="btn btn--sm"
                        type="button"
                        disabled={working !== null}
                        onClick={improveProfile}
                      >
                        {working === "improve"
                          ? "Improving…"
                          : "Improve with AI"}
                      </button>
                    ) : null}
                    <button
                      className="btn btn--primary btn--sm"
                      type="button"
                      disabled={working !== null}
                      onClick={saveProfile}
                    >
                      {working === "profile" ? "Saving…" : "Save profile"}
                    </button>
                  </div>
                ) : null}
              </header>

              {setup.upload ? (
                <div className="profile-summary">
                  <div className="uploaded-file uploaded-file--compact">
                    <FileIcon />
                    <div>
                      <strong>{setup.upload.original_filename}</strong>
                      <span>
                        {setup.upload.file_type.toUpperCase()} ·{" "}
                        {readableBytes(setup.upload.size)} · source deleted
                      </span>
                    </div>
                  </div>
                  {profileDraft ? (
                    <div
                      className="profile-metrics"
                      aria-label="Profile summary"
                    >
                      <span>
                        <strong>{profileDraft.claims.length}</strong> evidence
                        items
                      </span>
                      <span>
                        <strong>{editedClaimCount}</strong> edited
                      </span>
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="upload-row">
                  <label className="upload-field">
                    <UploadIcon />
                    <span>{resume ? resume.name : "Choose résumé"}</span>
                    <small>PDF or DOCX · up to 5 MB</small>
                    <input
                      type="file"
                      accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                      onChange={(event) =>
                        setResume(event.target.files?.[0] ?? null)
                      }
                    />
                  </label>
                  <button
                    className="btn btn--primary"
                    type="button"
                    disabled={!resume || working !== null}
                    onClick={extractResume}
                  >
                    {working === "resume" ? "Extracting…" : "Extract profile"}
                  </button>
                </div>
              )}

              {profileDraft ? (
                <div className="profile-editor">
                  <label className="field profile-headline">
                    <span className="field__label">Profile headline</span>
                    <input
                      className="input"
                      value={profileDraft.headline}
                      onChange={(event) =>
                        setProfileDraft({
                          ...profileDraft,
                          headline: event.target.value,
                        })
                      }
                    />
                  </label>

                  <div className="claim-workspace">
                    <nav
                      className="claim-categories"
                      aria-label="Claim categories"
                    >
                      {availableClaimCategories.map((category) => (
                        <button
                          className={`claim-category ${visibleClaimCategory === category.value ? "is-active" : ""}`}
                          type="button"
                          key={category.value}
                          aria-pressed={visibleClaimCategory === category.value}
                          onClick={() => {
                            setClaimCategory(category.value);
                            setClaimPage(0);
                          }}
                        >
                          <span>{category.label}</span>
                          <strong>{category.count}</strong>
                        </button>
                      ))}
                    </nav>

                    <div className="claim-editor-list">
                      <div className="claim-editor-list__heading">
                        <div>
                          <h3>
                            {claimCategories.find(
                              (category) =>
                                category.value === visibleClaimCategory,
                            )?.label ?? "Claims"}
                          </h3>
                          <p>Edit a claim or open its original source.</p>
                        </div>
                        <span>
                          {categoryClaims.length === 0
                            ? "0 items"
                            : `${visibleClaimPage * claimsPerPage + 1}–${Math.min(
                                (visibleClaimPage + 1) * claimsPerPage,
                                categoryClaims.length,
                              )} of ${categoryClaims.length}`}
                        </span>
                      </div>
                      {visibleClaims.map((claim, index) => (
                        <article className="claim-row" key={claim.id}>
                          <div className="claim-row__topline">
                            <span>
                              {String(
                                visibleClaimPage * claimsPerPage + index + 1,
                              ).padStart(2, "0")}
                            </span>
                            {claim.edited ? (
                              <strong className="edited-label">Edited</strong>
                            ) : null}
                          </div>
                          <textarea
                            className="textarea claim-row__input"
                            rows={2}
                            aria-label={`${claim.category} claim ${
                              visibleClaimPage * claimsPerPage + index + 1
                            }`}
                            value={claim.text}
                            onChange={(event) =>
                              updateClaim(claim.id, event.target.value)
                            }
                          />
                          <details className="source-reference">
                            <summary>{claim.source.label}</summary>
                            <p>{claim.source.excerpt}</p>
                          </details>
                        </article>
                      ))}
                      {claimPageCount > 1 ? (
                        <div
                          className="claim-pagination"
                          aria-label="Claim pages"
                        >
                          <span>
                            Page {visibleClaimPage + 1} of {claimPageCount}
                          </span>
                          <div>
                            <button
                              className="btn btn--sm"
                              type="button"
                              disabled={visibleClaimPage === 0}
                              onClick={() =>
                                setClaimPage((current) =>
                                  Math.max(0, current - 1),
                                )
                              }
                            >
                              Previous
                            </button>
                            <button
                              className="btn btn--sm"
                              type="button"
                              disabled={visibleClaimPage === claimPageCount - 1}
                              onClick={() =>
                                setClaimPage((current) =>
                                  Math.min(claimPageCount - 1, current + 1),
                                )
                              }
                            >
                              Next
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="panel-empty">
                  <strong>Your profile will appear here</strong>
                  <p>
                    Uploading extracts editable claims while preserving a link
                    to the original résumé text.
                  </p>
                </div>
              )}
            </section>
          ) : null}

          {activeTab === "role" ? (
            <section
              className="card setup-panel"
              id="role-panel"
              role="tabpanel"
              aria-labelledby="target-title"
            >
              <header className="panel-heading">
                <div>
                  <p className="section__eyebrow">Interview target</p>
                  <h2 id="target-title">Target role</h2>
                  <p>
                    Paste the role once. We turn it into an editable scorecard.
                  </p>
                </div>
                {scorecardDraft ? (
                  <span className="panel-status">
                    <CheckIcon size={16} /> Scorecard generated
                  </span>
                ) : null}
              </header>

              <div className="role-form">
                <div className="form-grid">
                  <label className="field field--wide">
                    <span className="field__label">Role title</span>
                    <input
                      className="input"
                      disabled={scorecardDraft !== null}
                      value={roleTitle}
                      onChange={(event) => setRoleTitle(event.target.value)}
                      placeholder="Senior Backend Engineer"
                    />
                  </label>
                  <label className="field">
                    <span className="field__label">Seniority</span>
                    <select
                      className="select"
                      disabled={scorecardDraft !== null}
                      value={seniority}
                      onChange={(event) =>
                        setSeniority(event.target.value as Seniority)
                      }
                    >
                      <option value="junior">Junior</option>
                      <option value="mid">Mid-level</option>
                      <option value="senior">Senior</option>
                    </select>
                  </label>
                </div>
                <label className="field">
                  <span className="field__label">Job description</span>
                  <textarea
                    className="textarea textarea--jd"
                    disabled={scorecardDraft !== null}
                    value={jobDescription}
                    onChange={(event) => setJobDescription(event.target.value)}
                    placeholder="Paste responsibilities, required skills, and preferred experience…"
                  />
                  <span className="field__hint">
                    {scorecardDraft
                      ? "This saved job description is the source for the scorecard."
                      : "Minimum 50 characters. Document instructions are treated as untrusted text."}
                  </span>
                </label>
              </div>

              <footer className="panel-actions">
                {!scorecardDraft ? (
                  <button
                    className="btn btn--primary"
                    type="button"
                    disabled={
                      !profileDraft ||
                      roleTitle.trim().length < 2 ||
                      jobDescription.trim().length < 50 ||
                      working !== null
                    }
                    onClick={buildScorecard}
                  >
                    {working === "scorecard"
                      ? "Building scorecard…"
                      : "Generate scorecard"}
                  </button>
                ) : (
                  <button
                    className="btn btn--primary"
                    type="button"
                    onClick={() => setActiveTab("scorecard")}
                  >
                    Review scorecard
                  </button>
                )}
                <span>
                  Built from the role requirements and selected seniority.
                </span>
              </footer>
            </section>
          ) : null}

          {activeTab === "scorecard" && scorecardDraft ? (
            <section
              className="card setup-panel scorecard-panel"
              id="scorecard-panel"
              role="tabpanel"
              aria-labelledby="scorecard-title"
            >
              <header className="panel-heading">
                <div>
                  <p className="section__eyebrow">Interview focus</p>
                  <h2 id="scorecard-title">Role scorecard</h2>
                  <p>Open only the competency you want to tune.</p>
                </div>
                <div
                  className={`weight-total ${totalWeight === 100 ? "is-valid" : "is-invalid"}`}
                >
                  <strong>{totalWeight}%</strong>
                  <span>Total weight</span>
                </div>
              </header>

              <div className="competency-list">
                {scorecardDraft.competencies.map((competency, index) => {
                  const isOpen = expandedCompetency === competency.id;
                  return (
                    <article
                      className={`competency-row ${isOpen ? "is-open" : ""}`}
                      key={competency.id}
                    >
                      <button
                        className="competency-summary"
                        type="button"
                        aria-expanded={isOpen}
                        aria-controls={`competency-${competency.id}`}
                        onClick={() =>
                          setExpandedCompetency(isOpen ? null : competency.id)
                        }
                      >
                        <span className="competency-index">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <span className="competency-summary__copy">
                          <strong>{competency.name}</strong>
                          <small>{competency.classification}</small>
                        </span>
                        <span className="competency-summary__weight">
                          {competency.weight}%
                        </span>
                        <ChevronDownIcon />
                      </button>

                      {isOpen ? (
                        <div
                          className="competency-editor"
                          id={`competency-${competency.id}`}
                        >
                          <div className="competency-editor__topline">
                            <label className="field competency-name-field">
                              <span className="field__label">Competency</span>
                              <input
                                className="input competency-name"
                                aria-label={`Competency ${index + 1} name`}
                                value={competency.name}
                                onChange={(event) =>
                                  updateCompetency(
                                    competency.id,
                                    "name",
                                    event.target.value,
                                  )
                                }
                              />
                            </label>
                            <label className="field">
                              <span className="field__label">
                                Classification
                              </span>
                              <select
                                className="select"
                                value={competency.classification}
                                onChange={(event) =>
                                  updateCompetency(
                                    competency.id,
                                    "classification",
                                    event.target.value as RequirementClass,
                                  )
                                }
                              >
                                <option value="must-have">Must-have</option>
                                <option value="trainable">Trainable</option>
                                <option value="nice-to-have">
                                  Nice-to-have
                                </option>
                              </select>
                            </label>
                            <label className="field competency-weight-field">
                              <span className="field__label">Weight</span>
                              <span className="weight-control">
                                <input
                                  className="input"
                                  type="number"
                                  min="1"
                                  max="100"
                                  value={competency.weight}
                                  onChange={(event) =>
                                    updateCompetency(
                                      competency.id,
                                      "weight",
                                      Number(event.target.value),
                                    )
                                  }
                                />
                                <b>%</b>
                              </span>
                            </label>
                          </div>

                          <label className="field">
                            <span className="field__label">Description</span>
                            <textarea
                              className="textarea textarea--small"
                              value={competency.description}
                              onChange={(event) =>
                                updateCompetency(
                                  competency.id,
                                  "description",
                                  event.target.value,
                                )
                              }
                            />
                          </label>
                          <label className="field">
                            <span className="field__label">
                              {seniority[0].toUpperCase() + seniority.slice(1)}{" "}
                              expectation
                            </span>
                            <textarea
                              className="textarea textarea--small"
                              value={competency.seniority_expectation}
                              onChange={(event) =>
                                updateCompetency(
                                  competency.id,
                                  "seniority_expectation",
                                  event.target.value,
                                )
                              }
                            />
                          </label>
                          <div className="evidence-grid">
                            <label className="field">
                              <span className="field__label">
                                Evidence to collect · one per line
                              </span>
                              <textarea
                                className="textarea textarea--small"
                                value={competency.evidence_to_collect.join(
                                  "\n",
                                )}
                                onChange={(event) =>
                                  updateCompetencyList(
                                    competency.id,
                                    "evidence_to_collect",
                                    event.target.value,
                                  )
                                }
                              />
                            </label>
                            <label className="field">
                              <span className="field__label">
                                Question families · one per line
                              </span>
                              <textarea
                                className="textarea textarea--small"
                                value={competency.question_families.join("\n")}
                                onChange={(event) =>
                                  updateCompetencyList(
                                    competency.id,
                                    "question_families",
                                    event.target.value,
                                  )
                                }
                              />
                            </label>
                          </div>
                          <details className="source-reference">
                            <summary>
                              {competency.source_references[0]?.label ??
                                "Job description source"}
                            </summary>
                            <p>{competency.source_references[0]?.excerpt}</p>
                          </details>
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </div>

              {totalWeight !== 100 ? (
                <p className="weight-error" role="alert">
                  Adjust weights by {Math.abs(100 - totalWeight)} percentage
                  points to reach 100%.
                </p>
              ) : null}
              {!scorecardComplete ? (
                <p className="weight-error" role="alert">
                  Every competency needs a name, description, seniority
                  expectation, evidence item, and question family.
                </p>
              ) : null}
              <footer className="panel-actions">
                <div className="panel-actions__buttons">
                  <button
                    className="btn"
                    type="button"
                    disabled={
                      totalWeight !== 100 ||
                      !scorecardComplete ||
                      working !== null
                    }
                    onClick={saveScorecard}
                  >
                    {working === "save" ? "Saving…" : "Save scorecard"}
                  </button>
                  <button
                    className="btn btn--primary"
                    type="button"
                    disabled={
                      totalWeight !== 100 ||
                      !scorecardComplete ||
                      working !== null
                    }
                    onClick={continueToPreflight}
                  >
                    {working === "start"
                      ? "Preparing…"
                      : "Continue to preflight"}
                  </button>
                </div>
                <span>
                  Audio and input mode are checked before the timer starts.
                </span>
              </footer>
            </section>
          ) : null}
        </div>
      ) : null}
    </main>
  );
}
