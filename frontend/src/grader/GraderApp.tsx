// Rubric Grader UI, served at /grader. Four steps: load an exam (the Chen example
// or CSV uploads, with names stripped in the browser) -> review/edit the rubric
// wording -> run grading -> review uncertain calls and export 0/1 grades.
import { useEffect, useMemo, useRef, useState } from "react";

import { api, type Assessment, type Example, type Health, type Run } from "./api";
import { download, prepareResponses, SAMPLE_RESPONSES, SAMPLE_RUBRIC } from "./csv";
import { Results } from "./Results";
import { errMsg, fmtInt } from "./util";
import "./grader.css";

type Step = "load" | "rubric" | "grade" | "results";
const STEPS: { id: Step; label: string }[] = [
  { id: "load", label: "1 · Load exam" },
  { id: "rubric", label: "2 · Rubric" },
  { id: "grade", label: "3 · Grade" },
  { id: "results", label: "4 · Review & export" },
];
const DEFAULT_HARD_FLAGS = ["error", "tie", "quote_missing", "no_evidence", "addresses_grader"];

const SESSION_KEY = "grader.session";
type Session = { assessmentId?: string; runId?: string };
function saveSession(s: Session) {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(s));
  } catch {
    /* storage unavailable: the session just won't survive a reload */
  }
}
function loadSession(): Session {
  try {
    return JSON.parse(localStorage.getItem(SESSION_KEY) ?? "{}") as Session;
  } catch {
    return {};
  }
}

export default function GraderApp() {
  const [health, setHealth] = useState<Health | null>(null);
  const [examples, setExamples] = useState<Example[]>([]);
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [idMap, setIdMap] = useState<Record<string, string>>({});
  const [run, setRun] = useState<Run | null>(null);
  const [step, setStep] = useState<Step>("load");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const prevStatus = useRef<string | undefined>(undefined);

  useEffect(() => {
    document.title = "Rubric Grader";
    api.health().then(setHealth).catch((e) => setError(`Can't reach the grader API: ${errMsg(e)}`));
    api.examples().then(setExamples).catch(() => setExamples([]));
    const s = loadSession();
    if (!s.assessmentId) return;
    api
      .getAssessment(s.assessmentId)
      .then((a) => {
        setAssessment(a);
        setStep("rubric");
        if (!s.runId) return;
        api
          .getRun(s.runId)
          .then((r) => {
            if (r.assessment_id !== a.id) return;
            setRun(r);
            setStep(r.status === "running" ? "grade" : "results");
          })
          .catch(() => saveSession({ assessmentId: a.id }));
      })
      .catch(() => saveSession({}));
  }, []);

  // Poll while a run is in flight; jump to results when it finishes.
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const timer = setInterval(() => {
      api.getRun(run.id).then(setRun).catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [run?.id, run?.status]);
  useEffect(() => {
    if (prevStatus.current === "running" && run?.status === "done" && step === "grade") setStep("results");
    prevStatus.current = run?.status;
  }, [run?.status, step]);

  const onLoaded = (a: Assessment, map: Record<string, string> = {}, note: string | null = null) => {
    setAssessment(a);
    setIdMap(map);
    setRun(null);
    setNotice(note);
    saveSession({ assessmentId: a.id });
    setStep("rubric");
  };
  const onStarted = (r: Run) => {
    setRun(r);
    saveSession({ assessmentId: r.assessment_id, runId: r.id });
  };
  const reachable = (s: Step) => s === "load" || (!!assessment && (s !== "results" || !!run));

  return (
    <div className="g-app">
      <header className="g-header">
        <h1>
          Rubric <span>Grader</span>
        </h1>
        <span className="g-sub">binary rubric items · k votes each · you review the uncertain ones</span>
        {health && (
          <span className="g-badge" title={`prompt version ${health.prompt_version}`}>
            {health.profile} · {health.model}
          </span>
        )}
        <a className="g-link" href="/">
          DemoBuilder ↗
        </a>
      </header>
      {error && (
        <div className="g-error" role="alert">
          <span>{error}</span>
          <button onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}
      {notice && (
        <div className="g-notice">
          <span>{notice}</span>
          <button onClick={() => setNotice(null)}>OK</button>
        </div>
      )}
      <nav className="g-steps">
        {STEPS.map((s) => (
          <button
            key={s.id}
            className={step === s.id ? "active" : ""}
            disabled={!reachable(s.id)}
            onClick={() => setStep(s.id)}
          >
            {s.label}
          </button>
        ))}
        {assessment && (
          <span className="g-current">
            {assessment.name} · {assessment.responses.length} responses · {assessment.criteria.length} items
          </span>
        )}
      </nav>
      <main className="g-main">
        {step === "load" && <LoadStep examples={examples} onError={setError} onLoaded={onLoaded} />}
        {step === "rubric" && assessment && (
          <RubricStep assessment={assessment} onChange={setAssessment} onError={setError} onNext={() => setStep("grade")} />
        )}
        {step === "grade" && assessment && (
          <GradeStep
            assessment={assessment}
            health={health}
            run={run}
            onError={setError}
            onStarted={onStarted}
            onRunUpdate={setRun}
            onResults={() => setStep("results")}
          />
        )}
        {step === "results" && assessment && run && (
          <Results
            assessment={assessment}
            run={run}
            idMap={idMap}
            hardFlags={health?.hard_flags ?? DEFAULT_HARD_FLAGS}
            onRunChange={setRun}
            onError={setError}
          />
        )}
      </main>
    </div>
  );
}

// --- 1. Load -----------------------------------------------------------------

function LoadStep({
  examples,
  onError,
  onLoaded,
}: {
  examples: Example[];
  onError: (m: string) => void;
  onLoaded: (a: Assessment, map?: Record<string, string>, note?: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState("simple");
  const [name, setName] = useState("My exam");
  const [rubric, setRubric] = useState({ text: "", file: "" });
  const [responses, setResponses] = useState({ text: "", file: "" });
  const [roster, setRoster] = useState("");
  const [pseudonymize, setPseudonymize] = useState(true);
  const chen = examples.find((e) => e.id === "chen");

  const read = (file: File | undefined, set: (v: { text: string; file: string }) => void) => {
    if (file) file.text().then((text) => set({ text, file: file.name }));
  };

  const loadExample = async () => {
    setBusy(true);
    try {
      onLoaded(await api.loadExample(version));
    } catch (e) {
      onError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const upload = async () => {
    setBusy(true);
    try {
      const prepared = prepareResponses(responses.text, roster.split("\n"), pseudonymize);
      const a = await api.loadCsv(name.trim() || "My exam", rubric.text, prepared.csv);
      const ids = Object.keys(prepared.idMap).length;
      onLoaded(
        a,
        prepared.idMap,
        `Uploaded ${prepared.rows} responses. In your browser: ${prepared.redactions} name occurrence(s) replaced with [STUDENT]` +
          (ids ? `, ${ids} student ids swapped for one-time ids. The key exists only in this tab: exports map ids back; reloading loses it.` : "."),
      );
    } catch (e) {
      onError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="g-grid2">
      <section className="g-card">
        <h2>Try the worked example</h2>
        {chen ? (
          <>
            <p className="g-muted">{chen.description}</p>
            <label className="g-field">
              Rubric wording
              <select value={version} onChange={(e) => setVersion(e.target.value)}>
                <option value="simple">simple: what the human graders used (honest test)</option>
                <option value="detailed">detailed: Chen's rewrite for the model</option>
                <option value="detailed_latest">detailed_latest: Chen's best (tuned on these answers, so optimistic)</option>
              </select>
            </label>
            <button className="g-primary" disabled={busy} onClick={loadExample}>
              Load example
            </button>
          </>
        ) : (
          <p className="g-muted">The Chen &amp; Wan kit isn't installed on this server (grader_kit/ is empty).</p>
        )}
      </section>

      <section className="g-card">
        <h2>Upload an exam</h2>
        <label className="g-field">
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="g-field">
          Rubric CSV <span className="g-hint">problem_id, problem_text, item, criterion_text [, points]</span>
          <input type="file" accept=".csv,text/csv" onChange={(e) => read(e.target.files?.[0], setRubric)} />
        </label>
        <label className="g-field">
          Responses CSV <span className="g-hint">student_id, problem_id, response_text (text only)</span>
          <input type="file" accept=".csv,text/csv" onChange={(e) => read(e.target.files?.[0], setResponses)} />
        </label>
        <label className="g-field">
          Names to strip, one per line (optional)
          <span className="g-hint">replaced with [STUDENT] in your browser before anything is sent</span>
          <textarea rows={3} value={roster} onChange={(e) => setRoster(e.target.value)} placeholder={"Jane Doe\nAli Khan"} />
        </label>
        <label className="g-check">
          <input type="checkbox" checked={pseudonymize} onChange={(e) => setPseudonymize(e.target.checked)} />
          Replace student ids with one-time ids (the key stays in this tab)
        </label>
        <div className="g-row">
          <button className="g-primary" disabled={busy || !rubric.text || !responses.text} onClick={upload}>
            Upload
          </button>
          <button className="g-ghost" onClick={() => download("sample_rubric.csv", SAMPLE_RUBRIC)}>
            Sample rubric CSV
          </button>
          <button className="g-ghost" onClick={() => download("sample_responses.csv", SAMPLE_RESPONSES)}>
            Sample responses CSV
          </button>
        </div>
        {(rubric.file || responses.file) && (
          <p className="g-hint">
            {rubric.file && `Rubric: ${rubric.file}. `}
            {responses.file && `Responses: ${responses.file}.`}
          </p>
        )}
      </section>
    </div>
  );
}

// --- 2. Rubric -------------------------------------------------------------

function RubricStep({
  assessment,
  onChange,
  onError,
  onNext,
}: {
  assessment: Assessment;
  onChange: (a: Assessment) => void;
  onError: (m: string) => void;
  onNext: () => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const drop = (cid: string) =>
    setDrafts((d) => {
      const next = { ...d };
      delete next[cid];
      return next;
    });

  const save = async (cid: string) => {
    const text = drafts[cid];
    if (!text?.trim()) return;
    setSaving(cid);
    try {
      const updated = await api.editCriterion(assessment.id, cid, text);
      onChange({ ...assessment, criteria: assessment.criteria.map((c) => (c.id === cid ? updated : c)) });
      drop(cid);
    } catch (e) {
      onError(errMsg(e));
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="g-stack">
      <section className="g-card g-row between">
        <div>
          <h2>Rubric ({assessment.rubric_version})</h2>
          <p className="g-muted">
            Each item is graded on its own: credit (1) or no credit (0). Edit the wording the grader uses; the original
            stays for reference. Edits apply to the next run.
          </p>
        </div>
        <button className="g-primary" onClick={onNext}>
          Continue to grading →
        </button>
      </section>
      {assessment.problems.map((p) => (
        <section className="g-card" key={p.id}>
          <h3>
            Problem {p.id}{" "}
            <span className="g-muted">· {assessment.responses.filter((r) => r.problem_id === p.id).length} responses</span>
          </h3>
          <details>
            <summary>Problem text</summary>
            <pre className="g-pre">{p.text || "(none)"}</pre>
          </details>
          {p.instructions && (
            <details>
              <summary>What students were asked to write</summary>
              <pre className="g-pre">{p.instructions}</pre>
            </details>
          )}
          {assessment.criteria
            .filter((c) => c.problem_id === p.id)
            .map((c) => {
              const value = drafts[c.id] ?? c.text;
              const dirty = drafts[c.id] !== undefined && drafts[c.id] !== c.text;
              return (
                <div className="g-crit" key={c.id}>
                  <div className="g-crit-head">
                    <strong>{c.label}</strong>
                    {c.points !== 1 && <span className="g-muted"> · {c.points} pts</span>}
                  </div>
                  <textarea
                    rows={Math.min(14, Math.max(3, value.split("\n").length + 1))}
                    value={value}
                    onChange={(e) => setDrafts((d) => ({ ...d, [c.id]: e.target.value }))}
                  />
                  <div className="g-row">
                    {dirty && (
                      <>
                        <button className="g-primary small" disabled={saving === c.id} onClick={() => save(c.id)}>
                          Save
                        </button>
                        <button className="g-ghost small" onClick={() => drop(c.id)}>
                          Discard
                        </button>
                      </>
                    )}
                    {c.source_text && c.source_text !== c.text && (
                      <details className="g-orig">
                        <summary>Original wording</summary>
                        <pre className="g-pre">{c.source_text}</pre>
                      </details>
                    )}
                  </div>
                </div>
              );
            })}
        </section>
      ))}
    </div>
  );
}

// --- 3. Grade ----------------------------------------------------------------

function GradeStep({
  assessment,
  health,
  run,
  onError,
  onStarted,
  onRunUpdate,
  onResults,
}: {
  assessment: Assessment;
  health: Health | null;
  run: Run | null;
  onError: (m: string) => void;
  onStarted: (r: Run) => void;
  onRunUpdate: (r: Run) => void;
  onResults: () => void;
}) {
  const allIds = assessment.problems.map((p) => p.id);
  const [k, setK] = useState(3);
  const [effort, setEffort] = useState("minimal");
  const [concurrency, setConcurrency] = useState(6);
  const [problemIds, setProblemIds] = useState<string[]>(allIds);
  const [maxResponses, setMaxResponses] = useState("");
  const [busy, setBusy] = useState(false);
  const mine = run && run.assessment_id === assessment.id ? run : null;
  const running = mine?.status === "running";

  const pairs = useMemo(() => {
    const cap = maxResponses ? Math.max(1, Number(maxResponses)) : Infinity;
    return problemIds.reduce((sum, pid) => {
      const nResp = Math.min(cap, assessment.responses.filter((r) => r.problem_id === pid).length);
      return sum + nResp * assessment.criteria.filter((c) => c.problem_id === pid).length;
    }, 0);
  }, [assessment, problemIds, maxResponses]);

  const start = async () => {
    setBusy(true);
    try {
      onStarted(
        await api.startRun({
          assessment_id: assessment.id,
          k,
          effort,
          concurrency,
          problem_ids: problemIds.length === allIds.length ? null : problemIds,
          max_responses: maxResponses ? Number(maxResponses) : null,
        }),
      );
    } catch (e) {
      onError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!mine) return;
    try {
      await api.cancel(mine.id);
      onRunUpdate(await api.getRun(mine.id));
    } catch (e) {
      onError(errMsg(e));
    }
  };

  const toggle = (pid: string) =>
    setProblemIds((ids) => (ids.includes(pid) ? ids.filter((x) => x !== pid) : [...ids, pid]));

  return (
    <div className="g-stack">
      <section className="g-card">
        <h2>Grade</h2>
        <div className="g-form">
          <label className="g-field">
            Votes per item (k)
            <select value={k} onChange={(e) => setK(Number(e.target.value))}>
              <option value={1}>1: fastest, no confidence signal</option>
              <option value={3}>3</option>
              <option value={5}>5: Chen's setting</option>
            </select>
          </label>
          <label className="g-field">
            Reasoning effort
            <select value={effort} onChange={(e) => setEffort(e.target.value)}>
              {(health?.efforts ?? ["minimal", "low", "medium"]).map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </label>
          <label className="g-field">
            Parallel calls
            <input type="number" min={1} max={16} value={concurrency} onChange={(e) => setConcurrency(Number(e.target.value) || 1)} />
          </label>
          <label className="g-field">
            Max responses per problem
            <input type="number" min={1} placeholder="all" value={maxResponses} onChange={(e) => setMaxResponses(e.target.value)} />
          </label>
        </div>
        <div className="g-row">
          <span className="g-muted">Problems:</span>
          {assessment.problems.map((p) => (
            <label className="g-check" key={p.id}>
              <input type="checkbox" checked={problemIds.includes(p.id)} onChange={() => toggle(p.id)} /> {p.id}
            </label>
          ))}
        </div>
        <p className="g-muted">
          {pairs} response × item pairs × {k} = <strong>{pairs * k}</strong> model calls
          {health ? ` on ${health.model}` : ""}. Re-running identical settings is free (cached).
        </p>
        <div className="g-row">
          <button className="g-primary" disabled={busy || running || pairs === 0} onClick={start}>
            {running ? "Running…" : "Start grading"}
          </button>
        </div>
      </section>
      {mine && <RunProgress run={mine} onCancel={cancel} onResults={onResults} />}
    </div>
  );
}

function RunProgress({ run, onCancel, onResults }: { run: Run; onCancel: () => void; onResults: () => void }) {
  const pct = run.total_calls ? Math.round((100 * run.done_calls) / run.total_calls) : 0;
  const elapsed = Math.max(0, (run.finished ?? Date.now() / 1000) - run.started);
  return (
    <section className="g-card">
      <div className="g-row between">
        <h3>
          Run {run.id} · {run.status}
        </h3>
        {run.status === "running" ? (
          <button className="g-ghost" onClick={onCancel}>
            Cancel
          </button>
        ) : (
          <button className="g-primary" onClick={onResults}>
            Review results →
          </button>
        )}
      </div>
      <div className="g-bar">
        <div style={{ width: `${pct}%` }} />
      </div>
      <p className="g-muted">
        {run.done_calls}/{run.total_calls} calls ({pct}%) · {run.cached_calls} cached · {run.failed_calls} failed ·{" "}
        {Math.round(elapsed)}s · tokens in {fmtInt(run.usage.input_tokens)} / out {fmtInt(run.usage.output_tokens)}
      </p>
      {run.error && <p className="g-warn">{run.error}</p>}
    </section>
  );
}
