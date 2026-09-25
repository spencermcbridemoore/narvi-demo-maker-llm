// Step 4: the students x items grid, the review queue (confidence slider + hard
// flags), the per-cell drawer with every vote, teacher overrides, and exports.
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import {
  api,
  instanceId,
  needsReview,
  type Assessment,
  type Cell,
  type CellCompact,
  type Criterion,
  type Metrics,
  type ResponseItem,
  type Run,
} from "./api";
import { download, toCsv } from "./csv";
import { errMsg, f2, fmtInt, pct } from "./util";

const FLAG_TEXT: Record<string, string> = {
  split: "the votes disagreed",
  tie: "tied votes (scored 0 until you decide)",
  error: "a model call failed",
  quote_missing: "a quoted phrase isn't in the response",
  no_evidence: "credit given without quoting anything",
  addresses_grader: "the response talks to the grader",
};

type Props = {
  assessment: Assessment;
  run: Run;
  idMap: Record<string, string>;
  hardFlags: string[];
  onRunChange: (r: Run) => void;
  onError: (m: string) => void;
};

export function Results({ assessment, run, idMap, hardFlags, onRunChange, onError }: Props) {
  const [threshold, setThreshold] = useState(0.8);
  const [onlyReview, setOnlyReview] = useState(false);
  const [compare, setCompare] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);

  const cells = useMemo(() => new Map(run.cells.map((c) => [c.instance_id, c])), [run.cells]);
  const hasRefs = Object.keys(assessment.references).length > 0;
  const displayId = useCallback(
    (r: ResponseItem) => idMap[r.external_id] ?? (r.external_id || r.id),
    [idMap],
  );

  // Row/column structure in display order, limited to what this run graded.
  const layout = useMemo(
    () =>
      assessment.problems
        .map((p) => {
          const crits = assessment.criteria.filter((c) => c.problem_id === p.id);
          const resps = assessment.responses.filter(
            (r) => r.problem_id === p.id && crits.some((c) => cells.has(instanceId(r, c))),
          );
          return { problem: p, crits, resps };
        })
        .filter((x) => x.resps.length > 0),
    [assessment, cells],
  );

  const queue = useMemo(() => {
    const out: string[] = [];
    for (const { crits, resps } of layout)
      for (const r of resps)
        for (const c of crits) {
          const cell = cells.get(instanceId(r, c));
          if (cell && needsReview(cell, threshold, hardFlags)) out.push(cell.instance_id);
        }
    return out;
  }, [layout, cells, threshold, hardFlags]);

  const refreshMetrics = useCallback(() => {
    if (hasRefs && run.status !== "running") api.metrics(run.id).then(setMetrics).catch(() => setMetrics(null));
  }, [hasRefs, run.id, run.status]);
  useEffect(() => refreshMetrics(), [refreshMetrics]);

  const next = useCallback(() => {
    const at = selected ? queue.indexOf(selected) : -1;
    setSelected(queue.find((_, i) => i > at) ?? queue[0] ?? null);
  }, [queue, selected]);

  const decide = useCallback(
    async (iid: string, final: number | null) => {
      try {
        const updated = await api.override(run.id, iid, final);
        onRunChange({ ...run, cells: run.cells.map((c) => (c.instance_id === iid ? updated : c)) });
        refreshMetrics();
      } catch (e) {
        onError(errMsg(e));
      }
    },
    [run, onRunChange, refreshMetrics, onError],
  );

  // Keyboard review: 1 = credit, 0 = no credit (both then advance), N = next, Esc = close.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (!selected || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "1" || e.key === "0") {
        decide(selected, Number(e.key)).then(next);
      } else if (e.key === "n" || e.key === "N") next();
      else if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, decide, next]);

  const graded = run.cells.filter((c) => c.final !== null);
  const credit = graded.filter((c) => c.final === 1).length;
  const overridden = run.cells.filter((c) => c.override !== null).length;

  const exportRows = (wide: boolean) => {
    const keys = [...new Set(assessment.criteria.map((c) => c.key))].sort(
      (a, b) => (Number(a) || 0) - (Number(b) || 0) || a.localeCompare(b),
    );
    if (wide) {
      const rows: (string | number | null)[][] = [
        ["student_id", "problem_id", ...keys.map((k) => `item_${k}`), "points", "max_points", "needs_review"],
      ];
      for (const { problem, crits, resps } of layout)
        for (const r of resps) {
          let points = 0;
          let max = 0;
          let review = 0;
          const byKey: Record<string, number | null> = {};
          for (const c of crits) {
            const cell = cells.get(instanceId(r, c));
            if (!cell) continue;
            byKey[c.key] = cell.final;
            max += c.points;
            if (cell.final === 1) points += c.points;
            if (needsReview(cell, threshold, hardFlags)) review++;
          }
          rows.push([displayId(r), problem.id, ...keys.map((k) => byKey[k] ?? ""), points, max, review]);
        }
      return rows;
    }
    const rows: (string | number | null)[][] = [
      ["student_id", "problem_id", "item", "final", "confidence", "votes", "flags", "decided_by", "needs_review"],
    ];
    for (const { problem, crits, resps } of layout)
      for (const r of resps)
        for (const c of crits) {
          const cell = cells.get(instanceId(r, c));
          if (!cell) continue;
          rows.push([
            displayId(r),
            problem.id,
            c.key,
            cell.final,
            cell.confidence,
            cell.votes,
            cell.flags.join(" "),
            cell.resolved_by,
            needsReview(cell, threshold, hardFlags) ? 1 : 0,
          ]);
        }
    return rows;
  };

  return (
    <div className={selected ? "g-results with-drawer" : "g-results"}>
      <div className="g-stack">
        <section className="g-card g-controls">
          <div className="g-row between">
            <div>
              <h2>Results</h2>
              <p className="g-muted">
                {run.model} · k={run.config.k} · effort {run.config.effort} · run {run.id} ({run.status}) ·{" "}
                {graded.length} graded, {credit} credit ({pct(graded.length ? credit / graded.length : null)}) · tokens{" "}
                {fmtInt(run.usage.input_tokens)} in / {fmtInt(run.usage.output_tokens)} out
              </p>
            </div>
            <button className="g-primary" disabled={!queue.length} onClick={next}>
              Review next ({queue.length}) →
            </button>
          </div>
          <div className="g-row">
            <label className="g-slider">
              Review items with vote agreement below <strong>{Math.round(threshold * 100)}%</strong>
              <input
                type="range"
                min={0.5}
                max={1}
                step={0.05}
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
              />
            </label>
            <span className="g-muted">
              {queue.length} to review · {overridden} decided by you · flagged items always included
            </span>
          </div>
          <div className="g-row">
            <label className="g-check">
              <input type="checkbox" checked={onlyReview} onChange={(e) => setOnlyReview(e.target.checked)} /> Only rows
              with something to review
            </label>
            {hasRefs && (
              <label className="g-check">
                <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Outline cells where
                both human graders disagree with the final grade
              </label>
            )}
          </div>
          <div className="g-row">
            <span className="g-muted">Export:</span>
            <button className="g-ghost small" onClick={() => download(`grades_${run.id}.csv`, toCsv(exportRows(true)))}>
              Grades (one row per student)
            </button>
            <button className="g-ghost small" onClick={() => download(`grades_detail_${run.id}.csv`, toCsv(exportRows(false)))}>
              Detail (one row per item)
            </button>
            <a className="g-ghost small" href={api.predictionsUrl(run.id)}>
              predictions.csv (benchmark format)
            </a>
            {Object.keys(idMap).length > 0 && (
              <button
                className="g-ghost small"
                onClick={() =>
                  download("id_key.csv", toCsv([["one_time_id", "student_id"], ...Object.entries(idMap)]))
                }
              >
                ID key
              </button>
            )}
          </div>
        </section>

        {metrics?.run && <MetricsPanel m={metrics} />}

        {layout.map(({ problem, crits, resps }) => {
          const rows = onlyReview
            ? resps.filter((r) =>
                crits.some((c) => {
                  const cell = cells.get(instanceId(r, c));
                  return cell ? needsReview(cell, threshold, hardFlags) : false;
                }),
              )
            : resps;
          return (
            <section className="g-card" key={problem.id}>
              <h3>
                Problem {problem.id} <span className="g-muted">· {rows.length} of {resps.length} students shown</span>
              </h3>
              <div className="g-table-wrap">
                <table className="g-grid">
                  <thead>
                    <tr>
                      <th>Student</th>
                      {crits.map((c) => (
                        <th key={c.id} title={c.text}>
                          {c.label}
                        </th>
                      ))}
                      <th>Points</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <GridRow
                        key={r.id}
                        response={r}
                        crits={crits}
                        cells={cells}
                        label={displayId(r)}
                        references={compare ? assessment.references : {}}
                        threshold={threshold}
                        hardFlags={hardFlags}
                        selected={selected}
                        onSelect={setSelected}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          );
        })}
      </div>

      {selected && cells.get(selected) && (
        <CellDrawer
          runId={run.id}
          compact={cells.get(selected)!}
          assessment={assessment}
          displayId={displayId}
          queueLength={queue.length}
          onDecide={decide}
          onNext={next}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

function GridRow({
  response,
  crits,
  cells,
  label,
  references,
  threshold,
  hardFlags,
  selected,
  onSelect,
}: {
  response: ResponseItem;
  crits: Criterion[];
  cells: Map<string, CellCompact>;
  label: string;
  references: Assessment["references"];
  threshold: number;
  hardFlags: string[];
  selected: string | null;
  onSelect: (iid: string) => void;
}) {
  let points = 0;
  let max = 0;
  const tds = crits.map((c) => {
    const iid = instanceId(response, c);
    const cell = cells.get(iid);
    if (!cell) return <td key={c.id} />;
    max += c.points;
    if (cell.final === 1) points += c.points;
    const ref = references[iid];
    const wrongVsBoth = !!ref && cell.final !== null && ref.ref_a === ref.ref_b && ref.ref_a !== cell.final;
    const classes = [
      "g-cell",
      cell.final === 1 ? "credit" : cell.final === 0 ? "nocredit" : "pending",
      needsReview(cell, threshold, hardFlags) ? "review" : "",
      cell.override !== null ? "overridden" : "",
      wrongVsBoth ? "wrong" : "",
      selected === iid ? "selected" : "",
    ].join(" ");
    const title = [
      `votes ${cell.votes || "–"}`,
      cell.flags.length ? `flags: ${cell.flags.join(", ")}` : "",
      cell.override !== null ? "decided by you" : "",
      ref ? `humans: A=${ref.ref_a} B=${ref.ref_b}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
    return (
      <td key={c.id}>
        <button className={classes} title={title} onClick={() => onSelect(iid)}>
          {cell.final ?? "…"}
        </button>
      </td>
    );
  });
  return (
    <tr>
      <td className="g-sid" title={response.text.slice(0, 400)}>
        {label}
      </td>
      {tds}
      <td className="g-total">
        {points}/{max}
      </td>
    </tr>
  );
}

function MetricsPanel({ m }: { m: Metrics }) {
  const r = m.run!;
  const h = m.human;
  const ceiling = Object.fromEntries((h?.problems ?? []).map((p) => [p.problem_id, p.human_exact_match]));
  return (
    <section className="g-card">
      <h3>Agreement with the two human graders</h3>
      <table className="g-table">
        <thead>
          <tr>
            <th>Problem</th>
            <th>Students</th>
            <th>All items match grader A</th>
            <th>…grader B</th>
            <th>A and B match each other</th>
            <th>Wrong vs both</th>
          </tr>
        </thead>
        <tbody>
          {r.problems.map((p) => (
            <tr key={p.problem_id}>
              <td>{p.problem_id}</td>
              <td>{p.n_responses}</td>
              <td>{pct(p.exact_match_ref_a)}</td>
              <td>{pct(p.exact_match_ref_b)}</td>
              <td>{pct(ceiling[p.problem_id])}</td>
              <td>{pct(p.wrong_vs_both)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="g-muted">
        Item level: accuracy {pct(r.overall.item_acc_ref_a)} vs A, {pct(r.overall.item_acc_ref_b)} vs B; macro-F1{" "}
        {f2(r.overall.macro_f1_ref_a)} / {f2(r.overall.macro_f1_ref_b)}. Humans vs each other: accuracy{" "}
        {pct(h?.overall.item_acc_ref_a)}, macro-F1 {f2(h?.overall.macro_f1_ref_a)}. Scored by the kit's evaluate.py on{" "}
        {r.n_covered}/{r.n_instances} items.
      </p>
      {r.confidence && (
        <p className="g-muted">
          Review value: {r.confidence.n_wrong} of {r.confidence.n_agreed_scored} items where the humans agree are graded
          wrong; reviewing the least-confident 10 / 15 / 20% catches {pct(r.confidence.wrong_caught_reviewing_10pct)} /{" "}
          {pct(r.confidence.wrong_caught_reviewing_15pct)} / {pct(r.confidence.wrong_caught_reviewing_20pct)} of them
          (AUROC {f2(r.confidence.auroc_flag_wrong)}).
        </p>
      )}
    </section>
  );
}

function CellDrawer({
  runId,
  compact,
  assessment,
  displayId,
  queueLength,
  onDecide,
  onNext,
  onClose,
}: {
  runId: string;
  compact: CellCompact;
  assessment: Assessment;
  displayId: (r: ResponseItem) => string;
  queueLength: number;
  onDecide: (iid: string, final: number | null) => Promise<void>;
  onNext: () => void;
  onClose: () => void;
}) {
  const [cell, setCell] = useState<Cell | null>(null);
  const iid = compact.instance_id;
  useEffect(() => {
    let live = true;
    api
      .getCell(runId, iid)
      .then((c) => live && setCell(c))
      .catch(() => live && setCell(null));
    return () => {
      live = false;
    };
  }, [runId, iid, compact.final, compact.override, compact.votes]);

  const response = assessment.responses.find((r) => r.id === compact.response_id);
  const criterion = assessment.criteria.find((c) => c.id === compact.criterion_id);
  if (!response || !criterion) return null;
  const ref = assessment.references[iid];
  const evidence = (cell?.samples ?? []).filter((s) => s.verdict === compact.final).flatMap((s) => s.evidence);

  return (
    <aside className="g-drawer">
      <div className="g-row between">
        <h3>
          {displayId(response)} · {criterion.label}
        </h3>
        <button className="g-ghost small" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
      <p className="g-verdict">
        Final:{" "}
        <strong className={compact.final === 1 ? "credit" : "nocredit"}>
          {compact.final === 1 ? "credit (1)" : compact.final === 0 ? "no credit (0)" : "pending"}
        </strong>{" "}
        · votes {compact.votes || "–"} · {compact.resolved_by === "teacher" ? "decided by you" : compact.resolved_by}
      </p>
      {compact.flags.length > 0 && (
        <ul className="g-flags">
          {compact.flags.map((f) => (
            <li key={f}>{FLAG_TEXT[f] ?? f}</li>
          ))}
        </ul>
      )}
      <div className="g-row">
        <button className="g-primary small" onClick={() => onDecide(iid, 1).then(onNext)}>
          Credit (1)
        </button>
        <button className="g-danger small" onClick={() => onDecide(iid, 0).then(onNext)}>
          No credit (0)
        </button>
        {compact.override !== null && (
          <button className="g-ghost small" onClick={() => onDecide(iid, null)}>
            Undo my decision
          </button>
        )}
        <button className="g-ghost small" onClick={onNext}>
          Next ({queueLength}) →
        </button>
      </div>
      <p className="g-hint">Keys: 1 credit · 0 no credit (both jump to the next) · N next · Esc close</p>
      {ref && (
        <p className="g-muted">
          Human graders: A = {ref.ref_a}, B = {ref.ref_b}
        </p>
      )}
      <h4>Rubric item</h4>
      <pre className="g-pre">{criterion.text}</pre>
      <h4>Response {evidence.length > 0 && <span className="g-hint">(highlighted: quotes the winning votes relied on)</span>}</h4>
      <div className="g-response">{highlight(response.text, evidence)}</div>
      <h4>Votes</h4>
      {!cell && <p className="g-muted">Loading…</p>}
      {cell?.samples.map((s, i) => (
        <div className="g-sample" key={i}>
          <div className="g-row">
            <span className={`g-chip ${s.verdict === 1 ? "credit" : s.verdict === 0 ? "nocredit" : "err"}`}>
              {s.verdict === 1 ? "credit" : s.verdict === 0 ? "no credit" : "failed"}
            </span>
            {s.cached && <span className="g-tag">cached</span>}
            {s.quotes_found === false && <span className="g-tag warn">quote not found in response</span>}
          </div>
          <p>{s.comparison || s.error}</p>
          {s.evidence.length > 0 && (
            <ul>
              {s.evidence.map((q, j) => (
                <li key={j}>“{q}”</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </aside>
  );
}

function highlight(text: string, quotes: string[]): ReactNode {
  const lower = text.toLowerCase();
  const ranges: [number, number][] = [];
  for (const q of quotes)
    for (const frag of q.split(/\.\.\.|…/)) {
      const f = frag.trim().replace(/^["'“”‘’]+|["'“”‘’.,;:]+$/g, "").toLowerCase();
      if (f.length < 4) continue;
      for (let i = lower.indexOf(f); i >= 0; i = lower.indexOf(f, i + f.length)) ranges.push([i, i + f.length]);
    }
  if (!ranges.length) return text;
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const [s, e] of ranges) {
    const last = merged[merged.length - 1];
    if (last && s <= last[1]) last[1] = Math.max(last[1], e);
    else merged.push([s, e]);
  }
  const parts: ReactNode[] = [];
  let pos = 0;
  merged.forEach(([s, e], i) => {
    if (s > pos) parts.push(text.slice(pos, s));
    parts.push(<mark key={i}>{text.slice(s, e)}</mark>);
    pos = e;
  });
  parts.push(text.slice(pos));
  return parts;
}
