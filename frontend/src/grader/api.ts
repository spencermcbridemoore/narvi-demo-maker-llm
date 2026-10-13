// Typed client for the grader API (backend/app/grader/api.py, mounted at /grader/api).
import { BACKEND_URL } from "../config";

/** One version of the bank's problem. */
export type Problem = { id: string; text: string; instructions: string };
export type Criterion = {
  id: string;
  problem_id: string;
  key: string;
  label: string;
  text: string;
  source_text: string;
  points: number; // the weight; 0 = not set yet
  accept: string[];
  reject: string[];
  origin: string; // teacher | copied | ai | ai_inferred
};
export type ResponseItem = {
  id: string;
  problem_id: string; // "" = version unknown
  text: string;
  external_id: string;
  version_label: string;
  excluded: boolean;
  fingerprint: string; // a hash of the text, to line a response up with a run's grades
};

/** Something intake noticed in an upload. History: the live `check` says what blocks now. */
export type Finding = {
  code: string;
  message: string;
  filled: "code" | "owner" | "note";
  scope: "rubric" | "responses";
  box: string;
  problem_id: string;
  item_key: string;
  response_id: string;
  value: string;
  row: number | null;
};

/** A box that still has to be filled before the rubric can be approved. */
export type Box = { box: string; problem_id: string; item_key: string; message: string };
/** A reason grading cannot start, and the step where it is fixed. */
export type GradeBlocker = { step: "rubric" | "responses"; message: string; response_ids?: string[] };

export type Check = {
  legacy: boolean;
  editable: boolean;
  can_approve: boolean;
  can_grade: boolean;
  to_fill: Box[];
  grade_blockers: GradeBlocker[];
  warnings: string[];
  rubric_state: "none" | "some" | "all";
  without_rubric: string[];
  counts: {
    versions: number;
    items: number;
    responses: number;
    excluded: number;
    unknown_version: number;
    per_version: Record<string, number>;
  };
};

/** A problem bank: one problem in one or more versions, plus the live check. */
export type Bank = {
  id: string;
  name: string;
  source: string;
  kind: string; // "bank", anything else is a bank saved by an earlier version
  rubric_version: string;
  problems: Problem[];
  criteria: Criterion[];
  responses: ResponseItem[];
  references: Record<string, { ref_a: number; ref_b: number }>;
  created: number;
  rubric_status: "draft" | "approved";
  rubric_hash: string;
  rubric_revision: number;
  rubric_approved_at: number | null;
  findings: Finding[];
  ai_fill: boolean;
  held_responses: number;
  check: Check;
  running: boolean;
};

export type BankSummary = {
  id: string;
  name: string;
  kind: "bank" | "legacy" | "unreadable"; // unreadable: a saved file that can only be deleted
  source: string;
  versions: number;
  items: number;
  responses: number;
  runs: number;
  rubric_status: "draft" | "approved";
  rubric_revision: number;
  created: number;
};

export type Sample = {
  verdict: number | null;
  comparison: string;
  evidence: string[];
  quotes_found: boolean | null;
  error: string | null;
  cached: boolean;
};
export type CellCompact = {
  instance_id: string;
  response_id: string;
  criterion_id: string;
  final: number | null;
  confidence: number | null;
  votes: string;
  flags: string[];
  resolved_by: string;
  override: number | null;
};
export type Cell = CellCompact & { samples: Sample[] };

export type RunConfig = {
  k: number;
  effort: string;
  concurrency: number;
  problem_ids: string[] | null;
  max_responses: number | null;
};
/** The item as a run graded it. */
export type RubricItem = {
  id: string;
  key: string;
  label: string;
  text: string;
  points: number;
  accept?: string[];
  reject?: string[];
};
export type RubricVersion = { id: string; text: string; instructions: string; items: RubricItem[] };
export type RunSummary = {
  id: string;
  assessment_id: string;
  config: RunConfig;
  status: "running" | "done" | "cancelled" | "error";
  model: string;
  prompt_version: string;
  total_calls: number;
  done_calls: number;
  failed_calls: number;
  cached_calls: number;
  started: number;
  finished: number | null;
  usage: Record<string, number>;
  error: string | null;
  rubric_hash?: string;
  rubric_revision?: number;
};
export type Run = RunSummary & {
  cells: CellCompact[];
  /** The versions and items this run graded with; absent on runs saved before it was recorded. */
  rubric_copy?: { versions: RubricVersion[] } | null;
  /** Response id -> fingerprint of the text this run graded; absent on runs saved before it was recorded. */
  response_fingerprints?: Record<string, string> | null;
};

export type Health = {
  ok: boolean;
  profile: string;
  model: string;
  prompt_version: string;
  kit: boolean;
  efforts: string[];
  hard_flags: string[];
};
export type Example = {
  id: string;
  name: string;
  description: string;
  problems: { id: string; responses: number; items: number }[];
  wordings: string[];
};

export type EvalProblem = {
  problem_id: string;
  n_responses: number;
  exact_match_ref_a: number | null;
  exact_match_ref_b: number | null;
  wrong_vs_both: number | null;
  human_exact_match: number | null;
};
export type EvalResult = {
  n_instances: number;
  n_covered: number;
  coverage: number;
  problems: EvalProblem[];
  overall: {
    macro_f1_ref_a: number | null;
    macro_f1_ref_b: number | null;
    item_acc_ref_a: number | null;
    item_acc_ref_b: number | null;
    mean_exact_match_ref_a: number | null;
    mean_exact_match_ref_b: number | null;
  };
  confidence?: {
    auroc_flag_wrong: number | null;
    n_agreed_scored: number;
    n_wrong: number;
    wrong_caught_reviewing_10pct: number | null;
    wrong_caught_reviewing_15pct: number | null;
    wrong_caught_reviewing_20pct: number | null;
  };
};
export type Metrics = { run?: EvalResult; human?: EvalResult };

/**
 * A request the grader refused or could not complete. `message` is a plain sentence
 * to show as it is; `reasons` are the boxes still to fill, when the refusal names
 * them. The HTTP status never reaches the screen.
 */
export class ApiError extends Error {
  reasons: { message: string }[];
  constructor(message: string, reasons: { message: string }[] = []) {
    super(message);
    this.reasons = reasons;
  }
}

const BASE = `${BACKEND_URL}/grader/api`;

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("The grader could not be reached. Check that it is running, then try again.");
  }
  if (!res.ok) {
    let message = "Something went wrong on the grader's side. Please try again.";
    let reasons: { message: string }[] = [];
    try {
      const detail = (await res.json())?.detail;
      if (typeof detail === "string") message = detail;
      else if (detail && typeof detail.message === "string") {
        message = detail.message;
        if (Array.isArray(detail.boxes)) reasons = detail.boxes;
      }
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, reasons);
  }
  return (await res.json()) as T;
}

const send = (method: string, body: unknown = {}): RequestInit => ({ method, body: JSON.stringify(body) });
const post = (body: unknown = {}) => send("POST", body);

export type ItemInput = { key: string; text: string; points: number | null; accept: string[]; reject: string[] };
export type ResponsesPatch = {
  response_ids?: string[];
  version_label?: string;
  only_unknown?: boolean;
  problem_id?: string;
  excluded?: boolean;
};

// Ids the owner chose (versions, items, responses) go in the request body, never in
// the path or the query string: they may contain any character, and a response id in
// a URL would end up in the server's access log. Bank and run ids are our own.
export const api = {
  health: () => call<Health>("/health"),
  examples: () => call<Example[]>("/examples"),

  listBanks: () => call<BankSummary[]>("/assessments"),
  uploadProblemInfo: (name: string, csv: string, responses_csv = "") =>
    call<Bank>("/assessments/problem-info", post({ name, csv, responses_csv })),
  loadExample: (problem_id: string, wording: string) =>
    call<Bank>("/assessments/example", post({ problem_id, wording })),
  getBank: (id: string) => call<Bank>(`/assessments/${id}`),
  deleteBank: (id: string) =>
    call<{ deleted: string; runs: number; cached_samples: number }>(`/assessments/${id}`, send("DELETE")),
  clearCache: () => call<{ deleted: number }>("/cache", send("DELETE")),
  problemInfoUrl: (id: string) => `${BASE}/assessments/${id}/problem-info.csv`,

  editVersion: (id: string, body: { problem_id: string; new_id?: string; text?: string; instructions?: string }) =>
    call<Bank>(`/assessments/${id}/version`, send("PUT", body)),
  addVersion: (id: string) => call<Bank & { added: string }>(`/assessments/${id}/version`, post()),
  removeVersion: (id: string, problem_id: string) =>
    call<Bank & { unassigned_responses: number }>(`/assessments/${id}/version`, send("DELETE", { problem_id })),
  setItems: (id: string, problem_id: string, items: ItemInput[]) =>
    call<Bank>(`/assessments/${id}/rubric`, send("PUT", { problem_id, items })),
  copyRubric: (id: string, problem_id: string, to?: string[]) =>
    call<Bank & { copied_to: string[] }>(`/assessments/${id}/rubric/copy`, post({ problem_id, to })),
  approve: (id: string) => call<Bank & { warnings: string[] }>(`/assessments/${id}/rubric/approve`, post()),
  unlock: (id: string) => call<Bank>(`/assessments/${id}/rubric/unlock`, post()),

  attachResponses: (id: string, csv: string) =>
    call<Bank & { findings: Finding[] }>(`/assessments/${id}/responses`, post({ csv })),
  useHeldResponses: (id: string) =>
    call<Bank & { findings: Finding[] }>(`/assessments/${id}/responses`, post({ use_held: true })),
  exampleResponses: (id: string) =>
    call<Bank & { findings: Finding[] }>(`/assessments/${id}/responses/example`, post()),
  patchResponses: (id: string, body: ResponsesPatch) =>
    call<Bank & { changed: number }>(`/assessments/${id}/responses`, send("PATCH", body)),

  startRun: (body: { assessment_id: string } & RunConfig) => call<Run>("/runs", post(body)),
  getRun: (id: string) => call<Run>(`/runs/${id}`),
  listRuns: (assessment_id: string) =>
    call<RunSummary[]>(`/runs?assessment_id=${encodeURIComponent(assessment_id)}`),
  getCell: (rid: string, instance_id: string) => call<Cell>(`/runs/${rid}/cell`, post({ instance_id })),
  override: (rid: string, instance_id: string, final: number | null) =>
    call<CellCompact>(`/runs/${rid}/override`, post({ instance_id, final })),
  cancel: (rid: string) => call<RunSummary>(`/runs/${rid}/cancel`, post()),
  metrics: (rid: string) => call<Metrics>(`/runs/${rid}/metrics`),
  predictionsUrl: (rid: string) => `${BASE}/runs/${rid}/predictions.csv`,
};

export function needsReview(cell: CellCompact, threshold: number, hardFlags: string[]): boolean {
  if (cell.override !== null) return false;
  if (cell.flags.some((f) => hardFlags.includes(f))) return true;
  return cell.confidence !== null && cell.confidence < threshold;
}
