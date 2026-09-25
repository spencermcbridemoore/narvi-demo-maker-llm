// Small CSV helpers plus the browser-side privacy step: names are stripped and ids
// swapped for one-time pseudonyms BEFORE anything is uploaded. The pseudonym key
// never leaves this tab; exports map ids back locally.

export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  const s = text.replace(/^﻿/, "");
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (quoted) {
      if (ch === '"') {
        if (s[i + 1] === '"') {
          field += '"';
          i++;
        } else quoted = false;
      } else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && s[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else field += ch;
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.some((v) => v.trim() !== ""));
}

export function toCsv(rows: (string | number | null | undefined)[][]): string {
  const cell = (v: string | number | null | undefined) => {
    const t = v === null || v === undefined ? "" : String(v);
    return /[",\r\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
  };
  return rows.map((r) => r.map(cell).join(",")).join("\n") + "\n";
}

const norm = (h: string) => h.trim().toLowerCase().replace(/[\s-]+/g, "_");
const ID_COLS = ["response_id", "id", "student_id", "student", "submission_id", "pseudonym"];
const TEXT_COLS = ["response_text", "response", "answer", "explanation", "text"];

function findCol(headers: string[], wanted: string[]): number {
  const n = headers.map(norm);
  for (const w of wanted) {
    const i = n.indexOf(w);
    if (i >= 0) return i;
  }
  return -1;
}

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
  csv: string;
  idMap: Record<string, string>; // pseudonym -> original id
  redactions: number;
  rows: number;
};

export function prepareResponses(csvText: string, roster: string[], pseudonymize: boolean): Prepared {
  const rows = parseCsv(csvText);
  if (rows.length < 2) throw new Error("Responses CSV needs a header row and at least one response.");
  const [headers, ...body] = rows;
  const idCol = findCol(headers, ID_COLS);
  const textCol = findCol(headers, TEXT_COLS);
  if (textCol < 0) throw new Error(`Responses CSV needs a text column (e.g. "response_text"); got: ${headers.join(", ")}`);

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
        if (orig.length >= 3) names.push(orig); // an id can itself be a name or email
      }
    }
  }
  const terms = redactionTerms(names);
  const pattern = terms.length
    ? new RegExp(`(?<![\\p{L}\\p{N}])(?:${terms.map(escapeRe).join("|")})(?![\\p{L}\\p{N}])`, "giu")
    : null;

  let redactions = 0;
  const out = body.map((r) => {
    const copy = [...r];
    if (pattern && copy[textCol]) {
      copy[textCol] = copy[textCol].replace(pattern, () => {
        redactions++;
        return "[STUDENT]";
      });
    }
    if (pseudonymize && idCol >= 0) copy[idCol] = byOriginal[(copy[idCol] ?? "").trim()] ?? copy[idCol];
    return copy;
  });
  return { csv: toCsv([headers, ...out]), idMap, redactions, rows: body.length };
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

export const SAMPLE_RUBRIC = toCsv([
  ["problem_id", "problem_text", "item", "criterion_text", "points"],
  ["P1", "Explain why ice floats on water.", "1", "Mentions density (explicitly or via mass per volume).", "1"],
  ["P1", "Explain why ice floats on water.", "2", "States that solid ice is less dense than liquid water.", "1"],
  ["P1", "Explain why ice floats on water.", "3", "Connects floating to buoyancy or displaced water. Just saying 'it is lighter' does not count.", "1"],
]);

export const SAMPLE_RESPONSES = toCsv([
  ["student_id", "problem_id", "response_text"],
  ["jdoe", "P1", "Ice is less dense than water because the molecules form a lattice, so it floats. - Jane Doe"],
  ["asmith", "P1", "It floats because it is lighter than water."],
  ["bkhan", "P1", "Ice has lower density than liquid water, so the buoyant force from the water it displaces holds it up."],
]);
