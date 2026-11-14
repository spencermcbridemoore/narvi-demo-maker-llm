// An explanations export -> the versions and answers exactly as the grader's web app
// would upload them, using the app's own browser code (src/grader/csv.ts): each ItemID
// is a version with its cleaned problem text; answers are cleaned of Canvas HTML
// (equation images kept as their LaTeX); names in the optional roster are replaced
// with [STUDENT]; ids become one-time ids (S001 ...) in file order. One answer per row
// of the export, in the same order.
//
// The output holds student answers: write it only to a git-ignored folder. Prints counts
// and the app's notes about the file, never answer text.
//
// Usage (used by backend/app/grader/batch.py; Node 22.6+):
//   node --experimental-strip-types frontend/scripts/prepare_explanations.mjs <export.csv> <out.json> [roster.txt]
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const [, , exportPath, outPath, rosterPath] = process.argv;
if (!exportPath || !outPath) {
  console.error("usage: prepare_explanations.mjs <export.csv> <out.json> [roster.txt]");
  process.exit(2);
}
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(`${frontend}/package.json`);
const { Window } = await import(pathToFileURL(require.resolve("happy-dom")).href);
const window = new Window();
globalThis.DOMParser = window.DOMParser;
globalThis.Node = window.Node;
const csv = await import(pathToFileURL(`${frontend}/src/grader/csv.ts`).href);

const text = new TextDecoder("utf-8", { fatal: true }).decode(readFileSync(exportPath));
const kind = csv.recognize(text);
if (kind.kind !== "explanations") {
  console.error(`Not an explanations export: ${kind.sentence}`);
  process.exit(1);
}
const roster = rosterPath ? readFileSync(rosterPath, "utf8").split(/\r?\n/) : [];
const prepared = csv.prepareExplanations(text, roster, true);
const versions = Object.fromEntries(csv.parseTable(prepared.versionsCsv).rows.slice(1).map(([id, problem]) => [id, problem]));
const answers = csv.parseTable(prepared.csv).rows.slice(1).map(([sid, item, answer]) => ({
  sid,
  item,
  original: prepared.idMap[sid] ?? sid,
  text: answer ?? "",
}));
writeFileSync(outPath, JSON.stringify({ sentence: kind.sentence, notes: prepared.notes, versions, answers }, null, 1));
console.log(kind.sentence);
for (const note of prepared.notes) console.log(note);
console.log(`Names replaced: ${prepared.redactions}.`);
