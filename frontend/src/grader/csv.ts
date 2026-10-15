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

// --- answers exported as HTML (Canvas and similar tools) ----------------------

// A closing tag, or a <br> or <img>: what real HTML has and plain text does not. An
// answer such as "a<b and b>c" has neither, so it is never treated as HTML.
const HTML_MARK = /<\/\s*(p|div|span|li|ol|ul|sub|sup|strong|em|b|i|u|h[1-6]|table|tr|td|th|blockquote|pre|code|a|font)\s*>|<\s*(br|img)\b[^>]*>/i;
const BLOCKS = new Set(["P", "DIV", "UL", "OL", "H1", "H2", "H3", "H4", "H5", "H6", "TABLE", "TR", "BLOCKQUOTE", "PRE"]);

export const looksLikeHtml = (text: string) => HTML_MARK.test(text);

/** A subscript or superscript as plain text: F_x, v_{max}, x^2. */
function script(mark: "_" | "^", inner: string): string {
  const t = inner.trim();
  if (!t) return "";
  return /^[A-Za-z0-9]$/.test(t) ? `${mark}${t}` : `${mark}{${t}}`;
}

/** An image as text: an equation image as its LaTeX, anything else by its description. */
function imageText(img: Element): string {
  const latex = img.getAttribute("data-equation-content") ?? "";
  if (latex.trim()) return `\\(${latex.trim()}\\)`;
  const alt = (img.getAttribute("alt") ?? "").trim();
  if (/^latex:/i.test(alt)) return `\\(${alt.replace(/^latex:\s*/i, "")}\\)`;
  return alt ? `[image: ${alt}]` : "[image]";
}

function render(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return (node.textContent ?? "").replace(/ /g, " ");
  if (node.nodeType !== Node.ELEMENT_NODE) return "";
  const el = node as Element;
  const tag = el.tagName.toUpperCase();
  if (tag === "IMG") return imageText(el);
  if (tag === "BR") return "\n";
  if (tag === "SCRIPT" || tag === "STYLE") return "";
  const inner = Array.from(el.childNodes).map(render).join("");
  if (tag === "SUB") return script("_", inner);
  if (tag === "SUP") return script("^", inner);
  if (tag === "LI") {
    const list = el.parentElement;
    const n = list?.tagName.toUpperCase() === "OL" ? Array.from(list.children).indexOf(el) + 1 : 0;
    return `\n${n ? `${n}. ` : "- "}${inner.trim()}\n`;
  }
  return BLOCKS.has(tag) ? `\n${inner}\n` : inner;
}

/**
 * An answer exported as HTML, as plain text the grader can read: equation images
 * become their LaTeX in \( \), subscripts and superscripts become _{} and ^{},
 * paragraphs, line breaks and list items become line breaks, and entities such as
 * &nbsp; become the characters they stand for. Text without real HTML is returned
 * exactly as it is, so a plain answer containing "<" is never damaged.
 */
export function htmlToText(text: string): string {
  if (!looksLikeHtml(text)) return text;
  const doc = new DOMParser().parseFromString(text, "text/html");
  return render(doc.body)
    .split("\n")
    .map((line) => line.replace(/[ \t]+/g, " ").trim())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

// --- what kind of file is this? ----------------------------------------------

export type FileKind = "problem_info" | "explanations" | "responses" | "rubric" | "bank" | "empty";
export type Recognized = { kind: FileKind; sentence: string };

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const PTEXT_COLS = ["problem_text", "problem_body", "problem_statement", "question_text", "prompt", "statement"];
const RUBRIC_COLS = ["rubric_item", "rubric_items", "criterion_text", "item_text", "criterion", "rubric", "weight", "points"];

/**
 * Says what a file is before anything is loaded, so that a rubric or a bank in YAML
 * is never read as a table of problems, and an explanations export is split into
 * versions and answers. Anything that is a table and not recognised otherwise is a
 * Problem Info CSV, read as softly as before.
 */
export function recognize(text: string): Recognized {
  const t = text.replace(/^﻿/, "");
  if (!t.trim()) return { kind: "empty", sentence: "The file is empty, so there is nothing to load." };
  if (/^rubrics\s*:/m.test(t) && !t.trimStart().split("\n", 1)[0].includes(",")) {
    const items = (t.match(/^\s*-\s+(id|item)\s*:/gm) ?? []).filter((m) => /id\s*:/.test(m)).length;
    return { kind: "rubric", sentence: `A rubric file${items ? ` with ${plural(items, "item")}` : ""}.` };
  }
  if (/^questions\s*:/m.test(t) && /^bank_info\s*:/m.test(t)) {
    const questions = (t.match(/^\s*-\s+(multiple_choice|essay|short_answer|numerical|true_false|matching)\s*:/gm) ?? []).length;
    return {
      kind: "bank",
      sentence: `A problem bank in YAML${questions ? ` with ${plural(questions, "question")}` : ""}. Reading it comes in the next update of the grader.`,
    };
  }
  const { rows } = parseTable(t);
  const header = (rows[0] ?? []).map(norm);
  const at = (names: string[]) => header.findIndex((h) => names.includes(h));
  const itemCol = at(["item_id"]);
  // "text" alone is too common a column name to mean "the students' answers" here.
  const answerCol = at(["response_text", "response", "answer", "explanation"]);
  if (itemCol >= 0 && at(["problem_body"]) >= 0 && answerCol >= 0) {
    const body = rows.slice(1);
    const versions = new Set(body.map((r) => (r[itemCol] ?? "").trim()).filter(Boolean)).size;
    return {
      kind: "explanations",
      sentence: `An explanations export: ${plural(body.length, "answer")} to ${plural(versions, "version")} of one problem.`,
    };
  }
  if (answerCol >= 0 && at(PTEXT_COLS) < 0 && at(RUBRIC_COLS) < 0) {
    return {
      kind: "responses",
      sentence: `A responses file with ${plural(rows.length - 1, "answer")}. Responses are added on the Responses step, once the rubric is approved.`,
    };
  }
  return { kind: "problem_info", sentence: "A Problem Info CSV." };
}

/** A chosen file, with what it was recognised as. */
export type Chosen = TextFile & Recognized;
export const choose = (f: TextFile): Chosen => ({ ...f, ...recognize(f.text) });

/** What the chosen files load together, or what is still missing, in a sentence. */
export type Plan = {
  problems: Chosen | null; // a Problem Info CSV or an explanations export
  rubric: Chosen | null; // becomes the rubric every version shares
  answers: Chosen | null; // a responses file, held until the rubric is approved
  unused: string[]; // files that are not loaded, and why
  ready: boolean;
  say: string;
};

const listed = (fs: Chosen[]) => {
  const names = fs.map((f) => f.name || "the pasted text");
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names.join("");
};

/**
 * Which of the chosen files are loaded, and how: one file with the problems (a
 * Problem Info CSV or an explanations export), at most one rubric file, and, with a
 * Problem Info CSV, at most one responses file. A bank in YAML or an empty file is
 * left out with its reason; anything else that does not fit is said before upload.
 */
export function planFor(files: Chosen[]): Plan {
  const of = (...kinds: FileKind[]) => files.filter((f) => kinds.includes(f.kind));
  const problems = of("problem_info", "explanations");
  const rubrics = of("rubric");
  const answers = of("responses");
  const plan: Plan = {
    problems: null,
    rubric: null,
    answers: null,
    // Each file is listed with its sentence, which says why; this only says it is left out.
    unused: of("bank", "empty").map((f) => `${f.name || "The pasted text"} is not loaded.`),
    ready: false,
    say: "",
  };
  if (!files.length) return plan;
  if (problems.length !== 1)
    return {
      ...plan,
      say: problems.length
        ? `Choose one file with the problems: ${listed(problems)} each have them.`
        : rubrics.length || answers.length
          ? "Add the file with the problems too: a Problem Info CSV or an explanations export."
          : "Nothing here has the problems. Choose a Problem Info CSV or an explanations export.",
    };
  if (rubrics.length > 1) return { ...plan, say: `Choose one rubric file: ${listed(rubrics)} are each a rubric.` };
  const [problem] = problems;
  const rubric = rubrics[0] ?? null;
  if (problem.kind === "explanations") {
    for (const f of answers)
      plan.unused.push(`${f.name} is not loaded. The explanations export already holds the answers.`);
    return {
      ...plan,
      problems: problem,
      rubric,
      ready: true,
      say:
        "Uploading loads each version with its problem text, and holds the answers until the rubric is approved." +
        (rubric ? " The rubric file becomes the rubric every version shares." : " The rubric comes on the next step."),
    };
  }
  if (answers.length > 1) return { ...plan, say: `Choose one responses file: ${listed(answers)} each have answers.` };
  const held = answers[0] ?? null;
  return {
    ...plan,
    problems: problem,
    rubric,
    answers: held,
    ready: true,
    say:
      `Uploading loads the problems${held ? ", and holds the responses until the rubric is approved" : ""}.` +
      (rubric ? " The rubric file becomes the rubric every version shares, unless the CSV has rubric items of its own." : ""),
  };
}

// --- an explanations export --------------------------------------------------

export type PreparedExplanations = Prepared & {
  versionsCsv: string; // problem_id, problem_text: one row per ItemID
  versions: number;
};

/**
 * An explanations export (ID, ItemID, ProblemBody, Explanation, and other columns
 * such as EarnedPoints) split, in the browser, into the bank's versions and its
 * answers. Each ItemID is a version, with its problem text cleaned. Each answer is
 * cleaned first, then its names are stripped and its id swapped as for any responses
 * file. Only the id, the ItemID and the answer leave the browser with the answers.
 */
export function prepareExplanations(text: string, roster: string[], pseudonymize: boolean): PreparedExplanations {
  const { rows } = parseTable(text);
  if (rows.length < 2) throw new Error("The explanations file has no answers, so there is nothing to load.");
  const header = rows[0];
  const names = header.map(norm);
  const at = (wanted: string[]) => names.findIndex((h) => wanted.includes(h));
  const idCol = at(ID_COLS);
  const itemCol = at(["item_id"]);
  const bodyCol = at(["problem_body"]);
  const answerCol = at(TEXT_COLS);
  const body = rows.slice(1);

  const versions = new Map<string, string>();
  let differing = 0;
  for (const r of body) {
    const item = (r[itemCol] ?? "").trim();
    const problem = htmlToText((r[bodyCol] ?? "").trim());
    if (!item) continue;
    if (!versions.has(item) || (!versions.get(item) && problem)) versions.set(item, problem);
    else if (problem && versions.get(item) !== problem) differing++;
  }
  const equations = body.reduce(
    (n, r) => n + ((r[answerCol] ?? "").match(/<img\b[^>]*data-equation-content/gi) ?? []).length,
    0,
  );
  const answers = toCsv([
    ["student_id", "problem_id", "response_text"],
    ...body.map((r) => [(r[idCol] ?? "").trim(), (r[itemCol] ?? "").trim(), htmlToText(r[answerCol] ?? "")]),
  ]);
  const prepared = prepareResponses(answers, roster, pseudonymize);

  const used = new Set([idCol, itemCol, bodyCol, answerCol]);
  const left = header.filter((h, i) => !used.has(i) && h.trim()).map((h) => `"${h.trim()}"`);
  const notes = [
    `Each of the ${plural(versions.size, "ItemID")} is a version, with the problem text the export gives for it.`,
    ...(differing ? [`${plural(differing, "answer")} came with a different problem text for the same ItemID; the first one is used.`] : []),
    ...(body.some((r) => looksLikeHtml(r[answerCol] ?? ""))
      ? [`Answers written as HTML were turned into plain text${equations ? `, keeping ${plural(equations, "equation")} as LaTeX` : ""}.`]
      : []),
    ...(left.length ? [`Not uploaded, and never left your browser: ${left.join(", ")}.`] : []),
  ];
  return {
    ...prepared,
    notes: [...notes, ...prepared.notes],
    versionsCsv: toCsv([["problem_id", "problem_text"], ...[...versions].map(([id, problem]) => [id, problem])]),
    versions: versions.size,
  };
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
