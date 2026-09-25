// Typed client for the grader API (backend/app/grader/api.py, mounted at /grader/api).
import { BACKEND_URL } from "../config";

export type Problem = { id: string; text: string; instructions: string };
export type Criterion = {
  id: string;
  problem_id: string;
  key: string;
  label: string;
  text: string;
  source_text: string;
  points: number;
};
export type ResponseItem = { id: string; problem_id: string; text: string; external_id: string };
export type Assessment = {
  id: string;
  name: string;
  source: string;
  rubric_version: string;
  problems: Problem[];
  criteria: Criterion[];
  responses: ResponseItem[];
  references: Record<string, { ref_a: number; ref_b: number }>;
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
};
export type Run = RunSummary & { cells: CellCompact[] };

export type Health = {
  ok: boolean;
  profile: string;
  model: string;
  prompt_version: string;
  kit: boolean;
  efforts: string[];
  hard_flags: string[];
};
export type Example = { id: string; name: string; description: string; rubric_versions: string[] };

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

const BASE = `${BACKEND_URL}/grader/api`;

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return (await res.json()) as T;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => call<Health>("/health"),
  examples: () => call<Example[]>("/examples"),
  loadExample: (rubric_version: string) =>
    call<Assessment>("/assessments/example", post({ example: "chen", rubric_version })),
  loadCsv: (name: string, rubric_csv: string, responses_csv: string) =>
    call<Assessment>("/assessments/csv", post({ name, rubric_csv, responses_csv })),
  getAssessment: (id: string) => call<Assessment>(`/assessments/${id}`),
  editCriterion: (aid: string, criterion_id: string, text: string) =>
    call<Criterion>(`/assessments/${aid}/criteria`, {
      method: "PATCH",
      body: JSON.stringify({ criterion_id, text }),
    }),
  startRun: (body: { assessment_id: string } & RunConfig) => call<Run>("/runs", post(body)),
  getRun: (id: string) => call<Run>(`/runs/${id}`),
  listRuns: () => call<RunSummary[]>("/runs"),
  getCell: (rid: string, iid: string) =>
    call<Cell>(`/runs/${rid}/cell?instance_id=${encodeURIComponent(iid)}`),
  override: (rid: string, instance_id: string, final: number | null) =>
    call<CellCompact>(`/runs/${rid}/override`, post({ instance_id, final })),
  cancel: (rid: string) => call<RunSummary>(`/runs/${rid}/cancel`, post({})),
  metrics: (rid: string) => call<Metrics>(`/runs/${rid}/metrics`),
  predictionsUrl: (rid: string) => `${BASE}/runs/${rid}/predictions.csv`,
};

/** Mirrors backend model.instance_id(): "<response id>#<criterion key>". */
export const instanceId = (r: ResponseItem, c: Criterion) => `${r.id}#${c.key}`;

export function needsReview(cell: CellCompact, threshold: number, hardFlags: string[]): boolean {
  if (cell.override !== null) return false;
  if (cell.flags.some((f) => hardFlags.includes(f))) return true;
  return cell.confidence !== null && cell.confidence < threshold;
}
