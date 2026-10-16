// Rubric Grader UI, served at /grader. One problem bank at a time, in five steps:
// Problem -> Rubric -> Responses -> Grade -> Review & export. Each step shows one
// default action; everything else sits in a collapsed side box. What an upload is
// missing is asked for as boxes to fill, never reported as an error.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, itemsFor, type Bank, type BankSummary, type Example, type Health, type Run, type RunSummary } from "./api";
import {
  describePrepared,
  download,
  choose,
  NO_FILE,
  planFor,
  prepareExplanations,
  prepareResponses,
  readTextFile,
  TEMPLATE_PROBLEM_INFO,
  type Chosen,
  type Plan,
  type Prepared,
} from "./csv";
import {
  ConfirmButton,
  RunProblem,
  SideBox,
  StepLayout,
  deleteBankQuestion,
  fmtDate,
  isLegacy,
  statusText,
  unitWord,
} from "./parts";
import { ResponsesStep } from "./Responses";
import { Results } from "./Results";
import { RubricStep } from "./Rubric";
import { errMsg, fmtInt } from "./util";
import "./grader.css";

type Step = "problem" | "rubric" | "responses" | "grade" | "results";
const STEPS: { id: Step; label: string }[] = [
  { id: "problem", label: "1 · Problem" },
  { id: "rubric", label: "2 · Rubric" },
  { id: "responses", label: "3 · Responses" },
  { id: "grade", label: "4 · Grade" },
  { id: "results", label: "5 · Review & export" },
];
const DEFAULT_HARD_FLAGS = ["error", "tie", "quote_missing", "no_evidence", "addresses_grader"];
const WORDINGS: Record<string, string> = {
  simple: "simple: what the human graders used (honest test)",
  detailed: "detailed: Chen's rewrite for the model",
  detailed_latest: "detailed_latest: Chen's best (tuned on these answers, so optimistic)",
};
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

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
  const [bank, setBank] = useState<Bank | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [idMap, setIdMap] = useState<Record<string, string>>({});
  // The one-time-id keys of the banks used in this tab. They live only here: never sent, lost on reload.
  const idMaps = useRef<Record<string, Record<string, string>>>({});
  const [step, setStep] = useState<Step>("problem");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const prevStatus = useRef<string | undefined>(undefined);

  /** Open a saved bank with its latest (or the given) run, on the step where work continues. */
  const openBank = useCallback(async (id: string, runId?: string) => {
    const [b, rs] = await Promise.all([api.getBank(id), api.listRuns(id)]);
    const pick = rs.find((r) => r.id === runId) ?? rs[0];
    const r = pick ? await api.getRun(pick.id).catch(() => null) : null;
    setBank(b);
    setRuns(rs);
    setRun(r);
    setIdMap(idMaps.current[b.id] ?? {});
    saveSession({ assessmentId: b.id, runId: r?.id });
    if (r) setStep(r.status === "running" ? "grade" : "results");
    else if (b.rubric_status === "approved" && !isLegacy(b)) setStep(b.responses.length ? "grade" : "responses");
    else setStep("rubric");
  }, []);

  useEffect(() => {
    document.title = "Rubric Grader";
    api.health().then(setHealth).catch((e) => setError(errMsg(e)));
    api.examples().then(setExamples).catch(() => setExamples([]));
    const s = loadSession();
    if (s.assessmentId) openBank(s.assessmentId, s.runId).catch(() => saveSession({}));
  }, [openBank]);

  const refreshBank = useCallback((id: string) => {
    Promise.all([api.getBank(id), api.listRuns(id)])
      .then(([b, rs]) => {
        setBank(b);
        setRuns(rs);
      })
      .catch(() => undefined);
  }, []);

  // Poll while a run is in flight; when it stops, refresh the bank and show the results.
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const timer = setInterval(() => {
      api
        .getRun(run.id)
        .then((latest) => setRun((shown) => (shown && shown.id === latest.id ? latest : shown)))
        .catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [run?.id, run?.status]);
  useEffect(() => {
    if (prevStatus.current === "running" && run && run.status !== "running") {
      refreshBank(run.assessment_id);
      if (run.status === "done") setStep((s) => (s === "grade" ? "results" : s));
    }
    prevStatus.current = run?.status;
  }, [run, refreshBank]);

  const onCreated = (b: Bank, map: Record<string, string> = {}, note: string | null = null) => {
    setBank(b);
    setRuns([]);
    setRun(null);
    idMaps.current[b.id] = map;
    setIdMap(map);
    setNotice(note);
    setError(null);
    saveSession({ assessmentId: b.id });
    setStep("rubric");
  };
  const onStarted = (r: Run) => {
    setRun(r);
    saveSession({ assessmentId: r.assessment_id, runId: r.id });
    refreshBank(r.assessment_id);
  };
  const openRun = (id: string) => {
    api
      .getRun(id)
      .then((r) => {
        setRun(r);
        saveSession({ assessmentId: r.assessment_id, runId: r.id });
      })
      .catch((e) => setError(errMsg(e)));
  };
  const closeBank = (note: string | null) => {
    if (bank) delete idMaps.current[bank.id];
    setBank(null);
    setRuns([]);
    setRun(null);
    setIdMap({});
    setNotice(note);
    saveSession({});
    setStep("problem");
  };

  const mine = run && bank && run.assessment_id === bank.id ? run : null;
  const reachable = (s: Step) => s === "problem" || (!!bank && (s !== "results" || !!mine));
  const exampleCount = useMemo(() => {
    if (!bank || !bank.source.startsWith("example:chen") || !examples.length) return null;
    const problem = bank.source.split(":")[2]; // "example:chen:Q2", or the whole kit without it
    return examples[0].problems
      .filter((p) => (problem ? p.id === problem : bank.problems.some((v) => v.id === p.id)))
      .reduce((n, p) => n + p.responses, 0);
  }, [bank, examples]);

  return (
    <div className="g-app">
      <header className="g-header">
        <h1>
          Rubric <span>Grader</span>
        </h1>
        <span className="g-sub">one problem bank at a time · binary rubric items · you review the uncertain grades</span>
        {health && (
          <span
            className="g-badge"
            title={`Grading: ${health.model}, prompt version ${health.prompt_version}. Rubric drafting: ${health.draft_model ?? "not available"}, prompt version ${health.draft_prompt_version ?? "none"}.`}
          >
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
        {bank && (
          <span className="g-current">
            {bank.name} · {plural(bank.problems.length, unitWord(isLegacy(bank)))} · {plural(bank.check.counts.items, "item")} ·{" "}
            {plural(bank.responses.length, "response")} · {statusText(bank)}
          </span>
        )}
      </nav>
      <main className="g-main">
        {step === "problem" && (
          <ProblemStep
            examples={examples}
            currentId={bank?.id}
            onCreated={onCreated}
            onOpen={(id) => openBank(id).catch((e) => setError(errMsg(e)))}
            onDeleted={(id, note) => (id === bank?.id ? closeBank(note) : setNotice(note))}
            onError={setError}
            onNotice={setNotice}
          />
        )}
        {step === "rubric" && bank && (
          <RubricStep
            key={bank.id}
            bank={bank}
            runCount={runs.length}
            onBank={setBank}
            onError={setError}
            onNotice={setNotice}
            onNext={() => setStep("responses")}
          />
        )}
        {step === "responses" && bank && (
          <ResponsesStep
            key={bank.id}
            bank={bank}
            idMap={idMap}
            exampleCount={exampleCount}
            onBank={setBank}
            onIdMap={(map) => {
              idMaps.current[bank.id] = map;
              setIdMap(map);
            }}
            onNotice={setNotice}
            onError={setError}
            onGoRubric={() => setStep("rubric")}
            onNext={() => setStep("grade")}
          />
        )}
        {step === "grade" && bank && (
          <GradeStep
            key={bank.id}
            bank={bank}
            health={health}
            run={mine}
            onError={setError}
            onStarted={onStarted}
            onRunUpdate={setRun}
            onResults={() => setStep("results")}
            onGo={setStep}
          />
        )}
        {step === "results" && bank && mine && (
          <Results
            key={mine.id}
            bank={bank}
            run={mine}
            runs={runs}
            idMap={idMap}
            hardFlags={health?.hard_flags ?? DEFAULT_HARD_FLAGS}
            onRunChange={setRun}
            onOpenRun={openRun}
            onDeleted={closeBank}
            onError={setError}
          />
        )}
      </main>
    </div>
  );
}

// --- 1. Problem --------------------------------------------------------------

/** Names to strip and one-time ids: asked for wherever answers are uploaded. */
function AnswerPrivacy({
  roster,
  pseudonymize,
  onRoster,
  onPseudonymize,
}: {
  roster: string;
  pseudonymize: boolean;
  onRoster: (v: string) => void;
  onPseudonymize: (v: boolean) => void;
}) {
  return (
    <>
      <label className="g-field">
        Names to strip from the answers, one per line
        <textarea rows={2} value={roster} onChange={(e) => onRoster(e.target.value)} placeholder={"Jane Doe\nAli Khan"} />
      </label>
      <label className="g-check">
        <input type="checkbox" checked={pseudonymize} onChange={(e) => onPseudonymize(e.target.checked)} />
        Replace student ids with one-time ids (the key stays in this tab)
      </label>
    </>
  );
}

function ProblemStep({
  examples,
  currentId,
  onCreated,
  onOpen,
  onDeleted,
  onError,
  onNotice,
}: {
  examples: Example[];
  currentId: string | undefined;
  onCreated: (b: Bank, map?: Record<string, string>, note?: string | null) => void;
  onOpen: (id: string) => void;
  onDeleted: (id: string, note: string) => void;
  onError: (m: string) => void;
  onNotice: (m: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [files, setFiles] = useState<Chosen[]>([]);
  const [roster, setRoster] = useState("");
  const [pseudonymize, setPseudonymize] = useState(true);
  // Other ways to start
  const chen = examples.find((e) => e.id === "chen");
  const [problem, setProblem] = useState("Q2");
  const [wording, setWording] = useState("simple");
  const [pastedInfo, setPastedInfo] = useState("");
  const [pastedResponses, setPastedResponses] = useState("");
  const [material, setMaterial] = useState("");
  const [banks, setBanks] = useState<BankSummary[] | null>(null);

  const loadBanks = useCallback(() => {
    api.listBanks().then(setBanks).catch(() => setBanks([]));
  }, []);
  useEffect(loadBanks, [loadBanks]);

  const guard = async (work: () => Promise<void>) => {
    setBusy(true);
    try {
      await work();
    } catch (e) {
      onError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  /** Read the chosen files and say what each one is. A file chosen again replaces the earlier one. */
  const addFiles = async (input: HTMLInputElement) => {
    const list = Array.from(input.files ?? []);
    input.value = ""; // so that choosing the same file again, after editing it, works
    const read = (await Promise.all(list.map(readTextFile))).map(choose);
    setFiles((fs) => [...fs.filter((f) => !read.some((r) => r.name === f.name)), ...read]);
  };
  const plan = planFor(files);

  /**
   * Create a bank from what a plan loads. Answers are cleaned, their names stripped
   * and their ids swapped here, in the browser, before they are sent; they are held
   * until the rubric is approved.
   */
  const load = (plan: Plan) =>
    guard(async () => {
      const { problems, rubric, answers } = plan;
      if (!plan.ready || !problems) {
        onError(plan.say);
        return;
      }
      const names = roster.split("\n");
      const bankName = name.trim() || problems.name.replace(/\.[^.]+$/, "");
      let bank: Bank;
      let prepared: Prepared | null = null;
      if (problems.kind === "explanations") {
        const split = prepareExplanations(problems.text, names, pseudonymize);
        prepared = split;
        bank = await api.uploadProblemInfo(bankName, split.versionsCsv, split.csv, rubric?.text ?? "");
      } else {
        prepared = answers?.text.trim() ? prepareResponses(answers.text, names, pseudonymize) : null;
        bank = await api.uploadProblemInfo(bankName, problems.text, prepared?.csv ?? "", rubric?.text ?? "");
      }
      const notes = [
        prepared
          ? `${plural(bank.held_responses, "response is", "responses are")} held until the rubric is approved. ${describePrepared(prepared)}`
          : "",
        ...plan.unused,
        problems.note,
        rubric?.note ?? "",
        answers?.note ?? "",
      ].filter(Boolean);
      onCreated(bank, prepared?.idMap ?? {}, notes.length ? notes.join(" ") : null);
      setFiles([]);
    });
  const pasted = () =>
    load(
      planFor([
        choose({ ...NO_FILE, text: pastedInfo }),
        ...(pastedResponses.trim()
          ? [{ ...NO_FILE, name: "the pasted responses", text: pastedResponses, kind: "responses" as const, sentence: "" }]
          : []),
      ]),
    );

  const side = (
    <SideBox title="Other ways to start">
      <h4>Try the worked example</h4>
      {chen ? (
        <>
          <p className="g-hint">{chen.description}</p>
          <div className="g-form">
            <label className="g-field">
              Problem
              <select value={problem} onChange={(e) => setProblem(e.target.value)}>
                {chen.problems.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id} · {p.responses} responses
                  </option>
                ))}
              </select>
            </label>
            <label className="g-field">
              Rubric wording
              <select value={wording} onChange={(e) => setWording(e.target.value)}>
                {chen.wordings.map((w) => (
                  <option key={w} value={w}>
                    {WORDINGS[w] ?? w}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="g-row">
            <button
              className="g-ghost small"
              disabled={busy}
              onClick={() => guard(async () => onCreated(await api.loadExample(problem, wording)))}
            >
              Load the example
            </button>
          </div>
        </>
      ) : (
        <p className="g-hint">The Chen &amp; Wan example isn't installed on this server.</p>
      )}

      <h4>Paste the CSV text instead</h4>
      <textarea
        className="g-paste"
        rows={4}
        value={pastedInfo}
        onChange={(e) => setPastedInfo(e.target.value)}
        placeholder={"problem_id,problem_text,rubric_item,weight\n…"}
        aria-label="Problem Info CSV text"
      />
      <textarea
        className="g-paste"
        rows={3}
        value={pastedResponses}
        onChange={(e) => setPastedResponses(e.target.value)}
        placeholder={"Optional: responses to hold until approval\nstudent_id,problem_id,response_text\n…"}
        aria-label="Responses CSV text (optional)"
      />
      {pastedResponses.trim() && (
        <AnswerPrivacy roster={roster} pseudonymize={pseudonymize} onRoster={setRoster} onPseudonymize={setPseudonymize} />
      )}
      <div className="g-row">
        <button className="g-ghost small" disabled={busy || !pastedInfo.trim()} onClick={pasted}>
          Use the pasted text
        </button>
      </div>

      <h4>Paste unstructured material</h4>
      <p className="g-hint">
        Paste a problem with its solution, your grading notes or a point scheme, in any form. The AI drafts a problem
        bank from it: the problem text and rubric items. Nothing it writes is used until you accept it on the next
        step. Do not paste student work: the text goes to the AI as it is.
      </p>
      <textarea
        className="g-paste"
        rows={5}
        value={material}
        onChange={(e) => setMaterial(e.target.value)}
        placeholder={"Problem: …\nSolution: …\nGrading notes: …"}
        aria-label="Unstructured material"
      />
      <div className="g-row">
        <button
          className="g-ghost small"
          disabled={busy || !material.trim()}
          onClick={() =>
            guard(async () => {
              onCreated(await api.draftFromMaterial(name.trim(), material));
              setMaterial("");
            })
          }
        >
          Draft a rubric from this
        </button>
      </div>

      <h4>Saved problem banks</h4>
      {banks === null && <p className="g-hint">Loading…</p>}
      {banks?.length === 0 && <p className="g-hint">None yet.</p>}
      <ul className="g-banks">
        {banks?.map((b) => (
          <li key={b.id}>
            <div>
              <strong>{b.name}</strong>
              {b.id === currentId && <span className="g-tag">open now</span>}
              <div className="g-hint">
                {b.kind === "unreadable" ? (
                  "This saved file cannot be read. It can only be deleted."
                ) : (
                  <>
                    {plural(b.versions, unitWord(b.kind === "legacy"))} · {plural(b.items, "item")} ·{" "}
                    {plural(b.responses, "response")} · {plural(b.runs, "run")} ·{" "}
                    {b.kind === "legacy"
                      ? "saved by an earlier version, view only"
                      : b.rubric_status === "approved"
                        ? `rubric approved, revision ${b.rubric_revision}`
                        : "rubric not approved"}
                  </>
                )}
                {b.created ? ` · ${fmtDate(b.created)}` : ""}
              </div>
            </div>
            <div className="g-row">
              {b.kind !== "unreadable" && (
                <button className="g-ghost small" disabled={busy} onClick={() => onOpen(b.id)}>
                  Open
                </button>
              )}
              <ConfirmButton
                label="Delete"
                question={deleteBankQuestion(b.name, b.kind)}
                confirm="Delete it"
                disabled={busy}
                onConfirm={() =>
                  guard(async () => {
                    const gone = await api.deleteBank(b.id);
                    onDeleted(
                      b.id,
                      `Deleted "${b.name}": the bank, ${plural(gone.runs, "run")} and ${plural(gone.cached_samples, "cached model answer")}.`,
                    );
                    loadBanks();
                  })
                }
              />
            </div>
          </li>
        ))}
      </ul>
      <ConfirmButton
        label="Clear all cached model answers"
        question="Remove every cached model answer, for every problem bank? Grades you already have are kept. Grading the same responses again will call the model again."
        confirm="Clear them"
        disabled={busy}
        onConfirm={() =>
          guard(async () => {
            const gone = await api.clearCache();
            onNotice(`Removed ${plural(gone.deleted, "cached model answer")}.`);
          })
        }
      />
    </SideBox>
  );

  return (
    <StepLayout side={side}>
      <section className="g-card">
        <h2>Upload a problem or problem bank</h2>
        <p className="g-muted">
          Choose the files you have: a Problem Info CSV or an explanations export with the problem in one or more
          versions, and a rubric file if you have one. Each file is recognised before anything is loaded. Whatever is
          missing can be filled in on the next step.
        </p>
        <label className="g-field">
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: Spring launch problem" />
        </label>
        <label className="g-field">
          Files <span className="g-hint">Problem Info CSV, explanations export, rubric (YAML), responses</span>
          <input
            type="file"
            multiple
            accept=".csv,.tsv,.txt,.yaml,.yml,text/csv"
            onChange={(e) => void addFiles(e.target)}
          />
        </label>
        {files.length > 0 && (
          <ul className="g-files">
            {files.map((f) => (
              <li key={f.name}>
                <strong>{f.name}</strong>
                <span>{f.sentence}</span>
                <button
                  className="g-ghost small"
                  aria-label={`Remove ${f.name}`}
                  onClick={() => setFiles((fs) => fs.filter((x) => x.name !== f.name))}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        {plan.say && <p className={plan.ready ? "g-muted" : "g-callout"}>{plan.say}</p>}
        {plan.unused.map((u) => (
          <p className="g-hint" key={u}>
            {u}
          </p>
        ))}
        {(plan.problems?.kind === "explanations" || plan.answers) && (
          <AnswerPrivacy roster={roster} pseudonymize={pseudonymize} onRoster={setRoster} onPseudonymize={setPseudonymize} />
        )}
        <div className="g-row">
          <button className="g-primary" disabled={busy || !plan.ready} onClick={() => load(plan)}>
            Upload
          </button>
          <button className="g-link-button" onClick={() => download("problem_info_template.csv", TEMPLATE_PROBLEM_INFO)}>
            Download a template
          </button>
        </div>
      </section>
    </StepLayout>
  );
}

// --- 4. Grade ----------------------------------------------------------------

const RUN_STATUS: Record<string, string> = {
  running: "in progress",
  done: "finished",
  cancelled: "stopped",
  error: "stopped by a problem",
};

function GradeStep({
  bank,
  health,
  run,
  onError,
  onStarted,
  onRunUpdate,
  onResults,
  onGo,
}: {
  bank: Bank;
  health: Health | null;
  run: Run | null;
  onError: (m: string) => void;
  onStarted: (r: Run) => void;
  onRunUpdate: (r: Run) => void;
  onResults: () => void;
  onGo: (s: Step) => void;
}) {
  const [k, setK] = useState(3);
  const [effort, setEffort] = useState("minimal");
  const [concurrency, setConcurrency] = useState(6);
  const [maxResponses, setMaxResponses] = useState("");
  const [busy, setBusy] = useState(false);
  const running = run?.status === "running";
  const blockers = bank.check.grade_blockers;

  const { responses, pairs } = useMemo(() => {
    const cap = maxResponses ? Math.max(1, Number(maxResponses)) : Infinity;
    let responses = 0;
    let pairs = 0;
    for (const p of bank.problems) {
      const n = Math.min(cap, bank.responses.filter((r) => r.problem_id === p.id && !r.excluded).length);
      responses += n;
      pairs += n * itemsFor(bank, p.id).length;
    }
    return { responses, pairs };
  }, [bank, maxResponses]);

  const start = async () => {
    setBusy(true);
    try {
      onStarted(
        await api.startRun({
          assessment_id: bank.id,
          k,
          effort,
          concurrency,
          problem_ids: null,
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
    if (!run) return;
    try {
      await api.cancel(run.id);
      onRunUpdate(await api.getRun(run.id));
    } catch (e) {
      onError(errMsg(e));
    }
  };

  const side = isLegacy(bank) ? undefined : (
    <SideBox title="Run settings">
      <label className="g-field">
        Votes per item
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
        Max responses per version
        <input type="number" min={1} placeholder="all" value={maxResponses} onChange={(e) => setMaxResponses(e.target.value)} />
      </label>
    </SideBox>
  );

  return (
    <StepLayout side={side}>
      <section className="g-card">
        <h2>Grade</h2>
        {blockers.length > 0 ? (
          <>
            <p className="g-callout">Grading cannot start yet.</p>
            <ul className="g-reasons">
              {blockers.map((b, i) => (
                <li key={i}>
                  <button className="g-link-button" onClick={() => onGo(b.step)}>
                    {b.message} →
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <>
            <p className="g-muted">
              {plural(responses, "response")} × the rubric items of {responses === 1 ? "its" : "their"} version ={" "}
              {plural(pairs, "grade")}, each decided by {plural(k, "vote")}: <strong>{pairs * k}</strong> model calls
              {health ? ` on ${health.model}` : ""}. Grading the same thing again is free, because answers are cached.
            </p>
            <div className="g-row">
              <button className="g-primary" disabled={busy || running || pairs === 0} onClick={start}>
                {running ? "Grading…" : run ? "Grade again" : "Start grading"}
              </button>
            </div>
          </>
        )}
      </section>
      {run && <RunProgress run={run} onCancel={cancel} onResults={onResults} />}
    </StepLayout>
  );
}

function RunProgress({ run, onCancel, onResults }: { run: Run; onCancel: () => void; onResults: () => void }) {
  const pct = run.total_calls ? Math.round((100 * run.done_calls) / run.total_calls) : 0;
  const elapsed = Math.max(0, (run.finished ?? Date.now() / 1000) - run.started);
  return (
    <section className="g-card">
      <div className="g-row between">
        <h3>
          {run.status === "running" ? "Grading" : "Last run"} · {RUN_STATUS[run.status] ?? run.status}
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
      <RunProblem run={run} />
    </section>
  );
}
