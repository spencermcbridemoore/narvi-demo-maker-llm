// Step 5: the students x items grid grouped by version, the review queue (confidence
// slider + hard flags), the per-cell drawer with every vote, teacher overrides, and
// the grades export. A run is shown with the rubric it used (its own copy), and says
// so when the bank's rubric has been revised since.
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import {
  api,
  needsReview,
  type Bank,
  type Cell,
  type CellCompact,
  type Metrics,
  type ResponseItem,
  type RubricItem,
  type RubricVersion,
  type Run,
  type RunSummary,
} from "./api";
import { download, toCsv } from "./csv";
import { OriginMark } from "./Drafts";
import { ConfirmButton, RunProblem, SideBox, StepLayout, deleteBankQuestion, fmtDate, isLegacy } from "./parts";
import { displayId, errMsg, f2, fmtInt, pct } from "./util";

const FLAG_TEXT: Record<string, string> = {
  split: "the votes disagreed",
  tie: "tied votes (scored 0 until you decide)",
  error: "a model call failed",
  quote_missing: "a quoted phrase isn't in the response",
  no_evidence: "credit given without quoting anything",
  addresses_grader: "the response talks to the grader",
};

/** Mirrors backend model.instance_id(): "<response id>#<item key>". */
const instanceId = (r: ResponseItem, c: RubricItem) => `${r.id}#${c.key}`;
/** Points add up as fractions; show and export them without floating-point dust. */
const tidy = (points: number) => Number(points.toFixed(6));

type Props = {
  bank: Bank;
  run: Run;
  runs: RunSummary[]; // every run of this bank, newest first
  idMap: Record<string, string>;
  hardFlags: string[];
  onRunChange: (r: Run) => void;
  onOpenRun: (id: string) => void;
  onDeleted: (summary: string) => void;
  onError: (m: string) => void;
};

export function Results({ bank, run, runs, idMap, hardFlags, onRunChange, onOpenRun, onDeleted, onError }: Props) {
  const [threshold, setThreshold] = useState(0.8);
  const [onlyReview, setOnlyReview] = useState(false);
  const [compare, setCompare] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);

  const legacy = isLegacy(bank);
  const cells = useMemo(() => new Map(run.cells.map((c) => [c.instance_id, c])), [run.cells]);
  const hasRefs = Object.keys(bank.references).length > 0;
  const name = useCallback((r: ResponseItem) => displayId(idMap, r), [idMap]);

  // The versions and items this run graded with: its own copy, or (for a run saved
  // before copies were kept) the bank as it is now.
  const recorded = !!run.rubric_copy;
  const versions: RubricVersion[] = useMemo(
    () =>
      run.rubric_copy?.versions ??
      bank.problems.map((p) => ({
        id: p.id,
        text: p.text,
        instructions: p.instructions,
        items: bank.criteria.filter((c) => c.problem_id === p.id),
      })),
    [run.rubric_copy, bank.problems, bank.criteria],
  );
  const revised = recorded && !legacy && (bank.rubric_status !== "approved" || bank.rubric_hash !== run.rubric_hash);

  // A grade belongs to a response only while that response still has both the id and
  // the text that was graded. The bank's responses can be replaced, and one-time ids
  // start again at S001 with every upload, so the id alone could put one student's
  // grades next to another student's answer.
  const gradedText = run.response_fingerprints ?? null;
  const asGraded = useCallback(
    (r: ResponseItem) => !gradedText || gradedText[r.id] === r.fingerprint,
    [gradedText],
  );

  // Rows and columns in display order. A response sits under the version it was
  // graded with, whatever version it has now.
  const layout = useMemo(
    () =>
      versions
        .map((version) => ({
          version,
          crits: version.items,
          resps: bank.responses.filter(
            (r) => asGraded(r) && version.items.some((c) => cells.get(instanceId(r, c))?.criterion_id === c.id),
          ),
        }))
        .filter((x) => x.resps.length > 0),
    [versions, bank.responses, cells, asGraded],
  );
  // The grades on screen. Everything counted and exported below is taken from these.
  const visible = useMemo(
    () =>
      layout.flatMap(({ crits, resps }) =>
        resps.flatMap((r) => crits.map((c) => cells.get(instanceId(r, c))).filter((c): c is CellCompact => !!c)),
      ),
    [layout, cells],
  );
  const unmatched = run.cells.length - visible.length;
  const ungraded = run.status === "running" ? 0 : visible.filter((c) => c.final === null).length;

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
    if (compare && hasRefs && run.status !== "running")
      api.metrics(run.id).then(setMetrics).catch(() => setMetrics(null));
  }, [compare, hasRefs, run.id, run.status]);
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

  const graded = visible.filter((c) => c.final !== null);
  const credit = graded.filter((c) => c.final === 1).length;
  const overridden = visible.filter((c) => c.override !== null).length;

  const exportRows = (wide: boolean) => {
    const keys = [...new Set(versions.flatMap((v) => v.items.map((c) => c.key)))].sort(
      (a, b) => (Number(a) || 0) - (Number(b) || 0) || a.localeCompare(b),
    );
    if (wide) {
      const rows: (string | number | null)[][] = [
        ["student_id", "problem_id", ...keys.map((k) => `item_${k}`), "points", "max_points", "needs_review", "not_graded"],
      ];
      for (const { version, crits, resps } of layout)
        for (const r of resps) {
          let points = 0;
          let max = 0;
          let review = 0;
          let missing = 0; // items of a run that stopped early: no grade, so the points are not final
          const byKey: Record<string, number | null> = {};
          for (const c of crits) {
            const cell = cells.get(instanceId(r, c));
            if (!cell) continue;
            byKey[c.key] = cell.final;
            max += c.points;
            if (cell.final === 1) points += c.points;
            if (cell.final === null) missing++;
            if (needsReview(cell, threshold, hardFlags)) review++;
          }
          rows.push([name(r), version.id, ...keys.map((k) => byKey[k] ?? ""), tidy(points), tidy(max), review, missing]);
        }
      return rows;
    }
    const rows: (string | number | null)[][] = [
      ["student_id", "problem_id", "item", "final", "confidence", "votes", "flags", "decided_by", "needs_review"],
    ];
    for (const { version, crits, resps } of layout)
      for (const r of resps)
        for (const c of crits) {
          const cell = cells.get(instanceId(r, c));
          if (!cell) continue;
          rows.push([
            name(r),
            version.id,
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

  const removeBank = async () => {
    try {
      const gone = await api.deleteBank(bank.id);
      onDeleted(
        `Deleted "${bank.name}": the bank, ${gone.runs} run(s) and ${gone.cached_samples} cached model answer(s).`,
      );
    } catch (e) {
      onError(errMsg(e));
    }
  };

  const side = (
    <SideBox title="More">
      {hasRefs && (
        <>
          <h4>Compare with the human graders</h4>
          <label className="g-check">
            <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Show how these
            grades agree with the two human graders
          </label>
          <p className="g-hint">Example data only. Cells where both humans disagree with the final grade get a red dashed outline.</p>
        </>
      )}

      <h4>Other exports</h4>
      <div className="g-row">
        <button className="g-ghost small" onClick={() => download(`grades_detail_${run.id}.csv`, toCsv(exportRows(false)))}>
          Detail (one row per item)
        </button>
        <a className="g-ghost small" href={api.predictionsUrl(run.id)}>
          predictions.csv (benchmark format)
        </a>
        {Object.keys(idMap).length > 0 && (
          <button
            className="g-ghost small"
            onClick={() => download("id_key.csv", toCsv([["one_time_id", "student_id"], ...Object.entries(idMap)]))}
          >
            ID key
          </button>
        )}
      </div>

      <h4>This run</h4>
      <p className="g-hint">
        {run.model} · {run.config.k} vote(s) per item · effort {run.config.effort} · {run.status} ·{" "}
        {recorded ? `rubric revision ${run.rubric_revision}` : "rubric not recorded"} · started {fmtDate(run.started)} ·
        tokens {fmtInt(run.usage.input_tokens)} in / {fmtInt(run.usage.output_tokens)} out · run {run.id}
      </p>
      {runs.length > 1 && (
        <>
          <h4>Runs of this bank</h4>
          <ul className="g-runs">
            {runs.map((r) => (
              <li key={r.id}>
                <span>
                  {fmtDate(r.started)} · {r.status}
                  {r.rubric_revision ? ` · revision ${r.rubric_revision}` : ""}
                </span>
                {r.id === run.id ? (
                  <span className="g-hint">shown</span>
                ) : (
                  <button className="g-ghost small" onClick={() => onOpenRun(r.id)}>
                    Open
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      <h4>Delete this bank's data</h4>
      <ConfirmButton
        label="Delete this problem bank"
        question={deleteBankQuestion(bank.name, legacy ? "legacy" : "bank")}
        confirm="Delete it"
        disabled={run.status === "running"}
        onConfirm={removeBank}
      />
    </SideBox>
  );

  return (
    <div className={selected ? "g-results with-drawer" : "g-results"}>
      <StepLayout side={side}>
        <section className="g-card g-controls">
          <div className="g-row between">
            <div>
              <h2>Review &amp; export</h2>
              <p className="g-muted">
                {graded.length} grades, {credit} with credit ({pct(graded.length ? credit / graded.length : null)}) ·{" "}
                {queue.length} to review · {overridden} decided by you
              </p>
            </div>
            <button className="g-primary" disabled={!queue.length} onClick={next}>
              Review next ({queue.length}) →
            </button>
          </div>

          {!recorded && (
            <p className="g-callout">
              Rubric not recorded: this run was saved before runs kept a copy of their rubric. The wording shown is the
              bank's current one.
            </p>
          )}
          {revised && (
            <p className="g-callout">
              The rubric has been revised since this run. These grades used revision {run.rubric_revision}; the rubric is
              now {bank.rubric_status === "approved" ? `at revision ${bank.rubric_revision}` : "being edited"}. Grade again
              to use the new wording.
            </p>
          )}
          {unmatched > 0 && (
            <p className="g-callout">
              {unmatched} of this run's {run.cells.length} grades are for responses that are no longer attached, or
              whose text has changed since. They are not shown: they can no longer be matched to a student.
            </p>
          )}
          {ungraded > 0 && (
            <p className="g-callout">
              This run stopped before it finished: {ungraded} of the {visible.length} grades shown are missing. Start
              grading again to complete them; votes already given are kept. In the export, "not_graded" counts the
              missing items of each student.
            </p>
          )}
          <RunProblem run={run} />

          <div className="g-row">
            <label className="g-slider">
              <span>
                Review items with vote agreement below <strong>{Math.round(threshold * 100)}%</strong>
              </span>
              <input
                type="range"
                min={0.5}
                max={1}
                step={0.05}
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
              />
            </label>
            <label className="g-check">
              <input type="checkbox" checked={onlyReview} onChange={(e) => setOnlyReview(e.target.checked)} /> Only rows
              with something to review
            </label>
          </div>
          <div className="g-row">
            <button className="g-ghost" onClick={() => download(`grades_${run.id}.csv`, toCsv(exportRows(true)))}>
              Export grades (CSV)
            </button>
            <span className="g-hint">
              One row per student: 1 or 0 for each item, points, and how many items still need review. Flagged items are
              always in the review queue.
            </span>
          </div>
        </section>

        {compare && metrics?.run && <MetricsPanel m={metrics} />}

        {layout.map(({ version, crits, resps }) => {
          const rows = onlyReview
            ? resps.filter((r) =>
                crits.some((c) => {
                  const cell = cells.get(instanceId(r, c));
                  return cell ? needsReview(cell, threshold, hardFlags) : false;
                }),
              )
            : resps;
          return (
            <section className="g-card" key={version.id}>
              <h3>
                {legacy ? "Problem" : "Version"} {version.id}{" "}
                <span className="g-muted">
                  · {rows.length} of {resps.length} students shown
                </span>
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
                        label={name(r)}
                        references={compare ? bank.references : {}}
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
        {layout.length === 0 && (
          <section className="g-card">
            <p className="g-muted">
              {run.cells.length
                ? "None of the responses this run graded are attached to the bank any more."
                : "This run has no grades."}
            </p>
          </section>
        )}
      </StepLayout>

      {selected && cells.get(selected) && (
        <CellDrawer
          runId={run.id}
          compact={cells.get(selected)!}
          bank={bank}
          versions={versions}
          recorded={recorded}
          revised={revised}
          name={name}
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
  crits: RubricItem[];
  cells: Map<string, CellCompact>;
  label: string;
  references: Bank["references"];
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
        <button className={classes} title={title} aria-label={`${label}, ${c.label}: ${cell.final ?? "pending"}`} onClick={() => onSelect(iid)}>
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
        {tidy(points)}/{tidy(max)}
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
        {f2(r.overall.macro_f1_ref_a)} / {f2(r.overall.macro_f1_ref_b)}. Humans vs each other, over the whole example:
        accuracy {pct(h?.overall.item_acc_ref_a)}, macro-F1 {f2(h?.overall.macro_f1_ref_a)}. Scored by the kit's
        evaluate.py on {r.n_covered} of the example's {r.n_instances} items.
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
  bank,
  versions,
  recorded,
  revised,
  name,
  queueLength,
  onDecide,
  onNext,
  onClose,
}: {
  runId: string;
  compact: CellCompact;
  bank: Bank;
  versions: RubricVersion[];
  recorded: boolean;
  revised: boolean;
  name: (r: ResponseItem) => string;
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

  const response = bank.responses.find((r) => r.id === compact.response_id);
  const criterion = versions.flatMap((v) => v.items).find((c) => c.id === compact.criterion_id);
  if (!response || !criterion) return null;
  const ref = bank.references[iid];
  const evidence = (cell?.samples ?? []).filter((s) => s.verdict === compact.final).flatMap((s) => s.evidence);
  const current = bank.criteria.find((c) => c.id === criterion.id);

  return (
    <aside className="g-drawer">
      <div className="g-row between">
        <h3>
          {name(response)} · {criterion.label}
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
      <h4>
        Rubric item {recorded ? "as this run used it" : ""} <OriginMark origin={criterion.origin} />
      </h4>
      <pre className="g-pre">{criterion.text}</pre>
      {(criterion.accept?.length ?? 0) > 0 && (
        <>
          <p className="g-hint">Acceptable forms</p>
          <ul className="g-details-list">{criterion.accept!.map((a) => <li key={a}>{a}</li>)}</ul>
        </>
      )}
      {(criterion.reject?.length ?? 0) > 0 && (
        <>
          <p className="g-hint">Does not count</p>
          <ul className="g-details-list">{criterion.reject!.map((a) => <li key={a}>{a}</li>)}</ul>
        </>
      )}
      {!recorded && <p className="g-hint">Rubric not recorded for this run. This is the bank's current wording.</p>}
      {revised && (
        <p className="g-hint">
          The rubric has been revised since this run.
          {current && current.text.trim() !== criterion.text.trim()
            ? " This item reads differently now."
            : current
              ? " This item's wording is unchanged."
              : " Its version or this item has been renamed or removed."}
        </p>
      )}
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
          <p>
            {s.comparison ||
              (s.verdict === null ? "This vote failed: the model gave no answer. Grade again to retry it." : "")}
          </p>
          {s.error && (
            <details>
              <summary>Details</summary>
              <pre className="g-pre">{s.error}</pre>
            </details>
          )}
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
