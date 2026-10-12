// Small CSV helpers plus the browser-side privacy step: names are stripped and ids
// swapped for one-time pseudonyms BEFORE anything is uploaded, and only the columns
// the grader uses leave the browser. The pseudonym key never leaves this tab;
// exports map ids back locally.

const ONE_COLUMN = String.fromCharCode(31); // a separator no file contains

/**
 * Text pasted from a spreadsheet is tab-separated, so tabs and semicolons work too.
 * A first line without any separator means one column: nothing is split at commas.
 */
function separator(text: string): string {
  const first = text.trimStart().split("\n", 1)[0] ?? "";
  return first.includes("\t") ? "\t" : first.includes(",") ? "," : first.includes(";") ? ";" : ONE_COLUMN;
}

/**
 * Rows of cells, read the way backend/app/grader/adapters.py reads them: a quote mark
 * opens a quoted cell only at the start of the cell (anywhere else it is just a
 * character), and `unclosed` says the text ended inside a quoted cell.
 */
export function parseTable(text: string): { rows: string[][]; unclosed: boolean } {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let atStart = true; // nothing of the current cell has been read yet
  const s = text.replace(/^﻿/, "").replace(/\r\n?/g, "\n");
  const sep = separator(s);
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (quoted) {
      if (ch === '"') {
        if (s[i + 1] === '"') {
          field += '"';
          i++;
        } else quoted = false;
      } else field += ch;
    } else if (ch === '"' && atStart) {
      quoted = true;
      atStart = false;
    } else if (ch === sep) {
      row.push(field);
      field = "";
      atStart = true;
    } else if (ch === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      atStart = true;
    } else {
      field += ch;
      atStart = false;
    }
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return { rows: rows.filter((r) => r.some((v) => v.trim() !== "")), unclosed: quoted };
}

export const parseCsv = (text: string): string[][] => parseTable(text).rows;

export function toCsv(rows: (string | number | null | undefined)[][]): string {
  const cell = (v: string | number | null | undefined) => {
    const t = v === null || v === undefined ? "" : String(v);
    return /[",\r\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
  };
  return rows.map((r) => r.map(cell).join(",")).join("\n") + "\n";
}

export type TextFile = { text: string; name: string; note: string };
export const NO_FILE: TextFile = { text: "", name: "", note: "" };

/**
 * Read a chosen file as text. Excel's plain "CSV" is not UTF-8, so a file that is not
 * valid UTF-8 is read as UTF-16 (when it starts with that mark) or Windows-1252, and
 * a note says so: degree signs, Greek letters and curly quotes survive either way.
 */
export async function readTextFile(file: File): Promise<TextFile> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  try {
    return { text: new TextDecoder("utf-8", { fatal: true }).decode(bytes), name: file.name, note: "" };
  } catch {
    const utf16 =
      (bytes[0] === 0xff && bytes[1] === 0xfe) || (bytes[0] === 0xfe && bytes[1] === 0xff)
        ? bytes[0] === 0xff
          ? "utf-16le"
          : "utf-16be"
        : null;
    return {
      text: new TextDecoder(utf16 ?? "windows-1252").decode(bytes),
      name: file.name,
      note: `"${file.name}" is not saved as UTF-8, so we read it as ${utf16 ? "UTF-16" : "Windows-1252, the way Excel saves a plain CSV"}. If letters or symbols look wrong, save it as "CSV UTF-8" and upload it again.`,
    };
  }
}

// Column names are matched loosely, the same way as backend/app/grader/adapters.py.
const norm = (h: string) =>
  h
    .trim()
    .replace(/([a-z])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
const ID_COLS = ["response_id", "id", "student_id", "student", "submission_id", "pseudonym"];
const VERSION_COLS = [
  "problem_id",
  "version_id",
  "version",
  "problem_version",
  "problem",
  "question_id",
  "question",
  "q",
];
const TEXT_COLS = ["response_text", "response", "answer", "explanation", "text"];

function findCol(headers: string[], wanted: string[], used: Set<number>): number {
  const n = headers.map(norm);
  for (const w of wanted) {
    const i = n.findIndex((name, at) => name === w && !used.has(at));
    if (i >= 0) {
      used.add(i);
      return i;
    }
  }
  return -1;
}

/** A first row that holds a number or a sentence is data, not column names. */
const looksLikeData = (cells: string[]) =>
  cells.some((c) => {
    const t = c.trim();
    return t.length > 40 || t.split(/\s+/).length > 5 || (t !== "" && !Number.isNaN(Number(t)));
  });

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Name variants to strip: the full name and each part of 3+ characters. */
function redactionTerms(names: string[]): string[] {
  const terms = new Set<string>();
  for (const raw of names) {
    const name = raw.trim();
    if (name.length < 2) continue;
    terms.add(name);
    for (const part of name.split(/[\s,]+/)) if (part.length >= 3) terms.add(part);
  }
  // Longest first, so "Jane Doe" is replaced before "Jane".
  return [...terms].sort((a, b) => b.length - a.length);
}

export type Prepared = {
  csv: string; // student_id, problem_id, response_text: the only columns that are uploaded
  idMap: Record<string, string>; // pseudonym -> original id
  redactions: number;
  rows: number;
  notes: string[]; // plain sentences about what was assumed or left out
};

/**
 * Get a responses file ready for upload, in the browser. Soft like the server: it
 * reads what it can and says what it assumed. The only error is a file with no
 * responses in it.
 */
export function prepareResponses(csvText: string, roster: string[], pseudonymize: boolean): Prepared {
  const { rows, unclosed } = parseTable(csvText);
  if (rows.length === 0) throw new Error("The responses file is empty, so there is nothing to load.");
  const headers = rows[0];
  let body = rows.slice(1);
  const notes: string[] = [];
  const width = Math.max(...rows.map((r) => r.length));
  const holdsText = (i: number, from: string[][]) => from.some((r) => (r[i] ?? "").trim() !== "");

  const used = new Set<number>();
  let idCol = findCol(headers, ID_COLS, used);
  let versionCol = findCol(headers, VERSION_COLS, used);
  let textCol = findCol(headers, TEXT_COLS, used);
  let named = true; // does the first row hold column names?
  if (used.size === 0) {
    const n = headers.length;
    [idCol, versionCol, textCol] = n >= 3 ? [0, 1, 2] : n === 2 ? [0, -1, 1] : [-1, -1, 0];
    named = !looksLikeData(headers);
    if (!named) body = rows;
    const order = n >= 3 ? "student id, version, response" : n === 2 ? "student id, response" : "response";
    // Quote the row that was taken as column names, so a first response lost that way can be seen.
    const taken = headers
      .filter((h) => h.trim())
      .map((h) => `"${h.trim().slice(0, 30)}"`)
      .join(", ");
    notes.push(
      named
        ? `We did not recognise the column names (${taken}), so we read the columns in this order: ${order}. If that first row is a response and not column names, add a row of column names above it and upload again.`
        : `We did not recognise any column names, so we read the columns in this order: ${order}. The first row looked like a response, so we loaded it too.`,
    );
  } else if (textCol < 0) {
    // No column is named like the answers: take the spare column holding the most text, and say so.
    const spare = Array.from({ length: width }, (_, i) => i).filter((i) => !used.has(i) && holdsText(i, body));
    if (spare.length === 0)
      throw new Error(
        "We could not find the students' answers in the responses file. It needs a column holding what each student wrote, for example response_text.",
      );
    const amount = (i: number) => body.reduce((n, r) => n + (r[i] ?? "").trim().length, 0);
    textCol = spare.reduce((best, i) => (amount(i) > amount(best) ? i : best), spare[0]);
    const label = (headers[textCol] ?? "").trim();
    notes.push(
      `We read ${label ? `the column "${label}"` : "a column without a name"} as the students' answers` +
        (spare.length > 1 ? ", because it holds the most text." : "."),
    );
  }
  if (body.length === 0)
    throw new Error("The responses file has column names but no responses, so there is nothing to load.");
  if (unclosed)
    notes.push(
      "The responses file ends inside a quoted answer: an opening quote mark has no closing one, so everything after it was read as one answer. Check the last responses.",
    );

  // Only the id, version and response columns are uploaded. Say which others stayed behind.
  const kept = new Set([idCol, versionCol, textCol]);
  const others = Array.from({ length: width }, (_, i) => i).filter((i) => !kept.has(i) && holdsText(i, body));
  const withName = others.filter((i) => named && (headers[i] ?? "").trim()).map((i) => `"${headers[i].trim()}"`);
  const unnamed = others.length - withName.length;
  if (others.length)
    notes.push(
      `Not uploaded, and never left your browser: ${[
        ...withName,
        ...(unnamed ? [`${unnamed} column${unnamed === 1 ? "" : "s"} without a name`] : []),
      ].join(", ")}.`,
    );

  const idMap: Record<string, string> = {};
  const byOriginal: Record<string, string> = {};
  const names = [...roster];
  if (pseudonymize && idCol >= 0) {
    for (const r of body) {
      const orig = (r[idCol] ?? "").trim();
      if (orig && !(orig in byOriginal)) {
        const alias = `S${String(Object.keys(byOriginal).length + 1).padStart(3, "0")}`;
        byOriginal[orig] = alias;
        idMap[alias] = orig;
        // An id can itself be a name or an email, so it is stripped from the answers
        // too. A plain number is not: it would also hit the numbers in the answers.
        if (orig.length >= 3 && /\p{L}/u.test(orig)) names.push(orig);
      }
    }
  }
  const terms = redactionTerms(names);
  const pattern = terms.length
    ? new RegExp(`(?<![\\p{L}\\p{N}])(?:${terms.map(escapeRe).join("|")})(?![\\p{L}\\p{N}])`, "giu")
    : null;

  let redactions = 0;
  const out = body.map((r) => {
    let text = r[textCol] ?? "";
    if (pattern)
      text = text.replace(pattern, () => {
        redactions++;
        return "[STUDENT]";
      });
    const id = idCol >= 0 ? (r[idCol] ?? "").trim() : "";
    const line: string[] = [];
    if (idCol >= 0) line.push(pseudonymize ? (byOriginal[id] ?? id) : id);
    if (versionCol >= 0) line.push((r[versionCol] ?? "").trim());
    line.push(text);
    return line;
  });
  const header = [...(idCol >= 0 ? ["student_id"] : []), ...(versionCol >= 0 ? ["problem_id"] : []), "response_text"];
  return { csv: toCsv([header, ...out]), idMap, redactions, rows: body.length, notes };
}

/** What happened in the browser before the upload, as one plain paragraph. */
export function describePrepared(p: Prepared): string {
  const ids = Object.keys(p.idMap).length;
  const parts = [
    `In your browser, before upload: ${p.redactions} name occurrence(s) replaced with [STUDENT]` +
      (ids
        ? `, and ${ids} student ids swapped for one-time ids. The id key exists only in this tab: exports map ids back, and reloading the page loses it.`
        : "."),
    ...p.notes,
  ];
  return parts.join(" ");
}

export function download(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// A Problem Info CSV in the long shape: one row per rubric item, one or more versions
// of the same problem. Weight is optional (1 when left out).
export const TEMPLATE_PROBLEM_INFO = toCsv([
  ["problem_id", "problem_text", "rubric_item", "weight"],
  [
    "v1",
    "A block of mass m is released from rest at height h above a spring of constant k. Explain how you would find the maximum compression of the spring.",
    "States that mechanical energy is conserved, or writes the conservation of energy equation.",
    "1",
  ],
  ["v1", "", "Includes both gravitational and elastic potential energy.", "1"],
  [
    "v2",
    "A ball of mass m is dropped from height h onto a vertical spring of constant k. Explain how you would find how far the spring compresses.",
    "States that mechanical energy is conserved, or writes the conservation of energy equation.",
    "1",
  ],
  ["v2", "", "Includes both gravitational and elastic potential energy.", "1"],
]);

export const TEMPLATE_RESPONSES = toCsv([
  ["student_id", "problem_id", "response_text"],
  ["jdoe", "v1", "Energy is conserved, so mgh at the top equals the spring energy 1/2 k x^2 at the bottom. - Jane Doe"],
  ["asmith", "v2", "I used F = ma to find the acceleration of the ball."],
  ["bkhan", "v2", "Gravitational potential energy mg(h + x) turns into elastic potential energy 1/2 k x^2."],
]);
