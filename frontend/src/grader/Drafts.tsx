// AI drafts on the Rubric step. A draft is shown in the place it would go, marked as
// not part of the rubric yet, with Accept and Discard on every item. What is inferred
// (nothing of the owner's backs it) has to be accepted on its own.
import type { Drafting, Proposal, ProposedItem } from "./api";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Where a draft came from, in plain words. */
export function draftSource(p: Proposal): string {
  switch (p.mode) {
    case "example":
      return `adapted from version ${p.example_id}`;
    case "material":
      return "drafted from your pasted material";
    case "text":
      return "drafted from the problem text alone";
    default:
      return "suggested for your own items";
  }
}

const INFERRED: Record<Proposal["mode"], string> = {
  example: "",
  material: "Inferred: nothing in your pasted material says this word for word.",
  text: "Inferred: the AI's own idea of what a response must show.",
  elaborate: "Inferred: the AI's reading of your statement. Check that it asks for exactly what you mean.",
};

/** The small mark on text the AI wrote. */
export function AiMark({ inferred, title }: { inferred?: boolean; title?: string }) {
  return (
    <span className={`g-ai${inferred ? " inferred" : ""}`} title={title}>
      {inferred ? "AI draft · inferred" : "AI draft"}
    </span>
  );
}

/** The mark for a saved item, by who wrote its statement. */
export function OriginMark({ origin }: { origin: string | undefined }) {
  if (origin === "ai" || origin === "ai_inferred")
    return (
      <AiMark
        inferred={origin === "ai_inferred"}
        title="Drafted by the AI and accepted by you. The mark goes when you reword the item."
      />
    );
  if (origin === "copied")
    return (
      <span className="g-tag" title="Copied unchanged from another version. Check that it fits this one.">
        copied
      </span>
    );
  return null;
}

/** One list of an item (acceptable forms, or does not count), read-only. */
export function Lines({ label, lines, aiLines = [] }: { label: string; lines: string[]; aiLines?: string[] }) {
  if (!lines.length) return null;
  return (
    <>
      <p className="g-hint">{label}</p>
      <ul className="g-details-list">
        {lines.map((line, i) => (
          <li key={i}>
            {line} {aiLines.includes(line) && <AiMark />}
          </li>
        ))}
      </ul>
    </>
  );
}

/** One list of an item as boxes to edit: one line per entry. */
export function LinesEditor({
  label,
  placeholder,
  lines,
  aiLines,
  onChange,
  onBlur,
}: {
  label: string;
  placeholder: string;
  lines: string[];
  aiLines: string[];
  onChange: (lines: string[]) => void;
  onBlur: () => void;
}) {
  return (
    <div className="g-lines">
      <span className="g-hint">{label}</span>
      {lines.map((line, i) => (
        <div className="g-line" key={i}>
          <input
            value={line}
            placeholder={placeholder}
            aria-label={`${label}, line ${i + 1}`}
            onChange={(e) => onChange(lines.map((l, j) => (j === i ? e.target.value : l)))}
            onBlur={onBlur}
          />
          {aiLines.includes(line) && (
            <AiMark title="Written by the AI and accepted by you. The mark goes when you change the line." />
          )}
          <button
            className="g-ghost small"
            aria-label={`Remove line ${i + 1} of ${label}`}
            onClick={() => onChange(lines.filter((_, j) => j !== i))}
          >
            ×
          </button>
        </div>
      ))}
      <button className="g-link-button" onClick={() => onChange([...lines, ""])}>
        + Add a line
      </button>
    </div>
  );
}

/** One drafted item, or the lists drafted for an item the owner wrote, with its decision. */
export function ProposedItemCard({
  proposal,
  item,
  id,
  busy,
  onAccept,
  onDiscard,
}: {
  proposal: Proposal;
  item: ProposedItem;
  id: string;
  busy: boolean;
  onAccept: () => void;
  onDiscard: () => void;
}) {
  const whole = item.text !== "";
  return (
    <div className="g-proposed" id={id} tabIndex={-1}>
      <div className="g-item-head">
        <strong>{whole ? `Item ${item.key}` : `Suggested for item ${item.key}`}</strong>
        <AiMark inferred={item.inferred} />
        {whole && <span className="g-muted">weight {item.points}</span>}
        <span className="g-hint">not part of the rubric yet</span>
      </div>
      {whole && <pre className="g-pre">{item.text}</pre>}
      <Lines label="Acceptable forms" lines={item.accept} />
      <Lines label="Does not count" lines={item.reject} />
      {item.inferred && <p className="g-hint">{INFERRED[proposal.mode]}</p>}
      {item.note && <p className="g-warn">The AI notes: {item.note}</p>}
      {item.source_text && (
        <details className="g-orig">
          <summary>
            {proposal.mode === "example"
              ? `The item of version ${proposal.example_id} it was adapted from`
              : "The words of your material behind it"}
          </summary>
          <pre className="g-pre">{item.source_text}</pre>
        </details>
      )}
      <div className="g-row">
        <button className="g-primary small" disabled={busy} onClick={onAccept}>
          Accept
        </button>
        <button className="g-ghost small" disabled={busy} onClick={onDiscard}>
          Discard
        </button>
      </div>
    </div>
  );
}

/** A problem text the AI took from the pasted material, for a version that has none. */
export function ProposedText({
  proposal,
  id,
  busy,
  onAccept,
  onDiscard,
}: {
  proposal: Proposal;
  id: string;
  busy: boolean;
  onAccept: () => void;
  onDiscard: () => void;
}) {
  return (
    <div className="g-proposed" id={id} tabIndex={-1}>
      <div className="g-item-head">
        <strong>Problem text</strong>
        {proposal.version_text_found ? (
          <span className="g-mark">from your pasted material</span>
        ) : (
          <AiMark inferred />
        )}
        <span className="g-hint">not in the box yet</span>
      </div>
      <pre className="g-pre">{proposal.version_text}</pre>
      <p className="g-hint">
        {proposal.version_text_found
          ? "Found word for word in your pasted material."
          : "Not word for word what you pasted: the AI wrote this out. Compare it with your own text before you accept it."}
      </p>
      <div className="g-row">
        <button className="g-primary small" disabled={busy} onClick={onAccept}>
          Accept
        </button>
        <button className="g-ghost small" disabled={busy} onClick={onDiscard}>
          Discard
        </button>
      </div>
    </div>
  );
}

/** What can be accepted together: everything of a draft that is not inferred. */
export function acceptTogether(p: Proposal): { keys: string[]; text: boolean; count: number } {
  const keys = p.items.filter((i) => !i.inferred).map((i) => i.key);
  const text = p.version_text !== "" && p.version_text_found;
  return { keys, text, count: keys.length + (text ? 1 : 0) };
}

/** The head of a version's draft: where it came from, what the AI reported, and the whole-draft actions. */
export function ProposalHead({
  proposal,
  id,
  busy,
  onAcceptAll,
  onDiscardAll,
  onAgain,
}: {
  proposal: Proposal;
  id?: string;
  busy: boolean;
  onAcceptAll: () => void;
  onDiscardAll: () => void;
  onAgain: () => void;
}) {
  const together = acceptTogether(proposal);
  const alone =
    proposal.items.length - together.keys.length + (proposal.version_text && !together.text ? 1 : 0);
  return (
    <div className="g-proposal" id={id} tabIndex={-1}>
      <p>
        <strong>AI draft</strong> · {draftSource(proposal)} · {proposal.model}. Nothing here is part of the rubric
        until you accept it.
      </p>
      {proposal.stale && (
        <p className="g-warn">
          {proposal.mode === "example"
            ? `Version ${proposal.example_id}'s rubric or one of the two problem texts was changed after this draft was made.`
            : proposal.mode === "material"
              ? "Other material was pasted after this draft was made."
              : "The problem text was changed after this draft was made."}{" "}
          Draft again, or check it against the change.
        </p>
      )}
      {proposal.notes.length > 0 && (
        <>
          <p className="g-hint">The AI reports:</p>
          <ul className="g-details-list">
            {proposal.notes.map((note, i) => (
              <li key={i}>{note}</li>
            ))}
          </ul>
        </>
      )}
      {proposal.remarks.map((remark, i) => (
        <p className="g-hint" key={i}>
          {remark}
        </p>
      ))}
      <div className="g-row">
        {together.count > 1 && (
          <button className="g-primary small" disabled={busy} onClick={onAcceptAll}>
            Accept{" "}
            {together.text && together.keys.length === 0
              ? "the problem text"
              : `${plural(together.keys.length, "item")}${together.text ? " and the problem text" : ""}`}
          </button>
        )}
        <button className="g-ghost small" disabled={busy} onClick={onDiscardAll}>
          Discard the whole draft
        </button>
        <button className="g-ghost small" disabled={busy} onClick={onAgain}>
          Draft again
        </button>
      </div>
      {alone > 0 && (
        <p className="g-hint">
          {alone === 1 ? "One part is" : `${alone} parts are`} inferred: nothing of yours backs{" "}
          {alone === 1 ? "it" : "them"}. Decide on {alone === 1 ? "it" : "each one"} separately, below.
        </p>
      )}
    </div>
  );
}

/** The draft being written, and what could not be drafted. */
export function DraftingStatus({ drafting, onCancel }: { drafting: Drafting | null; onCancel: () => void }) {
  if (!drafting) return null;
  return (
    <div className="g-drafting" role="status">
      {drafting.running && (
        <div className="g-row between">
          <span>
            The AI is drafting{drafting.total > 1 ? `: ${drafting.done} of ${plural(drafting.total, "version")} done` : ""}
            . This can take a minute. You can keep working: nothing it writes is used until you accept it.
          </span>
          <button className="g-ghost small" onClick={onCancel}>
            Cancel
          </button>
        </div>
      )}
      {drafting.failed.map((f, i) => (
        <div className="g-warn" key={i}>
          {f.message}
          {f.detail && (
            <details>
              <summary>Details</summary>
              <pre className="g-pre">{f.detail}</pre>
            </details>
          )}
        </div>
      ))}
      {!drafting.running && (
        <div className="g-row">
          <button className="g-ghost small" onClick={onCancel}>
            OK
          </button>
        </div>
      )}
    </div>
  );
}
