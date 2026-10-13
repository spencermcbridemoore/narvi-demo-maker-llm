// Step 2: the rubric, as boxes. One panel per version: its problem text, each item,
// each weight. Boxes that code filled from the file carry a "filled in for you" mark
// and stay editable; empty boxes that are needed are highlighted. The server's live
// check says what is still missing; Approve & lock stays off until nothing is.
// An AI draft shows in the panel of its version, marked as not part of the rubric
// yet, until it is accepted or discarded item by item (Drafts.tsx).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError, type Bank, type Box, type DraftRequest, type Finding, type Proposal } from "./api";
import {
  DraftingStatus,
  Lines,
  LinesEditor,
  OriginMark,
  ProposalHead,
  ProposedItemCard,
  ProposedText,
  acceptTogether,
} from "./Drafts";
import { ConfirmButton, FilledMark, FindingList, SideBox, StepLayout, fmtDate, isLegacy } from "./parts";
import { errMsg } from "./util";

type ItemDraft = { uid: string; key: string; text: string; weight: string; accept: string[]; reject: string[] };
type VersionDraft = {
  uid: string;
  id: string; // the id the server knows
  idBox: string; // what is typed in the id box
  text: string;
  instructions: string;
  items: ItemDraft[];
};

let lastUid = 0;
const uid = () => `u${++lastUid}`;

const parseWeight = (text: string): number | null => {
  const t = text.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
};
const validWeight = (text: string) => (parseWeight(text) ?? 0) > 0;

function toDrafts(bank: Bank): VersionDraft[] {
  return bank.problems.map((p) => ({
    uid: uid(),
    id: p.id,
    idBox: p.id,
    text: p.text,
    instructions: p.instructions,
    items: bank.criteria
      .filter((c) => c.problem_id === p.id)
      .map((c) => ({
        uid: uid(),
        key: c.key,
        text: c.text,
        weight: c.points === 0 ? "" : String(c.points),
        accept: c.accept,
        reject: c.reject,
      })),
  }));
}

/** The DOM id of a box, so a blocked button can take the owner straight to it. */
const boxId = (box: string, problemId = "", itemKey = "") =>
  `gbox:${box}:${encodeURIComponent(problemId)}:${encodeURIComponent(itemKey)}`;

function goToBox(b: { box: string; problem_id?: string; item_key?: string } | undefined) {
  if (!b) return;
  const el =
    document.getElementById(boxId(b.box, b.problem_id, b.item_key)) ??
    document.getElementById(boxId("items", b.problem_id));
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth; // restart the animation
  el.classList.add("flash");
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) el.focus({ preventScroll: true });
}

const sameValue = (a: string, b: string) => {
  const [x, y] = [a.trim(), b.trim()];
  return x === y || (x !== "" && y !== "" && Number(x) === Number(y));
};

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const rows = (text: string, min = 2) => Math.min(14, Math.max(min, text.split("\n").length + 1));

type Props = {
  bank: Bank;
  runCount: number; // runs this bank already has
  onBank: (b: Bank) => void;
  onError: (m: string) => void;
  onNotice: (m: string | null) => void;
  onNext: () => void;
};

export function RubricStep({ bank, runCount, onBank, onError, onNotice, onNext }: Props) {
  const legacy = isLegacy(bank);
  const editable = bank.check.editable;
  const [drafts, setDrafts] = useState<VersionDraft[]>(() => toDrafts(bank));
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [idError, setIdError] = useState<Record<string, string>>({});
  const [copyFrom, setCopyFrom] = useState("");
  const [removeId, setRemoveId] = useState("");
  // Drafting with AI (side box): the versions chosen there, and the pasted material.
  const [exampleId, setExampleId] = useState("");
  const [elaborateId, setElaborateId] = useState("");
  const [materialId, setMaterialId] = useState("");
  const [material, setMaterial] = useState("");

  // The latest boxes and bank, readable from saves that run after this render.
  const draftsRef = useRef(drafts);
  const bankId = bank.id;
  const dirtyFields = useRef(new Set<string>());
  const dirtyItems = useRef(new Set<string>());
  const queue = useRef<Promise<void>>(Promise.resolve());
  const timer = useRef<number | undefined>(undefined);
  const report = useCallback((e: unknown) => onError(errMsg(e)), [onError]);

  const update = (fn: (ds: VersionDraft[]) => VersionDraft[]) => {
    draftsRef.current = fn(draftsRef.current);
    setDrafts(draftsRef.current);
  };
  /** Take the boxes from the server's copy again (after locking, unlocking, copying ...). */
  const resync = (b: Bank) => {
    dirtyFields.current.clear();
    dirtyItems.current.clear();
    draftsRef.current = toDrafts(b);
    setDrafts(draftsRef.current);
  };

  // Every change goes to the server one at a time, in order.
  const enqueue = useCallback((task: () => Promise<void>) => {
    const next = queue.current.then(task);
    queue.current = next.catch(() => undefined);
    return next;
  }, []);

  const saveVersion = useCallback(
    (vuid: string) =>
      enqueue(async () => {
        const v = draftsRef.current.find((d) => d.uid === vuid);
        if (!v) return;
        const fields = dirtyFields.current.delete(vuid);
        const items = dirtyItems.current.delete(vuid);
        try {
          let latest: Bank | null = null;
          if (fields)
            latest = await api.editVersion(bankId, { problem_id: v.id, text: v.text, instructions: v.instructions });
          if (items)
            latest = await api.setItems(
              bankId,
              v.id,
              v.items.map((i) => ({
                key: i.key,
                text: i.text,
                points: parseWeight(i.weight),
                // A line that was added and left empty is not part of the item.
                accept: i.accept.filter((line) => line.trim() !== ""),
                reject: i.reject.filter((line) => line.trim() !== ""),
              })),
            );
          if (latest) onBank(latest);
        } catch (e) {
          if (fields) dirtyFields.current.add(vuid); // keep it for the next save
          if (items) dirtyItems.current.add(vuid);
          throw e;
        }
      }),
    [bankId, enqueue, onBank],
  );

  const flush = useCallback(async () => {
    window.clearTimeout(timer.current);
    const pending = new Set([...dirtyFields.current, ...dirtyItems.current]);
    if (pending.size) setSaving(true);
    for (const vuid of pending) saveVersion(vuid).catch(report);
    await queue.current;
    setSaving(false);
  }, [saveVersion, report]);

  const touch = (vuid: string, what: "fields" | "items") => {
    (what === "fields" ? dirtyFields : dirtyItems).current.add(vuid);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void flush(), 600);
  };
  // Leaving the step saves whatever is still pending.
  const flushRef = useRef(flush);
  flushRef.current = flush;
  useEffect(() => () => void flushRef.current(), []);

  const editVersion = (vuid: string, patch: Partial<VersionDraft>) => {
    update((ds) => ds.map((d) => (d.uid === vuid ? { ...d, ...patch } : d)));
    touch(vuid, "fields");
  };
  const editItems = (vuid: string, fn: (items: ItemDraft[]) => ItemDraft[]) => {
    update((ds) => ds.map((d) => (d.uid === vuid ? { ...d, items: fn(d.items) } : d)));
    touch(vuid, "items");
  };
  const addItem = (v: VersionDraft) => {
    const taken = new Set(v.items.map((i) => i.key));
    let n = v.items.length + 1;
    while (taken.has(String(n))) n++;
    editItems(v.uid, (items) => [
      ...items,
      { uid: uid(), key: String(n), text: "", weight: "1", accept: [], reject: [] },
    ]);
    window.setTimeout(() => document.getElementById(boxId("item_text", v.id, String(n)))?.focus(), 0);
  };

  const rename = (vuid: string) =>
    enqueue(async () => {
      const v = draftsRef.current.find((d) => d.uid === vuid);
      if (!v) return;
      const wanted = v.idBox.trim();
      const restore = () => update((ds) => ds.map((d) => (d.uid === vuid ? { ...d, idBox: d.id } : d)));
      if (wanted === v.id) return restore();
      if (!wanted) {
        setIdError((e) => ({ ...e, [vuid]: "A version needs a name." }));
        return restore();
      }
      try {
        const latest = await api.editVersion(bankId, { problem_id: v.id, new_id: wanted });
        update((ds) => ds.map((d) => (d.uid === vuid ? { ...d, id: wanted, idBox: wanted } : d)));
        setIdError((e) => ({ ...e, [vuid]: "" }));
        onBank(latest);
      } catch (e) {
        // The rename may have reached the server although its answer did not come back.
        const now = await api.getBank(bankId).catch(() => null);
        if (now && now.problems.some((p) => p.id === wanted) && !now.problems.some((p) => p.id === v.id)) {
          update((ds) => ds.map((d) => (d.uid === vuid ? { ...d, id: wanted, idBox: wanted } : d)));
          setIdError((x) => ({ ...x, [vuid]: "" }));
          onBank(now);
          return;
        }
        setIdError((x) => ({ ...x, [vuid]: errMsg(e) }));
        restore();
      }
    });
  /** The id the server knows a version by right now (a rename may have just gone through). */
  const idOf = (vuid: string) => draftsRef.current.find((d) => d.uid === vuid)?.id ?? "";

  /**
   * A change that reshapes the bank: save what is pending, apply it, take the result.
   * A refusal that names a box takes the owner to it; `say` also shows its sentence.
   */
  const reshape = async (change: () => Promise<Bank>, say = false) => {
    setBusy(true);
    try {
      await flush();
      // A save that failed has said so. Stop here: approving, copying or reloading the
      // boxes now would work from the older text and drop what was typed.
      if (dirtyFields.current.size || dirtyItems.current.size) return;
      const latest = await change();
      onBank(latest);
      resync(latest);
    } catch (e) {
      const boxes = e instanceof ApiError ? e.reasons : [];
      if (boxes.length) goToBox(boxes[0] as Box);
      if (say || !boxes.length) report(e);
    } finally {
      setBusy(false);
    }
  };

  // --- AI drafts ---------------------------------------------------------------
  const drafting = bank.drafting?.running ?? false;
  // While the AI is drafting, ask how far it is. The question goes through the same
  // queue as the saves, so an older answer never replaces a newer one.
  useEffect(() => {
    if (!drafting) return;
    const poll = window.setInterval(() => {
      enqueue(async () => onBank(await api.getBank(bankId))).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(poll);
  }, [drafting, bankId, enqueue, onBank]);

  /** Ask the AI for a draft. True when it has started. */
  const draftNow = async (body: DraftRequest): Promise<boolean> => {
    let started = false;
    await reshape(async () => {
      const latest = await api.startDraft(bankId, body);
      started = true;
      return latest;
    }, true);
    return started;
  };
  const accept = (p: Proposal, keys: string[], text = false) =>
    reshape(async () => {
      const latest = await api.acceptProposal(bankId, p.id, keys, text);
      const left = latest.left.length + (latest.text_left ? 1 : 0);
      onNotice(
        left
          ? `${plural(left, "inferred part was", "inferred parts were")} left waiting. Nothing of yours backs ${left === 1 ? "it" : "them"}, so ${left === 1 ? "it is" : "each one is"} accepted or discarded on its own.`
          : null,
      );
      return latest;
    }, true);
  const discard = (p: Proposal, keys?: string[], text = false) =>
    reshape(() => api.discardProposal(bankId, p.id, keys, text), true);
  const again = (p: Proposal) =>
    draftNow({
      mode: p.mode,
      problem_ids: [p.problem_id],
      ...(p.mode === "example" ? { example_id: p.example_id } : {}),
    });

  // --- what the screen shows -------------------------------------------------
  const toFill = bank.check.to_fill;
  const toDecide = bank.check.to_decide;
  const canApprove = editable && toFill.length === 0 && toDecide.length === 0 && !drafting;
  const more = (n: number, one: string, many: string) => (n > 1 ? ` And ${plural(n - 1, one, many)}.` : "");
  const notYet = drafting
    ? "the AI is still drafting."
    : toFill.length
      ? toFill[0].message + more(toFill.length, "more box", "more boxes")
      : toDecide.length
        ? toDecide[0].message + more(toDecide.length, "more AI draft", "more AI drafts")
        : "";
  const withRubric = drafts.filter((d) => d.items.length > 0);
  const withoutRubric = drafts.filter((d) => d.items.length === 0);

  // What the AI drafted (by version), what it could draft now, and what is saved for each item.
  const ai = bank.ai;
  const proposals = new Map(bank.proposals.map((p) => [p.problem_id, p]));
  const saved = new Map(bank.criteria.map((c) => [`${c.problem_id}|${c.key}`, c]));
  const undrafted = (ids: string[]) => ids.filter((id) => !proposals.has(id));
  // The version the others are drafted from: the first with items, unless another is chosen.
  const example = withRubric.some((v) => v.id === exampleId) ? exampleId : ai.example;
  const missing = example ? undrafted(ai.from_example) : [];
  const carry =
    example === ai.example
      ? undrafted(ai.carry_over)
      : undrafted(withRubric.filter((v) => v.id !== example).map((v) => v.id));
  const byHand = bank.check.without_rubric.filter((id) => !missing.includes(id) && !proposals.has(id));
  const elaborateTarget = ai.elaborate.includes(elaborateId) ? elaborateId : (ai.elaborate[0] ?? "");
  const materialTarget = ai.from_material.includes(materialId) ? materialId : (ai.from_material[0] ?? "");
  const listed = (ids: string[]) => `${ids.length === 1 ? "Version" : "Versions"} ${ids.join(", ")}`;
  const rubricFindings = useMemo(() => bank.findings.filter((f) => f.scope === "rubric"), [bank.findings]);

  // "Filled in for you" marks: only before the first approval, and only while the box
  // still holds what code put there.
  const showMarks = editable && bank.rubric_revision === 0;
  const marks = useMemo(() => {
    const m = new Map<string, Finding>();
    if (showMarks)
      for (const f of rubricFindings)
        if (f.filled === "code" && f.box !== "file") m.set(`${f.box}|${f.problem_id}|${f.item_key}`, f);
    return m;
  }, [rubricFindings, showMarks]);
  const mark = (box: string, v: VersionDraft, key = "", current?: string) => {
    const f = marks.get(`${box}|${v.id}|${key}`);
    return f && (f.value === "" || current === undefined || sameValue(f.value, current)) ? f : undefined;
  };
  // A problem text taken from pasted material carries its own mark, not "filled in for you".
  const shownMarks = drafts.flatMap((v) => [
    mark("version_id", v, "", v.idBox),
    mark("version_text", v, "", v.text),
    mark("version_instructions", v, "", v.instructions),
    ...v.items.flatMap((i) => [mark("item_key", v, i.key, i.key), mark("item", v, i.key), mark("weight", v, i.key, i.weight)]),
  ]).filter((f): f is Finding => !!f && !f.code.startsWith("ai_"));
  const fileNotes = showMarks ? rubricFindings.filter((f) => f.filled === "code" && f.box === "file") : [];

  const copySource = withRubric.find((v) => v.uid === copyFrom) ?? withRubric[0];
  const removeTarget = drafts.find((v) => v.uid === removeId) ?? drafts[drafts.length - 1];
  const responsesOf = (id: string) => bank.responses.filter((r) => r.problem_id === id).length;

  const side = (
    <SideBox title="More options">
      {editable && (
        <>
          <h4>Copy a rubric to versions without one</h4>
          {withRubric.length > 0 && withoutRubric.length > 0 ? (
            <div className="g-row">
              <select value={copySource.uid} onChange={(e) => setCopyFrom(e.target.value)} aria-label="Version to copy from">
                {withRubric.map((v) => (
                  <option key={v.uid} value={v.uid}>
                    {v.id}
                  </option>
                ))}
              </select>
              <button
                className="g-ghost small"
                disabled={busy}
                onClick={() => reshape(() => api.copyRubric(bankId, idOf(copySource.uid)))}
              >
                Copy this rubric to {withoutRubric.map((v) => v.id).join(", ")}
              </button>
            </div>
          ) : (
            <p className="g-hint">
              {withRubric.length === 0
                ? "No version has rubric items to copy yet."
                : "Every version already has a rubric."}
            </p>
          )}
          <p className="g-hint">The items are copied unchanged and marked "copied", so you can adapt them. No AI is involved.</p>

          <h4>Versions</h4>
          <div className="g-row">
            <button className="g-ghost small" disabled={busy} onClick={() => reshape(() => api.addVersion(bankId))}>
              Add a version
            </button>
          </div>
          {drafts.length > 1 && (
            <div className="g-row">
              <select value={removeTarget.uid} onChange={(e) => setRemoveId(e.target.value)} aria-label="Version to remove">
                {drafts.map((v) => (
                  <option key={v.uid} value={v.uid}>
                    {v.id}
                  </option>
                ))}
              </select>
              <ConfirmButton
                label="Remove this version"
                question={
                  <>
                    Remove version <strong>{removeTarget.id}</strong> with its problem text and rubric items? Responses
                    matched to it are kept and will need a version again.
                  </>
                }
                confirm="Remove it"
                disabled={busy}
                onConfirm={() => {
                  const target = removeTarget.uid;
                  setRemoveId("");
                  return reshape(() => api.removeVersion(bankId, idOf(target)));
                }}
              />
            </div>
          )}
        </>
      )}

      <h4>Download</h4>
      <div className="g-row">
        <button
          className="g-ghost small"
          onClick={async () => {
            await flush();
            window.location.assign(api.problemInfoUrl(bankId));
          }}
        >
          Download as Problem Info CSV
        </button>
      </div>
      {toDecide.length > 0 && (
        <p className="g-hint">AI drafts you have not accepted are not part of the rubric, so they are not in the file.</p>
      )}

      {editable && (
        <>
          <h4>Let the AI fill gaps</h4>
          <label className="g-check">
            <input
              type="checkbox"
              checked={bank.ai_fill}
              disabled={busy}
              onChange={(e) => {
                const on = e.target.checked;
                void reshape(() => api.setAiFill(bankId, on), true);
              }}
            />
            Let the AI draft the empty boxes
          </label>
          <p className="g-hint">
            Off, the AI drafts only when you ask it to. On, it drafts every version that has no rubric as soon as you
            switch this on: from a version that has one, or else from the problem text alone. Either way, nothing it
            writes is used until you accept it.
          </p>

          <h4>Draft with AI</h4>
          {withRubric.length > 1 && (
            <label className="g-field">
              Version to draft from
              <select value={example} onChange={(e) => setExampleId(e.target.value)}>
                {withRubric.map((v) => (
                  <option key={v.uid} value={v.id}>
                    {v.id}
                  </option>
                ))}
              </select>
            </label>
          )}
          {example && (missing.length > 0 || carry.length > 0) && (
            <div className="g-row">
              {missing.length > 0 && (
                <button
                  className="g-ghost small"
                  disabled={busy || drafting}
                  onClick={() => draftNow({ mode: "example", example_id: example, problem_ids: missing })}
                >
                  Draft {missing.join(", ")} from {example}
                </button>
              )}
              {carry.length > 0 && (
                <button
                  className="g-ghost small"
                  disabled={busy || drafting}
                  onClick={() => draftNow({ mode: "example", example_id: example, problem_ids: carry })}
                >
                  Carry {example}'s acceptable forms and exclusions over to {carry.join(", ")}
                </button>
              )}
            </div>
          )}

          <p className="g-hint">
            <strong>Elaborate my existing items.</strong> For one version, the AI suggests acceptable forms and things
            that do not count. It does not change your statements. Once you accept what you want, it can be carried
            over to the other versions.
          </p>
          {elaborateTarget ? (
            <div className="g-row">
              {ai.elaborate.length > 1 && (
                <select value={elaborateTarget} onChange={(e) => setElaborateId(e.target.value)} aria-label="Version to elaborate">
                  {ai.elaborate.map((id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ))}
                </select>
              )}
              <button
                className="g-ghost small"
                disabled={busy || drafting}
                onClick={() => draftNow({ mode: "elaborate", problem_ids: [elaborateTarget] })}
              >
                Suggest them for {elaborateTarget}
              </button>
            </div>
          ) : (
            <p className="g-hint">It needs a version with a problem text and at least one item.</p>
          )}

          {materialTarget && (
            <>
              <p className="g-hint">
                <strong>Draft from pasted material.</strong> For a version without a rubric: paste its solution, your
                grading notes or a point scheme. Do not paste student work: the text goes to the AI as it is.
              </p>
              {ai.from_material.length > 1 && (
                <select value={materialTarget} onChange={(e) => setMaterialId(e.target.value)} aria-label="Version to draft for">
                  {ai.from_material.map((id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ))}
                </select>
              )}
              <textarea
                className="g-paste"
                rows={4}
                value={material}
                onChange={(e) => setMaterial(e.target.value)}
                placeholder="Solution, grading notes, point scheme…"
                aria-label="Material to draft from"
              />
              <div className="g-row">
                <button
                  className="g-ghost small"
                  disabled={busy || drafting || (!material.trim() && !bank.has_material)}
                  onClick={() =>
                    draftNow({
                      mode: "material",
                      problem_ids: [materialTarget],
                      ...(material.trim() ? { material } : {}),
                    }).then((started) => started && setMaterial(""))
                  }
                >
                  {material.trim() || !bank.has_material
                    ? `Draft ${materialTarget} from this material`
                    : `Draft ${materialTarget} again from what you pasted before`}
                </button>
              </div>
            </>
          )}
        </>
      )}

      <h4>Details</h4>
      <p className="g-hint">What was noticed in the uploaded file, with file rows, for anyone fixing the file itself.</p>
      <FindingList findings={rubricFindings} />
    </SideBox>
  );

  return (
    <StepLayout side={side}>
      <section className="g-card">
        <div className="g-row between">
          <h2>
            Rubric{" "}
            <span className={`g-status ${bank.rubric_status === "approved" && !legacy ? "ok" : ""}`}>
              {legacy
                ? "view only"
                : bank.rubric_status === "approved"
                  ? `approved and locked · revision ${bank.rubric_revision}`
                  : bank.rubric_revision > 0
                    ? `draft of revision ${bank.rubric_revision + 1}`
                    : "draft"}
            </span>
          </h2>
          {editable && <span className="g-hint">{saving ? "Saving…" : "Changes are saved as you type."}</span>}
        </div>

        {legacy && <p className="g-callout">{toFill[0]?.message}</p>}

        {bank.rubric_status === "approved" && !legacy && (
          <>
            <p className="g-muted">
              The rubric is locked{bank.rubric_approved_at ? ` (approved ${fmtDate(bank.rubric_approved_at)})` : ""}. Every
              run is graded with it and keeps a copy of it.
            </p>
            {bank.check.warnings.map((w) => (
              <p className="g-warn" key={w}>
                {w}
              </p>
            ))}
            <div className="g-row">
              <button className="g-primary" onClick={onNext}>
                Continue to responses →
              </button>
              <button className="g-ghost" disabled={busy} onClick={() => reshape(() => api.unlock(bankId))}>
                Edit rubric (starts revision {bank.rubric_revision + 1})
              </button>
            </div>
          </>
        )}

        {editable && (
          <>
            <p className="g-muted">
              One panel per version of the problem. Every item is graded on its own: credit or no credit.
              {runCount > 0 && ` The ${plural(runCount, "run")} you already have ${runCount === 1 ? "keeps" : "keep"} the rubric used at the time.`}
              {bank.responses.length > 0 && ` The ${plural(bank.responses.length, "response")} stay attached while you edit.`}
            </p>
            <DraftingStatus
              drafting={bank.drafting}
              onCancel={() => api.cancelDraft(bankId).then(onBank).catch(report)}
            />
            {bank.check.rubric_state === "none" && byHand.length > 0 && !drafting && (
              <p className="g-callout">
                A rubric is required for at least one version. Write the items for one version below
                {drafts.length > 1 ? "; you can then copy them to the other versions." : "."} Or paste a solution or
                your grading notes under "More options" and let the AI draft them for you to review.
              </p>
            )}
            {!drafting && missing.length > 0 && (
              <div className="g-callout">
                <p>
                  {listed(missing)} {missing.length === 1 ? "has" : "have"} no rubric yet. Do you want the AI to draft{" "}
                  {missing.length === 1 ? "it" : "them"} from version {example}? You review everything it writes before
                  it is used.
                </p>
                <div className="g-row">
                  <button
                    className="g-primary small"
                    disabled={busy}
                    onClick={() => draftNow({ mode: "example", example_id: example, problem_ids: missing })}
                  >
                    Yes, draft {missing.length === 1 ? "it" : "them"}
                  </button>
                  <button className="g-ghost small" onClick={() => goToBox({ box: "items", problem_id: missing[0] })}>
                    No, I will write {missing.length === 1 ? "it" : "them"}
                  </button>
                </div>
              </div>
            )}
            {bank.check.rubric_state === "some" && byHand.length > 0 && (
              <p className="g-callout">
                {listed(byHand)} {byHand.length === 1 ? "has" : "have"} no rubric yet. Write the items by hand, or copy
                them from a version that has one.
                {byHand.some((id) => ai.needs_text.includes(id)) &&
                  " The AI can draft them too, once the problem text is filled in."}
              </p>
            )}
            {!drafting && carry.length > 0 && example === ai.example && (
              <div className="g-callout">
                <p>
                  Version {example} has acceptable forms or exclusions that{" "}
                  {carry.length === 1 ? "version" : "versions"} {carry.join(", ")}{" "}
                  {carry.length === 1 ? "lacks" : "lack"}. Do you want the AI to carry them over? It keeps the
                  statements as they are, and you review what it adds.
                </p>
                <div className="g-row">
                  <button
                    className="g-ghost small"
                    disabled={busy}
                    onClick={() => draftNow({ mode: "example", example_id: example, problem_ids: carry })}
                  >
                    Yes, draft them
                  </button>
                </div>
              </div>
            )}
            {fileNotes.map((f, i) => (
              <p className="g-callout" key={i}>
                {f.message}
              </p>
            ))}

            {toFill.length > 0 && (
              <button className="g-count" onClick={() => goToBox(toFill[0])}>
                {plural(toFill.length, "box", "boxes")} still to fill →
              </button>
            )}
            {toDecide.length > 0 && (
              <button className="g-count decide" onClick={() => goToBox(toDecide[0])}>
                {plural(toDecide.length, "AI draft")} to accept or discard →
              </button>
            )}
            {canApprove && <p className="g-ok">Nothing is missing. You can approve the rubric.</p>}

            {shownMarks.length > 0 && (
              <div className="g-filled">
                <p>
                  We filled in {plural(shownMarks.length, "box", "boxes")} for you. {shownMarks.length === 1 ? "It is" : "They are"}{" "}
                  marked <span className="g-mark">filled in for you</span> and can be changed.
                </p>
                <ul>
                  {shownMarks.slice(0, 3).map((f, i) => (
                    <li key={i}>{f.message}</li>
                  ))}
                </ul>
                {shownMarks.length > 3 && (
                  <details>
                    <summary>and {shownMarks.length - 3} more</summary>
                    <ul>
                      {shownMarks.slice(3).map((f, i) => (
                        <li key={i}>{f.message}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}

            {bank.check.warnings.map((w) => (
              <p className="g-warn" key={w}>
                {w} This does not stop you.
              </p>
            ))}

            <div className="g-row">
              <button
                className={`g-primary${canApprove ? "" : " off"}`}
                aria-disabled={!canApprove}
                disabled={busy}
                onClick={() => reshape(() => api.approve(bankId))}
              >
                Approve &amp; lock
              </button>
              <span className="g-muted">
                {canApprove
                  ? "Locks the rubric so grading always uses exactly this wording. You can still edit it later."
                  : `Not yet: ${notYet}`}
              </span>
            </div>
          </>
        )}
      </section>

      {!editable &&
        bank.problems.map((p) => (
          <section className="g-card" key={p.id}>
            <h3>
              {legacy ? "Problem" : "Version"} {p.id}{" "}
              <span className="g-muted">· {plural(responsesOf(p.id), "response")}</span>
            </h3>
            <h4>Problem text</h4>
            <pre className="g-pre">{p.text || "(none)"}</pre>
            {p.instructions && (
              <>
                <h4>What students were asked to write</h4>
                <pre className="g-pre">{p.instructions}</pre>
              </>
            )}
            <h4>Rubric items</h4>
            {bank.criteria
              .filter((c) => c.problem_id === p.id)
              .map((c) => (
                <div className="g-item" key={c.id}>
                  <div className="g-item-head">
                    <strong>{c.label}</strong>
                    <span className="g-muted">weight {c.points}</span>
                    <OriginMark origin={c.origin} />
                  </div>
                  <pre className="g-pre">{c.text}</pre>
                  <Lines label="Acceptable forms" lines={c.accept} aiLines={c.ai_lines} />
                  <Lines label="Does not count" lines={c.reject} aiLines={c.ai_lines} />
                  {c.source_text && c.source_text !== c.text && (
                    <details className="g-orig">
                      <summary>Original wording</summary>
                      <pre className="g-pre">{c.source_text}</pre>
                    </details>
                  )}
                </div>
              ))}
          </section>
        ))}

      {editable && (
        <fieldset className="g-boxes" disabled={busy}>
          {drafts.map((v) => {
          const others = withRubric.filter((o) => o.uid !== v.uid);
          const proposal = proposals.get(v.id);
          const draftedItems = proposal?.items.filter((i) => i.text !== "") ?? [];
          const canGenerate = !proposal && !!example && example !== v.id && ai.from_example.includes(v.id);
          const canGuess = !proposal && bank.ai_fill && ai.from_text.includes(v.id);
          return (
            <section className="g-card" key={v.uid}>
              <div className="g-row g-version-head">
                <label className="g-inline">
                  Version
                  <input
                    className="g-idbox"
                    value={v.idBox}
                    aria-label="Version name"
                    onChange={(e) => update((ds) => ds.map((d) => (d.uid === v.uid ? { ...d, idBox: e.target.value } : d)))}
                    onBlur={() => rename(v.uid)}
                    onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                  />
                </label>
                <FilledMark finding={mark("version_id", v, "", v.idBox)} />
                {responsesOf(v.id) > 0 && <span className="g-muted">· {plural(responsesOf(v.id), "response")}</span>}
              </div>
              {idError[v.uid] && <p className="g-need">{idError[v.uid]}</p>}

              <label className="g-field">
                <span>
                  Problem text <FilledMark finding={mark("version_text", v, "", v.text)} />
                </span>
                <textarea
                  id={boxId("version_text", v.id)}
                  className={v.text.trim() ? "" : "needed"}
                  rows={rows(v.text, 3)}
                  value={v.text}
                  placeholder="Type or paste the problem exactly as the students saw it"
                  onChange={(e) => editVersion(v.uid, { text: e.target.value })}
                  onBlur={() => void flush()}
                />
                {!v.text.trim() && <span className="g-need">Needed: the problem text of this version.</span>}
              </label>
              {proposal?.version_text && (
                <ProposedText
                  proposal={proposal}
                  id={boxId("proposal_text", v.id)}
                  busy={busy}
                  onAccept={() => accept(proposal, [], true)}
                  onDiscard={() => discard(proposal, [], true)}
                />
              )}
              <details className="g-inner" open={!!v.instructions}>
                <summary>
                  What students were asked to write (optional){" "}
                  <FilledMark finding={mark("version_instructions", v, "", v.instructions)} />
                </summary>
                <textarea
                  rows={rows(v.instructions)}
                  value={v.instructions}
                  placeholder="For example: explain which physics principle you used and why"
                  onChange={(e) => editVersion(v.uid, { instructions: e.target.value })}
                  onBlur={() => void flush()}
                />
              </details>

              <h4>Rubric items</h4>
              {proposal && (
                <ProposalHead
                  proposal={proposal}
                  id={v.items.length === 0 ? boxId("items", v.id) : undefined}
                  busy={busy || drafting}
                  onAcceptAll={() => {
                    const together = acceptTogether(proposal);
                    return accept(proposal, together.keys, together.text);
                  }}
                  onDiscardAll={() => discard(proposal)}
                  onAgain={() => again(proposal)}
                />
              )}
              {proposal &&
                draftedItems.map((drafted) => (
                  <ProposedItemCard
                    key={drafted.key}
                    proposal={proposal}
                    item={drafted}
                    id={boxId("proposal", v.id, drafted.key)}
                    busy={busy}
                    onAccept={() => accept(proposal, [drafted.key])}
                    onDiscard={() => discard(proposal, [drafted.key])}
                  />
                ))}
              {v.items.length === 0 && draftedItems.length === 0 && (
                <div id={proposal ? undefined : boxId("items", v.id)} className="g-empty needed" tabIndex={-1}>
                  <p>Needed: at least one rubric item for this version.</p>
                  <div className="g-row">
                    <button className="g-primary small" onClick={() => addItem(v)}>
                      Write the first item
                    </button>
                    {others.map((o) => (
                      <button
                        key={o.uid}
                        className="g-ghost small"
                        disabled={busy}
                        onClick={() => reshape(() => api.copyRubric(bankId, idOf(o.uid), [idOf(v.uid)]))}
                      >
                        Copy the {plural(o.items.length, "item")} from {o.id}
                      </button>
                    ))}
                    {canGenerate && (
                      <button
                        className="g-ghost small"
                        disabled={busy || drafting}
                        title="The AI adapts that version's items to this one. You review what it writes."
                        onClick={() => draftNow({ mode: "example", example_id: example, problem_ids: [idOf(v.uid)] })}
                      >
                        Generate based on {example}
                      </button>
                    )}
                    {canGuess && (
                      <button
                        className="g-ghost small"
                        disabled={busy || drafting}
                        title="The AI's own guess from the problem text. Every item it writes is marked as inferred."
                        onClick={() => draftNow({ mode: "text", problem_ids: [idOf(v.uid)] })}
                      >
                        Let the AI draft from the problem text
                      </button>
                    )}
                  </div>
                </div>
              )}
              {v.items.map((item) => {
                const weightOk = validWeight(item.weight);
                const savedItem = saved.get(`${v.id}|${item.key}`);
                const suggested = proposal?.items.find((i) => i.text === "" && i.key === item.key);
                return (
                  <div className="g-item" key={item.uid}>
                    <div className="g-item-head">
                      <strong>Item {item.key}</strong>
                      <FilledMark finding={mark("item_key", v, item.key, item.key) ?? mark("item", v, item.key)} />
                      <OriginMark origin={savedItem?.origin} />
                      <button
                        className="g-ghost small g-remove"
                        onClick={() => editItems(v.uid, (items) => items.filter((i) => i.uid !== item.uid))}
                      >
                        Remove item
                      </button>
                    </div>
                    <div className="g-item-boxes">
                      <div className="g-item-text">
                        <textarea
                          id={boxId("item_text", v.id, item.key)}
                          className={item.text.trim() ? "" : "needed"}
                          rows={rows(item.text)}
                          value={item.text}
                          aria-label={`Item ${item.key} text`}
                          placeholder="What the response has to show to earn this item"
                          onChange={(e) =>
                            editItems(v.uid, (items) =>
                              items.map((i) => (i.uid === item.uid ? { ...i, text: e.target.value } : i)),
                            )
                          }
                          onBlur={() => void flush()}
                        />
                        {!item.text.trim() && <span className="g-need">Needed: the text of this item.</span>}
                      </div>
                      <label className="g-weight">
                        <span>
                          Weight <FilledMark finding={mark("weight", v, item.key, item.weight)} />
                        </span>
                        <input
                          id={boxId("weight", v.id, item.key)}
                          className={weightOk ? "" : "needed"}
                          inputMode="decimal"
                          value={item.weight}
                          aria-label={`Item ${item.key} weight`}
                          onChange={(e) =>
                            editItems(v.uid, (items) =>
                              items.map((i) => (i.uid === item.uid ? { ...i, weight: e.target.value } : i)),
                            )
                          }
                          onBlur={() => void flush()}
                        />
                        {!weightOk && <span className="g-need">Needed: a number above zero.</span>}
                      </label>
                    </div>
                    <details className="g-inner" open={item.accept.length + item.reject.length > 0}>
                      <summary>Acceptable forms and exclusions (optional)</summary>
                      <LinesEditor
                        label="Acceptable forms"
                        placeholder="Another way a response can satisfy this item"
                        lines={item.accept}
                        aiLines={savedItem?.ai_lines ?? []}
                        onChange={(lines) =>
                          editItems(v.uid, (items) => items.map((i) => (i.uid === item.uid ? { ...i, accept: lines } : i)))
                        }
                        onBlur={() => void flush()}
                      />
                      <LinesEditor
                        label="Does not count"
                        placeholder="Something close that must not earn this item"
                        lines={item.reject}
                        aiLines={savedItem?.ai_lines ?? []}
                        onChange={(lines) =>
                          editItems(v.uid, (items) => items.map((i) => (i.uid === item.uid ? { ...i, reject: lines } : i)))
                        }
                        onBlur={() => void flush()}
                      />
                    </details>
                    {proposal && suggested && (
                      <ProposedItemCard
                        proposal={proposal}
                        item={suggested}
                        id={boxId("proposal", v.id, suggested.key)}
                        busy={busy}
                        onAccept={() => accept(proposal, [suggested.key])}
                        onDiscard={() => discard(proposal, [suggested.key])}
                      />
                    )}
                  </div>
                );
              })}
              {(v.items.length > 0 || draftedItems.length > 0) && (
                <div className="g-row">
                  <button className="g-ghost small" onClick={() => addItem(v)}>
                    + Add an item
                  </button>
                </div>
              )}
            </section>
          );
          })}
        </fieldset>
      )}
    </StepLayout>
  );
}
