// Step 2: the rubric, as boxes. By default the versions of a problem share one rubric,
// edited once at the top; a version can have its own wording of a shared item, which
// replaces it for that version only. A bank whose versions each have their own items
// (an upload where they differ) shows one rubric per version instead. Boxes that code
// filled from the file carry a "filled in for you" mark and stay editable; empty boxes
// that are needed are highlighted. The server's live check says what is still missing;
// Approve & lock stays off until nothing is. An AI draft shows where it would go,
// marked as not part of the rubric yet, until it is accepted or discarded (Drafts.tsx).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError, SHARED, type Bank, type Box, type DraftRequest, type Finding, type Proposal } from "./api";
import { NO_FILE, readTextFile, recognize, type TextFile } from "./csv";
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

type ItemDraft = { uid: string; key: string; text: string; weight: string; specs: string[] };
type VersionDraft = {
  uid: string;
  id: string; // the id the server knows; SHARED for the shared rubric
  idBox: string; // what is typed in the id box
  text: string;
  instructions: string;
  items: ItemDraft[]; // the shared items, or the version's own
};
const SHARED_UID = "shared";

let lastUid = 0;
const uid = () => `u${++lastUid}`;

const parseWeight = (text: string): number | null => {
  const t = text.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
};
const validWeight = (text: string) => (parseWeight(text) ?? 0) > 0;

function itemDrafts(bank: Bank, problemId: string): ItemDraft[] {
  return bank.criteria
    .filter((c) => c.problem_id === problemId)
    .map((c) => ({
      uid: uid(),
      key: c.key,
      text: c.text,
      weight: c.points === 0 ? "" : String(c.points),
      specs: c.specifications,
    }));
}

/** The boxes: the shared rubric first, then each version with its own items. */
function toDrafts(bank: Bank): VersionDraft[] {
  return [
    { uid: SHARED_UID, id: SHARED, idBox: "", text: "", instructions: "", items: itemDrafts(bank, SHARED) },
    ...bank.problems.map((p) => ({
      uid: uid(),
      id: p.id,
      idBox: p.id,
      text: p.text,
      instructions: p.instructions,
      items: itemDrafts(bank, p.id),
    })),
  ];
}

/** The DOM id of a box, so a blocked button can take the owner straight to it. */
const boxId = (box: string, problemId = "", itemKey = "") =>
  `gbox:${box}:${encodeURIComponent(problemId)}:${encodeURIComponent(itemKey)}`;

function goToBox(b: { box: string; problem_id?: string; item_key?: string } | undefined) {
  if (!b) return;
  const el =
    document.getElementById(boxId(b.box, b.problem_id, b.item_key)) ??
    document.getElementById(boxId("items", b.problem_id)) ??
    document.getElementById(boxId("items", SHARED));
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
/** Every version, in a sentence: "both versions" or "all 20 versions". */
const everyVersion = (n: number) => (n === 1 ? "the one version" : n === 2 ? "both versions" : `all ${n} versions`);
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
  const [shareFrom, setShareFrom] = useState("");
  const [removeId, setRemoveId] = useState("");
  const [ownKey, setOwnKey] = useState<Record<string, string>>({}); // version uid -> the item to word its own way
  const [rubricFile, setRubricFile] = useState<TextFile>(NO_FILE);
  // Drafting with AI (side box): the versions chosen there, and the pasted material.
  const [exampleId, setExampleId] = useState("");
  const [elaborateId, setElaborateId] = useState("");
  const [materialId, setMaterialId] = useState("");
  const [ownWordingId, setOwnWordingId] = useState("");
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
          if (fields && v.uid !== SHARED_UID)
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
                specifications: i.specs.filter((line) => line.trim() !== ""),
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
  const addItem = (v: VersionDraft, from?: ItemDraft) => {
    const taken = new Set(v.items.map((i) => i.key));
    let n = v.items.length + 1;
    while (taken.has(String(n))) n++;
    const key = from?.key ?? String(n);
    editItems(v.uid, (items) => [
      ...items,
      { uid: uid(), key, text: from?.text ?? "", weight: from?.weight ?? "1", specs: [...(from?.specs ?? [])] },
    ]);
    window.setTimeout(() => document.getElementById(boxId("item_text", v.id, key))?.focus(), 0);
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
      ...(p.mode === "example" && p.example_id ? { example_id: p.example_id } : {}),
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

  const shared = drafts.find((d) => d.uid === SHARED_UID) ?? drafts[0];
  const versions = drafts.filter((d) => d.uid !== SHARED_UID);
  const ai = bank.ai;
  const proposals = new Map(bank.proposals.map((p) => [p.problem_id, p]));
  const textDrafts = new Map(bank.proposals.filter((p) => p.version_text).map((p) => [p.text_for || p.problem_id, p]));
  // The versions share a rubric (the default), or each has its own (an upload where they differ).
  const sharedMode = ai.shared || bank.criteria.length === 0 || shared.items.length > 0;
  const sharedByKey = new Map(shared.items.map((i) => [i.key, i]));
  const withRubric = versions.filter((d) => d.items.length > 0);
  const withoutRubric = versions.filter((d) => d.items.length === 0);
  const saved = new Map(bank.criteria.map((c) => [`${c.problem_id}|${c.key}`, c]));
  const undrafted = (ids: string[]) => ids.filter((id) => !proposals.has(id));
  // Rubrics of their own: the version the others are drafted from, the first with items unless another is chosen.
  const example = withRubric.some((v) => v.id === exampleId) ? exampleId : ai.example;
  const missing = !sharedMode && example ? undrafted(ai.from_example) : [];
  const carry = sharedMode
    ? []
    : example === ai.example
      ? undrafted(ai.carry_over)
      : undrafted(withRubric.filter((v) => v.id !== example).map((v) => v.id));
  const byHand = bank.check.without_rubric.filter((id) => !missing.includes(id) && !proposals.has(id));
  const elaborateTarget = ai.elaborate.includes(elaborateId) ? elaborateId : (ai.elaborate[0] ?? null);
  const materialTarget = ai.from_material.includes(materialId) ? materialId : (ai.from_material[0] ?? null);
  const ownWordingTarget = ai.own_wording.includes(ownWordingId) ? ownWordingId : (ai.own_wording[0] ?? "");
  const listed = (ids: string[]) => `${ids.length === 1 ? "Version" : "Versions"} ${ids.join(", ")}`;
  const where = (id: string) => (id ? `version ${id}` : "the rubric");
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
  const shareSource = withRubric.find((v) => v.uid === shareFrom) ?? withRubric[0];
  const removeTarget = versions.find((v) => v.uid === removeId) ?? versions[versions.length - 1];
  const responsesOf = (id: string) => bank.responses.filter((r) => r.problem_id === id).length;

  /** A draft for these items, in place: its head, then the whole items it drafts. */
  const draftBlock = (v: VersionDraft, emptyBoxId?: string) => {
    const proposal = proposals.get(v.id);
    if (!proposal) return null;
    return (
      <>
        <ProposalHead
          proposal={proposal}
          id={emptyBoxId}
          busy={busy || drafting}
          onAcceptAll={() => {
            const together = acceptTogether(proposal);
            return accept(proposal, together.keys, together.text);
          }}
          onDiscardAll={() => discard(proposal)}
          onAgain={() => again(proposal)}
        />
        {proposal.items
          .filter((i) => i.text !== "")
          .map((drafted) => (
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
      </>
    );
  };

  /**
   * One item's boxes. `own` marks a version's own wording of a shared item: its
   * weight is the shared item's, and removing it goes back to the shared wording.
   */
  const itemBoxes = (v: VersionDraft, item: ItemDraft, own?: ItemDraft) => {
    const weightOk = validWeight(item.weight);
    const savedItem = saved.get(`${v.id}|${item.key}`);
    const proposal = proposals.get(v.id);
    const suggested = proposal?.items.find((i) => i.text === "" && i.key === item.key);
    return (
      <div className="g-item" key={item.uid}>
        <div className="g-item-head">
          <strong>Item {item.key}</strong>
          {own && <span className="g-tag">this version's own wording</span>}
          <FilledMark finding={mark("item_key", v, item.key, item.key) ?? mark("item", v, item.key)} />
          <OriginMark origin={savedItem?.origin} />
          <button
            className="g-ghost small g-remove"
            onClick={() => editItems(v.uid, (items) => items.filter((i) => i.uid !== item.uid))}
          >
            {own ? "Use the shared wording again" : "Remove item"}
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
                editItems(v.uid, (items) => items.map((i) => (i.uid === item.uid ? { ...i, text: e.target.value } : i)))
              }
              onBlur={() => void flush()}
            />
            {!item.text.trim() && <span className="g-need">Needed: the text of this item.</span>}
          </div>
          {own ? (
            <p className="g-weight">
              Weight {own.weight || "not set"}
              <span className="g-hint">the shared item's</span>
            </p>
          ) : (
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
                  editItems(v.uid, (items) => items.map((i) => (i.uid === item.uid ? { ...i, weight: e.target.value } : i)))
                }
                onBlur={() => void flush()}
              />
              {!weightOk && <span className="g-need">Needed: a number above zero.</span>}
            </label>
          )}
        </div>
        <details className="g-inner" open={item.specs.length > 0}>
          <summary>Specifications (optional)</summary>
          <LinesEditor
            label="Specifications"
            placeholder="What counts, what also counts, or what does not count, one line each"
            lines={item.specs}
            aiLines={savedItem?.ai_lines ?? []}
            onChange={(lines) =>
              editItems(v.uid, (items) => items.map((i) => (i.uid === item.uid ? { ...i, specs: lines } : i)))
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
  };

  const side = (
    <SideBox title="More options">
      {editable && (
        <>
          <h4>Rubric file</h4>
          <p className="g-hint">
            A rubric in Dr. Chen's YAML format becomes the rubric every version shares. It has no weights, so each item
            is worth 1 until you change it.
          </p>
          <label className="g-field">
            <input
              type="file"
              accept=".yaml,.yml,.txt"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) readTextFile(file).then(setRubricFile);
                e.target.value = "";
              }}
            />
            {rubricFile.name && (
              <span className="g-hint">
                {rubricFile.name}: {recognize(rubricFile.text).sentence}
              </span>
            )}
          </label>
          {rubricFile.text && recognize(rubricFile.text).kind === "rubric" && (
            <div className="g-row">
              {bank.criteria.length > 0 ? (
                <ConfirmButton
                  label="Replace the rubric with this file"
                  question="Replace every rubric item, shared or of a version, with the items in the file? AI drafts made for the old items are dropped."
                  confirm="Replace it"
                  disabled={busy}
                  onConfirm={() =>
                    reshape(() => api.rubricFile(bankId, rubricFile.text, true), true).then(() => setRubricFile(NO_FILE))
                  }
                />
              ) : (
                <button
                  className="g-ghost small"
                  disabled={busy}
                  onClick={() =>
                    reshape(() => api.rubricFile(bankId, rubricFile.text), true).then(() => setRubricFile(NO_FILE))
                  }
                >
                  Use this rubric
                </button>
              )}
            </div>
          )}

          {!sharedMode && withRubric.length > 0 && versions.length > 1 && shareSource && (
            <>
              <h4>One rubric for every version</h4>
              <div className="g-row">
                <select value={shareSource.uid} onChange={(e) => setShareFrom(e.target.value)} aria-label="Version whose items to share">
                  {withRubric.map((v) => (
                    <option key={v.uid} value={v.uid}>
                      {v.id}
                    </option>
                  ))}
                </select>
                <button
                  className="g-ghost small"
                  disabled={busy}
                  onClick={() => reshape(() => api.shareRubric(bankId, idOf(shareSource.uid)), true)}
                >
                  Use these items for every version
                </button>
              </div>
              <p className="g-hint">
                Every version then uses these items. A version keeps, as its own wording, only the items it words
                differently.
              </p>

              <h4>Copy a rubric to versions without one</h4>
              {withoutRubric.length > 0 && copySource ? (
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
                <p className="g-hint">Every version already has a rubric.</p>
              )}
              <p className="g-hint">The items are copied unchanged and marked "copied", so you can adapt them. No AI is involved.</p>
            </>
          )}

          <h4>Versions</h4>
          <div className="g-row">
            <button className="g-ghost small" disabled={busy} onClick={() => reshape(() => api.addVersion(bankId))}>
              Add a version
            </button>
          </div>
          {versions.length > 1 && removeTarget && (
            <div className="g-row">
              <select value={removeTarget.uid} onChange={(e) => setRemoveId(e.target.value)} aria-label="Version to remove">
                {versions.map((v) => (
                  <option key={v.uid} value={v.uid}>
                    {v.id}
                  </option>
                ))}
              </select>
              <ConfirmButton
                label="Remove this version"
                question={
                  <>
                    Remove version <strong>{removeTarget.id}</strong> with its problem text and its own items? Responses
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
            Off, the AI drafts only when you ask it to. On, it drafts an empty rubric as soon as you switch this on: from
            a version that has one, or else from the problem texts alone. Either way, nothing it writes is used until you
            accept it.
          </p>

          <h4>Draft with AI</h4>
          {sharedMode && elaborateTarget === SHARED && (
            <>
              <p className="g-hint">
                <strong>Elaborate the rubric.</strong> The AI suggests specifications for the items: other ways a
                response can satisfy them, and near misses that do not count. It sees every version's problem text and
                does not change your statements.
              </p>
              <div className="g-row">
                <button
                  className="g-ghost small"
                  disabled={busy || drafting}
                  onClick={() => draftNow({ mode: "elaborate", problem_ids: [SHARED] })}
                >
                  Suggest specifications
                </button>
              </div>
            </>
          )}
          {sharedMode && ownWordingTarget && (
            <>
              <p className="g-hint">
                <strong>A version's own wording.</strong> Only where a version needs it: the AI adapts the shared items to
                that version's problem.
              </p>
              <div className="g-row">
                {ai.own_wording.length > 1 && (
                  <select
                    value={ownWordingTarget}
                    onChange={(e) => setOwnWordingId(e.target.value)}
                    aria-label="Version to word its own way"
                  >
                    {ai.own_wording.map((id) => (
                      <option key={id} value={id}>
                        {id}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  className="g-ghost small"
                  disabled={busy || drafting}
                  onClick={() => draftNow({ mode: "example", problem_ids: [ownWordingTarget] })}
                >
                  Draft its own wording
                </button>
              </div>
            </>
          )}

          {!sharedMode && withRubric.length > 1 && (
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
          {!sharedMode && example && (missing.length > 0 || carry.length > 0) && (
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
                  Carry {example}'s specifications over to {carry.join(", ")}
                </button>
              )}
            </div>
          )}
          {!sharedMode && elaborateTarget && (
            <>
              <p className="g-hint">
                <strong>Elaborate my existing items.</strong> For one version, the AI suggests specifications. It does not
                change your statements. Once you accept what you want, they can be carried over to the other versions.
              </p>
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
            </>
          )}

          {materialTarget !== null && (
            <>
              <p className="g-hint">
                <strong>Draft from pasted material.</strong> Paste a solution, your grading notes or a point scheme
                {materialTarget === SHARED ? ", and the AI drafts the rubric every version shares." : ` for ${where(materialTarget)}.`} Do
                not paste student work: the text goes to the AI as it is.
              </p>
              {ai.from_material.length > 1 && (
                <select value={materialTarget} onChange={(e) => setMaterialId(e.target.value)} aria-label="Version to draft for">
                  {ai.from_material.map((id) => (
                    <option key={id} value={id}>
                      {id || "the shared rubric"}
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
                  {material.trim() || !bank.has_material ? "Draft from this material" : "Draft again from what you pasted before"}
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

  /** The locked or read-only view of some items. */
  const readOnlyItems = (problemId: string) =>
    bank.criteria
      .filter((c) => c.problem_id === problemId)
      .map((c) => (
        <div className="g-item" key={c.id}>
          <div className="g-item-head">
            <strong>{c.label}</strong>
            {problemId !== SHARED && sharedMode && <span className="g-tag">this version's own wording</span>}
            <span className="g-muted">weight {c.points}</span>
            <OriginMark origin={c.origin} />
          </div>
          <pre className="g-pre">{c.text}</pre>
          <Lines label="Specifications" lines={c.specifications} aiLines={c.ai_lines} />
          {c.source_text && c.source_text !== c.text && (
            <details className="g-orig">
              <summary>Original wording</summary>
              <pre className="g-pre">{c.source_text}</pre>
            </details>
          )}
        </div>
      ));

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
              {sharedMode
                ? versions.length > 1
                  ? `One rubric for ${everyVersion(versions.length)} of the problem, below. A version can word an item its own way where it needs to.`
                  : "The rubric of the problem, below."
                : "Each version of the problem has its own rubric, below."}{" "}
              Every item is graded on its own: credit or no credit.
              {runCount > 0 && ` The ${plural(runCount, "run")} you already have ${runCount === 1 ? "keeps" : "keep"} the rubric used at the time.`}
              {bank.responses.length > 0 && ` The ${plural(bank.responses.length, "response")} stay attached while you edit.`}
              {bank.held_responses > 0 && ` ${plural(bank.held_responses, "response is", "responses are")} held until the rubric is approved.`}
            </p>
            <DraftingStatus
              drafting={bank.drafting}
              onCancel={() => api.cancelDraft(bankId).then(onBank).catch(report)}
            />
            {bank.check.rubric_state === "none" && !proposals.has(SHARED) && !drafting && (
              <p className="g-callout">
                A rubric is required. Write its items below, or, under "More options", add a rubric file or paste a
                solution or your grading notes for the AI to draft from.
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
            {!sharedMode && bank.check.rubric_state === "some" && byHand.length > 0 && (
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
                  Version {example} has specifications that {carry.length === 1 ? "version" : "versions"}{" "}
                  {carry.join(", ")} {carry.length === 1 ? "lacks" : "lack"}. Do you want the AI to carry them over? It
                  keeps the statements as they are, and you review what it adds.
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

      {!editable && sharedMode && bank.criteria.some((c) => c.problem_id === SHARED) && (
        <section className="g-card">
          <h3>
            Rubric <span className="g-muted">· used for {everyVersion(bank.problems.length)}</span>
          </h3>
          {readOnlyItems(SHARED)}
        </section>
      )}
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
            {bank.criteria.some((c) => c.problem_id === p.id) && (
              <h4>{sharedMode ? "Its own wording" : "Rubric items"}</h4>
            )}
            {readOnlyItems(p.id)}
          </section>
        ))}

      {editable && (
        <fieldset className="g-boxes" disabled={busy}>
          {sharedMode && (
            <section className="g-card g-shared">
              <h3>
                Rubric <span className="g-muted">· used for {everyVersion(versions.length)}</span>
              </h3>
              {draftBlock(shared, shared.items.length === 0 ? boxId("items", SHARED) : undefined)}
              {shared.items.length === 0 && !proposals.get(SHARED)?.items.some((i) => i.text) && (
                <div id={proposals.has(SHARED) ? undefined : boxId("items", SHARED)} className="g-empty needed" tabIndex={-1}>
                  <p>Needed: at least one rubric item. It is used for every version.</p>
                  <div className="g-row">
                    <button className="g-primary small" onClick={() => addItem(shared)}>
                      Write the first item
                    </button>
                    {bank.ai_fill && ai.from_text.includes(SHARED) && !proposals.has(SHARED) && (
                      <button
                        className="g-ghost small"
                        disabled={busy || drafting}
                        title="The AI's own guess from the problem texts. Every item it writes is marked as inferred."
                        onClick={() => draftNow({ mode: "text", problem_ids: [SHARED] })}
                      >
                        Let the AI draft from the problem texts
                      </button>
                    )}
                  </div>
                </div>
              )}
              {shared.items.map((item) => itemBoxes(shared, item))}
              {(shared.items.length > 0 || proposals.has(SHARED)) && (
                <div className="g-row">
                  <button className="g-ghost small" onClick={() => addItem(shared)}>
                    + Add an item
                  </button>
                </div>
              )}
            </section>
          )}

          {versions.map((v) => {
            const others = withRubric.filter((o) => o.uid !== v.uid);
            const proposal = proposals.get(v.id);
            const textDraft = textDrafts.get(v.id);
            const draftedItems = proposal?.items.filter((i) => i.text !== "") ?? [];
            const canGenerate = !proposal && !!example && example !== v.id && ai.from_example.includes(v.id);
            // Shared items this version has no wording of its own for yet.
            const unworded = shared.items.filter((s) => !v.items.some((i) => i.key === s.key));
            const chosen = unworded.find((s) => s.key === ownKey[v.uid]) ?? unworded[0];
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
                {textDraft && (
                  <ProposedText
                    proposal={textDraft}
                    id={boxId("proposal_text", v.id)}
                    busy={busy}
                    onAccept={() => accept(textDraft, [], true)}
                    onDiscard={() => discard(textDraft, [], true)}
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

                {sharedMode ? (
                  // Nothing to word its own way until the shared rubric has items.
                  (shared.items.length > 0 || v.items.length > 0 || proposal) && (
                  <>
                    <h4>Its own wording (optional)</h4>
                    {v.items.length === 0 && !proposal && (
                      <p className="g-hint">This version is graded with the shared rubric as it is.</p>
                    )}
                    {draftBlock(v)}
                    {v.items.map((item) => itemBoxes(v, item, sharedByKey.get(item.key)))}
                    {chosen && (
                      <div className="g-row">
                        {unworded.length > 1 && (
                          <select
                            value={chosen.key}
                            onChange={(e) => setOwnKey((m) => ({ ...m, [v.uid]: e.target.value }))}
                            aria-label="Item to word its own way"
                          >
                            {unworded.map((s) => (
                              <option key={s.key} value={s.key}>
                                Item {s.key}
                              </option>
                            ))}
                          </select>
                        )}
                        <button className="g-ghost small" onClick={() => addItem(v, chosen)}>
                          Word item {chosen.key} its own way
                        </button>
                        {ai.own_wording.includes(v.id) && !proposal && (
                          <button
                            className="g-ghost small"
                            disabled={busy || drafting}
                            title="The AI adapts the shared items to this version's problem. You review what it writes."
                            onClick={() => draftNow({ mode: "example", problem_ids: [idOf(v.uid)] })}
                          >
                            Let the AI adapt the rubric to it
                          </button>
                        )}
                      </div>
                    )}
                  </>
                  )
                ) : (
                  <>
                    <h4>Rubric items</h4>
                    {draftBlock(v, v.items.length === 0 ? boxId("items", v.id) : undefined)}
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
                        </div>
                      </div>
                    )}
                    {v.items.map((item) => itemBoxes(v, item))}
                    {(v.items.length > 0 || draftedItems.length > 0) && (
                      <div className="g-row">
                        <button className="g-ghost small" onClick={() => addItem(v)}>
                          + Add an item
                        </button>
                      </div>
                    )}
                  </>
                )}
              </section>
            );
          })}
        </fieldset>
      )}
    </StepLayout>
  );
}
