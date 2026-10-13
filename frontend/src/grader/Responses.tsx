// Step 3: responses. Locked until the rubric is approved. Names are stripped and ids
// swapped for one-time ids in the browser before upload. A response whose version
// cannot be matched is held: it gets a version box to fill, or is left out.
import { useMemo, useState } from "react";

import { api, type Bank, type ResponseItem } from "./api";
import {
  describePrepared,
  download,
  NO_FILE,
  prepareResponses,
  readTextFile,
  TEMPLATE_RESPONSES,
  toCsv,
  type TextFile,
} from "./csv";
import { FindingList, SideBox, StepLayout, isLegacy } from "./parts";
import { displayId, errMsg } from "./util";

const LEAVE_OUT = " leave out"; // version names are trimmed, so none can equal this
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

type Props = {
  bank: Bank;
  idMap: Record<string, string>;
  exampleCount: number | null; // responses the worked example has for this bank
  onBank: (b: Bank) => void;
  onIdMap: (m: Record<string, string>) => void;
  onNotice: (m: string | null) => void;
  onError: (m: string) => void;
  onGoRubric: () => void;
  onNext: () => void;
};

export function ResponsesStep(props: Props) {
  const { bank, idMap, exampleCount, onBank, onIdMap, onNotice, onError, onGoRubric, onNext } = props;
  const [file, setFile] = useState<TextFile>(NO_FILE);
  const [pasted, setPasted] = useState("");
  const [roster, setRoster] = useState("");
  const [pseudonymize, setPseudonymize] = useState(true);
  const [browserNotes, setBrowserNotes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const legacy = isLegacy(bank);
  const approved = bank.rubric_status === "approved" && !legacy;
  const isExample = bank.source.startsWith("example:") && exampleCount !== null;
  const counts = bank.check.counts;
  const has = bank.responses.length > 0;
  const name = (r: ResponseItem) => displayId(idMap, r);

  // Responses still waiting for a version, grouped by the label they were uploaded with.
  const groups = useMemo(() => {
    const by = new Map<string, ResponseItem[]>();
    for (const r of bank.responses)
      if (!r.excluded && !r.problem_id) by.set(r.version_label, [...(by.get(r.version_label) ?? []), r]);
    return [...by.entries()];
  }, [bank.responses]);
  const matched = counts.responses - counts.excluded - counts.unknown_version;

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    try {
      await work();
    } catch (e) {
      onError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const attach = (source: TextFile) =>
    run(async () => {
      const prepared = prepareResponses(source.text, roster.split("\n"), pseudonymize);
      const latest = await api.attachResponses(bank.id, prepared.csv);
      const notes = [...prepared.notes, ...(source.note ? [source.note] : [])];
      onBank(latest);
      onIdMap(prepared.idMap);
      setBrowserNotes(notes);
      setFile(NO_FILE);
      setPasted("");
      onNotice(
        `Loaded ${plural(latest.responses.length, "response")}. ${describePrepared(prepared)}${source.note ? ` ${source.note}` : ""}`,
      );
    });
  const useHeld = () =>
    run(async () => {
      const latest = await api.useHeldResponses(bank.id);
      onBank(latest);
      onNotice(`Loaded the ${plural(latest.responses.length, "response")} uploaded at the start.`);
    });
  const loadExample = () =>
    run(async () => {
      const latest = await api.exampleResponses(bank.id);
      onBank(latest);
      onIdMap({});
      setBrowserNotes([]);
      onNotice(latest.findings[0]?.message ?? null);
    });
  const patch = (body: Parameters<typeof api.patchResponses>[1]) =>
    run(async () => onBank(await api.patchResponses(bank.id, body)));

  /** Read the chosen file, then clear the input so that the same file can be chosen again after editing it. */
  const chooseFile = (input: HTMLInputElement) => {
    const f = input.files?.[0];
    if (f) readTextFile(f).then(setFile);
    input.value = "";
  };
  // What code decided about the file as a whole (which column holds the answers, for
  // instance) is said on the page, not only in the details.
  const fileNotes = bank.findings.filter((f) => f.scope === "responses" && f.filled === "code" && f.box === "file");

  const uploadBox = (label: string) => (
    <>
      <label className="g-field">
        Responses CSV <span className="g-hint">student_id, problem_id (the version), response_text</span>
        <input type="file" accept=".csv,.tsv,.txt,text/csv" onChange={(e) => chooseFile(e.target)} />
      </label>
      <div className="g-row">
        <button className="g-primary" disabled={busy || !file.text} onClick={() => attach(file)}>
          {label}
        </button>
        {file.name && <span className="g-hint">{file.name}</span>}
      </div>
      <p className="g-hint">
        Before anything is uploaded, in your browser: names you list under Privacy options are replaced with [STUDENT]
        {pseudonymize ? ", student ids are swapped for one-time ids," : ","} and only the id, version and response
        columns are sent.
      </p>
    </>
  );

  const side = legacy ? undefined : (
    <>
      <SideBox title="Privacy options">
        <label className="g-field">
          Names to strip, one per line
          <span className="g-hint">replaced with [STUDENT] in your browser before anything is sent</span>
          <textarea rows={3} value={roster} onChange={(e) => setRoster(e.target.value)} placeholder={"Jane Doe\nAli Khan"} />
        </label>
        <label className="g-check">
          <input type="checkbox" checked={pseudonymize} onChange={(e) => setPseudonymize(e.target.checked)} />
          Replace student ids with one-time ids
        </label>
        <p className="g-hint">
          The key that maps one-time ids back to your ids stays in this tab. Exports use it; reloading the page loses it.
        </p>
        <div className="g-row">
          <button
            className="g-ghost small"
            disabled={Object.keys(idMap).length === 0}
            onClick={() => download("id_key.csv", toCsv([["one_time_id", "student_id"], ...Object.entries(idMap)]))}
          >
            Download the id key
          </button>
        </div>
      </SideBox>

      <SideBox title="Other">
        {approved && (
          <>
            <h4>Paste the CSV text instead</h4>
            <textarea
              className="g-paste"
              rows={4}
              value={pasted}
              onChange={(e) => setPasted(e.target.value)}
              placeholder={"student_id,problem_id,response_text\n…"}
              aria-label="Responses CSV text"
            />
            <div className="g-row">
              <button
                className="g-ghost small"
                disabled={busy || !pasted.trim()}
                onClick={() => attach({ ...NO_FILE, text: pasted })}
              >
                {has ? "Replace the responses with the pasted text" : "Use the pasted text"}
              </button>
              <button className="g-ghost small" onClick={() => download("responses_template.csv", TEMPLATE_RESPONSES)}>
                Download a template
              </button>
            </div>
          </>
        )}
        {approved && has && (
          <>
            <h4>Replace the responses with another file</h4>
            <p className="g-hint">
              The {plural(bank.responses.length, "response")} attached now are removed. Grades from earlier runs can
              then no longer be shown or exported for them, so export what you need first.
            </p>
            {uploadBox("Replace the responses")}
          </>
        )}
        {has && (
          <>
            <h4>Leave a response out of grading</h4>
            <p className="g-hint">A response that is left out is kept, but not graded. You can also change its version here.</p>
            <div className="g-table-wrap g-scroll">
              <table className="g-table">
                <thead>
                  <tr>
                    <th>Response</th>
                    <th>Version</th>
                    <th>Left out</th>
                  </tr>
                </thead>
                <tbody>
                  {bank.responses.map((r) => (
                    <tr key={r.id} title={r.text.slice(0, 300)}>
                      <td className="g-sid">{name(r)}</td>
                      <td>
                        <select
                          value={r.problem_id}
                          disabled={busy}
                          aria-label={`Version of ${name(r)}`}
                          onChange={(e) => patch({ response_ids: [r.id], problem_id: e.target.value })}
                        >
                          <option value="">not chosen</option>
                          {bank.problems.map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.id}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          type="checkbox"
                          checked={r.excluded}
                          disabled={busy}
                          aria-label={`Leave ${name(r)} out`}
                          onChange={(e) => patch({ response_ids: [r.id], excluded: e.target.checked })}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <h4>Details</h4>
        <p className="g-hint">What was noticed in the responses file, with file rows.</p>
        <FindingList findings={bank.findings.filter((f) => f.scope === "responses")} />
        {browserNotes.length > 0 && (
          <ul className="g-details-list">
            {browserNotes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
      </SideBox>
    </>
  );

  return (
    <StepLayout side={side}>
      {!approved && !has && (
        <section className="g-card">
          <h2>Responses</h2>
          {legacy ? (
            <p className="g-callout">{bank.check.to_fill[0]?.message}</p>
          ) : (
            <>
              <p className="g-callout">Responses can be added once the rubric is approved.</p>
              {bank.check.to_fill.slice(0, 3).map((b, i) => (
                <p className="g-muted" key={i}>
                  Still needed: {b.message}
                </p>
              ))}
              {bank.held_responses > 0 && (
                <p className="g-muted">
                  The {plural(bank.held_responses, "response")} you uploaded at the start are being held until then.
                </p>
              )}
              <div className="g-row">
                <button className="g-primary" onClick={onGoRubric}>
                  Go to the rubric
                </button>
              </div>
            </>
          )}
        </section>
      )}

      {approved && !has && (
        <section className="g-card">
          <h2>Responses</h2>
          {bank.held_responses > 0 ? (
            <>
              <p className="g-muted">You uploaded responses together with the problem. They were held until the rubric was approved.</p>
              <div className="g-row">
                <button className="g-primary" disabled={busy} onClick={useHeld}>
                  Use the {plural(bank.held_responses, "response")} uploaded at the start
                </button>
              </div>
              <details className="g-inner">
                <summary>Upload a different file instead</summary>
                <p className="g-hint">The responses uploaded at the start are then discarded.</p>
                {uploadBox("Upload responses")}
              </details>
            </>
          ) : isExample ? (
            <>
              <p className="g-muted">
                The worked example comes with {exampleCount} de-identified student responses, each graded by two people.
              </p>
              <div className="g-row">
                <button className="g-primary" disabled={busy} onClick={loadExample}>
                  Load the kit's responses
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="g-muted">
                One row per student: who, which version of the problem they answered, and what they wrote.
              </p>
              {uploadBox("Upload responses")}
            </>
          )}
        </section>
      )}

      {has && (
        <section className="g-card">
          <h2>Responses</h2>
          <p className="g-muted">
            {plural(bank.responses.length, "response")} attached
            {counts.excluded > 0 ? `, ${counts.excluded} left out of grading` : ""}.
          </p>
          {fileNotes.map((f, i) => (
            <p className="g-callout" key={i}>
              {f.message}
            </p>
          ))}
          <table className="g-table">
            <thead>
              <tr>
                <th>{legacy ? "Problem" : "Version"}</th>
                <th>{legacy ? "Responses" : "Responses to grade"}</th>
              </tr>
            </thead>
            <tbody>
              {bank.problems.map((p) => (
                <tr key={p.id}>
                  <td>{p.id}</td>
                  <td>{counts.per_version[p.id] ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {groups.length > 0 && (
            <div className="g-held">
              <p className="g-callout">
                We matched {plural(matched, "response")} to a version. {counts.unknown_version}{" "}
                {counts.unknown_version === 1 ? "needs" : "need"} one from you.
              </p>
              {groups.map(([label, members]) => (
                <div className="g-group" key={label}>
                  <div>
                    <strong>{label ? `Uploaded as "${label}"` : "No version given"}</strong>{" "}
                    <span className="g-muted">
                      · {plural(members.length, "response")} ({members.slice(0, 6).map(name).join(", ")}
                      {members.length > 6 ? ", …" : ""})
                    </span>
                  </div>
                  <label className="g-inline">
                    Version
                    <select
                      className="needed"
                      value=""
                      disabled={busy}
                      aria-label={`Version for responses ${label ? `uploaded as ${label}` : "with no version given"}`}
                      onChange={(e) =>
                        patch(
                          e.target.value === LEAVE_OUT
                            ? { version_label: label, only_unknown: true, excluded: true }
                            : { version_label: label, only_unknown: true, problem_id: e.target.value },
                        )
                      }
                    >
                      <option value="" disabled>
                        Choose…
                      </option>
                      {bank.problems.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.id}
                        </option>
                      ))}
                      <option value={LEAVE_OUT}>Leave {members.length === 1 ? "it" : "them"} out of grading</option>
                    </select>
                  </label>
                </div>
              ))}
              <p className="g-hint">Grading does not start until each of these has a version or is left out.</p>
            </div>
          )}

          {!legacy && !approved && (
            <p className="g-muted">
              The rubric is being edited, so these responses are waiting. New responses can be added once it is approved
              again.
            </p>
          )}
          {approved && (
            <div className="g-row">
              <button
                className={`g-primary${bank.check.can_grade ? "" : " off"}`}
                aria-disabled={!bank.check.can_grade}
                onClick={() => {
                  if (bank.check.can_grade) return onNext();
                  // Off: take the owner to the first version box that is still empty.
                  const box = document.querySelector<HTMLSelectElement>(".g-held select");
                  box?.scrollIntoView({ behavior: "smooth", block: "center" });
                  box?.focus({ preventScroll: true });
                }}
              >
                Continue to grading →
              </button>
              {!bank.check.can_grade && <span className="g-muted">Not yet: {bank.check.grade_blockers[0]?.message}</span>}
            </div>
          )}
        </section>
      )}
    </StepLayout>
  );
}
