// Small pieces shared by the grader's steps.
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import type { Bank, Finding } from "./api";

/** One group of what is not the step's default action, shown in the step's options panel. */
export function SideBox({ title, children }: { title: string; children: ReactNode; open?: boolean }) {
  return (
    <section className="g-side">
      <h3 className="g-side-title">{title}</h3>
      <div className="g-side-body">{children}</div>
    </section>
  );
}

/**
 * A step: its default action, with what is not the default in an options panel that
 * stays hidden until asked for (a tab on the right edge opens it; Esc or Close shuts
 * it), so the step has the full width. ``nav`` adds a navigation column on the left.
 */
export function StepLayout({
  children,
  side,
  sideLabel = "More options",
  nav,
}: {
  children: ReactNode;
  side?: ReactNode;
  sideLabel?: string;
  nav?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const tab = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const close = useCallback(() => {
    setOpen(false);
    tab.current?.focus();
  }, []);
  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);
  return (
    <div className={nav ? "g-with-nav" : "g-stack"}>
      {nav}
      <div className="g-stack">{children}</div>
      {side && (
        <>
          <button
            ref={tab}
            className={`g-options-tab${open ? " open" : ""}`}
            aria-expanded={open}
            aria-controls="g-options"
            onClick={() => (open ? close() : setOpen(true))}
          >
            {sideLabel}
          </button>
          {open && (
            <aside id="g-options" className="g-options" aria-label={sideLabel} tabIndex={-1} ref={panel}>
              <div className="g-options-head">
                <h2>{sideLabel}</h2>
                <button className="g-ghost small" onClick={close}>
                  Close
                </button>
              </div>
              <div className="g-stack">{side}</div>
            </aside>
          )}
        </>
      )}
    </div>
  );
}

/**
 * The small mark on a box that was filled for the owner: by code from the file, or
 * with a problem text the AI took from pasted material. The sentence explains which.
 */
export function FilledMark({ finding }: { finding: Finding | undefined }) {
  if (!finding) return null;
  if (finding.code === "ai_version_text_draft")
    return (
      <span className="g-ai inferred" title={finding.message}>
        AI draft
      </span>
    );
  return (
    <span className="g-mark" title={finding.message}>
      {finding.code === "ai_version_text" ? "from your pasted material" : "filled in for you"}
    </span>
  );
}

/** A button that asks once more, in place, before doing something that cannot be undone. */
export function ConfirmButton({
  label,
  question,
  confirm,
  onConfirm,
  disabled,
}: {
  label: string;
  question: ReactNode;
  confirm: string;
  onConfirm: () => void | Promise<void>;
  disabled?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking)
    return (
      <button className="g-ghost small" disabled={disabled} onClick={() => setAsking(true)}>
        {label}
      </button>
    );
  return (
    <div className="g-confirm">
      <p>{question}</p>
      <div className="g-row">
        <button
          className="g-danger small"
          onClick={async () => {
            setAsking(false);
            await onConfirm();
          }}
        >
          {confirm}
        </button>
        <button className="g-ghost small" onClick={() => setAsking(false)}>
          Keep it
        </button>
      </div>
    </div>
  );
}

/** What deleting a saved bank removes, in plain words. */
export function deleteBankQuestion(name: string, kind: "bank" | "legacy" | "unreadable"): ReactNode {
  return (
    <>
      Delete <strong>{name}</strong>?{" "}
      {kind === "unreadable"
        ? "This removes the saved file, which cannot be read any more, and every run of it from the server."
        : "This removes the problem bank, its rubric, its responses and every run of it from the server."}{" "}
      {kind === "legacy"
        ? "Model answers cached for it by the earlier version are not linked to it, so they stay until you clear all cached model answers."
        : "Model answers cached for it are removed too."}{" "}
      This cannot be undone.
    </>
  );
}

/** Findings as a plain list for the "details" view, with the file row when there is one. */
export function FindingList({ findings }: { findings: Finding[] }) {
  if (!findings.length) return <p className="g-hint">Nothing was noticed in the file.</p>;
  return (
    <ul className="g-details-list">
      {findings.map((f, i) => (
        <li key={i}>
          {f.message}
          <span className="g-hint">
            {" "}
            {f.row ? `(file row ${f.row}) ` : ""}
            {f.code.startsWith("ai_")
              ? ""
              : f.filled === "code"
                ? "· filled in for you"
                : f.filled === "owner"
                  ? "· needs you"
                  : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Why a run stopped, when it did not finish by itself. An unexpected failure gets a
 * plain sentence; what the software reported goes under "Details".
 */
export function RunProblem({ run }: { run: { status: string; error: string | null } }) {
  if (!run.error) return null;
  if (run.status !== "error") return <p className="g-warn">{run.error}</p>;
  return (
    <div className="g-warn">
      Grading stopped because of an unexpected problem. Start it again: votes already given are kept.
      <details>
        <summary>Details</summary>
        <pre className="g-pre">{run.error}</pre>
      </details>
    </div>
  );
}

export const isLegacy = (bank: Bank) => bank.kind !== "bank";
/** A bank saved by the earlier version holds separate problems, not versions of one problem. */
export const unitWord = (legacy: boolean) => (legacy ? "problem" : "version");

export function statusText(bank: Bank): string {
  if (isLegacy(bank)) return "saved by an earlier version · view only";
  if (bank.rubric_status === "approved") return `rubric approved · revision ${bank.rubric_revision}`;
  return bank.rubric_revision > 0
    ? `rubric being edited · draft of revision ${bank.rubric_revision + 1}`
    : "rubric not approved yet";
}

export const fmtDate = (seconds: number) =>
  seconds ? new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
