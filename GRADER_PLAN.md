# Rubric Grader: plan for the problem-bank round

Agreed 2026-10-12. This file is the spec for the work that follows the MVP in
[GRADER.md](GRADER.md). Update it when a decision changes; do not let it drift.

- **Status:** Phases 1 and 2 are merged and deployed (Phase 2: PR #6, 8fb1c8e, live since
  2026-10-14). Phase 2b, making Chen's real files load (section 10), comes in two PRs: first
  branch `grader-p2b-chen-files` (his Question 7 files; built, see 10.1), then his bank YAML
  and a Rubric step for 20 to 36 versions.
- **Each phase:** ends runnable, with tests, and waits for the owner's OK before the next.
  A fresh branch per phase, one PR per phase into `grader-mvp`.

## 1. Where the requirements come from

Dr. Chen (GitHub `Zhongzhou`) filed four issues on 2026-09-28. They are requirements.

| Issue | What he asks |
|---|---|
| [#1](https://github.com/spencermcbridemoore/narvi-demo-maker-llm/issues/1) | Grade one problem at a time, not a whole exam. Wording: "upload problem / problem bank and response", never "Load exam". "One problem" includes one drawn at random from a problem bank. Whole-exam grading comes later, on top. |
| [#2](https://github.com/spencermcbridemoore/narvi-demo-maker-llm/issues/2) | A **Problem Info CSV** replaces the rubric CSV: problem ids and problem text, plus optional rubric items and rubric score (weight). Every item is binary for now. All problems in one file are isomorphic versions. If no version has a rubric, the Rubric screen requires one for at least one version. If some versions lack one, ask whether to use AI: yes = generate from the existing rubric as the example and show every generated rubric; no = write them by hand, one at a time, with a "generate based on example" button. Only when every version has a rubric, ask for student responses. |
| [#3](https://github.com/spencermcbridemoore/narvi-demo-maker-llm/issues/3) | A settings panel (collapsible, or its own tab): number of votes, "fraction of test gradings", and a model chosen from a list, separately for rubric generation, grading and feedback generation. |
| [#4](https://github.com/spencermcbridemoore/narvi-demo-maker-llm/issues/4) | His sample arrived 2026-10-13 19:54 UTC as a comment: an explanations CSV (ID, ItemID, ProblemBody, EarnedPoints, Explanation; 250 answers to 20 versions) and "Question 7 Grading Rubric.yaml" (five binary items with specifications, one rubric for all versions, no weights). He also pointed to a 36-question multiple-choice bank in his public ESTELA repo. Phase 2b (section 10) makes these load. |

The owner adds one feature: paste unstructured material and get a draft rubric (Phase 2).

The owner's replies so far (2026-10-10), both unanswered:
- On #1: is an exam rubric built one problem at a time and then applied to completed exams,
  or do completed exams come in first and get split into problems? **Open. Phase 1 must
  work for either answer** (see principle 1).
- On #2: no hard "raise error" stops; say what is missing, and let a settings checkbox
  decide whether the model makes a best guess and reports where it filled in.

## 2. Principles (every phase)

1. **The unit is one problem bank:** one problem with one or more isomorphic versions. One
   session grades one bank. There are no exam-level features. A bank is self-contained, and
   responses reach it as plain rows (student, version, text). Whichever way Chen answers
   the #1 question, the exam layer sits above this seam: it either loops over banks, or
   splits exams into exactly these rows.
2. **Soft handling, loud by default.** An upload is never rejected because something is
   missing or odd. It loads what it can. What is still needed is then asked for where it
   belongs: the screen shows the boxes that make up the bank, the ones code could fill are
   filled and marked "filled in for you", and the empty ones are highlighted for the owner
   to fill.
   - Nothing passes a hard stop with a box still empty. The blocked button says why in
     plain words and takes the owner to the first empty box.
   - No silent skipping and no silent guessing.
   - No status codes, row numbers or file jargon on the default view. Those sit in a
     "details" side box for anyone fixing the file itself.
   - The only upload error is a file with nothing in it to load.
3. **Hard stops, enforced in the API and not only in the UI:**
   - no approval while the bank is incomplete (every version needs problem text and at
     least one item with text and a positive weight);
   - no approval while an AI draft is undecided or still being written (from Phase 2);
   - no responses attached before the rubric is approved;
   - no grading without an approved rubric and responses;
   - a response whose version is unknown cannot be graded.
4. **AI rules (from Phase 2).**
   - "Let the AI fill gaps" is a setting, off by default. Off: the AI drafts only when the
     owner presses a button, and Chen's question (#2 step 6) is one of those buttons. On:
     it drafts every empty rubric it can without asking (section 9.1).
   - AI output is a proposal. Nothing it writes is used until the owner accepts it, and it
     reports every place it filled in.
   - Everything the AI wrote is marked. Text that the owner's own material does not back is
     "inferred" and is accepted one by one. Code decides what is inferred, not the model
     (section 9.3).
   - The drafting model never sees student responses. Pasted text is data, not instructions.
   - Binary items only. Each item has a statement, acceptable forms, and explicit "does
     not count" exclusions (Chen's finding). The teacher's own wording is kept as
     `source_text`.
5. **UI rule.** The simple option is the default. Alternatives and extra options go in a
   collapsible side box. Chen's guided path is the default path.
6. **Words.** "Version" means an isomorphic version of the problem. A change to an approved
   rubric is a "revision". Never say "exam".
7. **Privacy.** Consented, de-identified data only, until the privacy checks in GRADER.md
   are done. Names are stripped and ids swapped for one-time ids in the browser. The
   grader stays closed by default behind the login.
8. **Old data keeps loading.** New fields get defaults. A saved file that cannot be read is
   skipped, not fatal.

## 3. Decisions (2026-10-12)

1. **Feedback generation:** built in Phase 5. Until then its model selector says "not built yet".
2. **Mirroring:** a generated rubric takes the example's item keys and weights from code,
   not from the model. Hand-made mismatches warn and never block.
3. **All-at-once upload:** demoted, not dropped. It lives in an "Other ways to start" side
   box with the worked example and, from Phase 2, the paste entry. It cannot skip approval:
   its responses are held until the rubric is approved.
4. **Small fixes in Phase 1:** delete endpoints for banks and the sample cache; a saved
   problem banks list with delete; the misleading deletion sentence in GRADER.md;
   `deploy/enable-grader.sh` cleans up after a failed validation and prints the real URL.
5. **Branches:** a fresh branch per phase off `grader-mvp`, one PR per phase into
   `grader-mvp`. Ask the owner before every commit, push and PR. ("Closes #N" will not
   auto-close issues: `main` is still the initial commit.)
6. **Questions for Chen:** the owner posts them (section 7).
7. **Deploy after Phase 1.** Ask before every change on the server.
8. **Version on each response:** a stop before grading, not an upload error. A run does
   not start until each such response has a version filled in, or is deliberately left out.
9. **Missing information is prompted for, not reported as an error** (confirmed
   2026-10-12): "here are the boxes we could fill in; you need to fill in the empty one."
   Friendly and non-technical, but a visible stop. Filling by hand is the default; letting
   the AI draft the empty boxes is the opt-in alternative from Phase 2, and its fills show
   in the same boxes, marked as proposals to accept.

Also agreed: rubric edits are "revisions"; the hash covers everything the grader is told,
and each run keeps a copy of the rubric it used; drafting has a third entry, "elaborate my
existing items"; drafts are proposals until accepted; the test batch extends the same run.

Phase 2 decisions (2026-10-13):

10. **What "let the AI fill gaps" means:** off, the AI drafts only when a button is
    pressed, and Chen's question is one of those buttons. On, it drafts every empty rubric
    it can as soon as it is switched on, including a rubric from the problem text alone,
    which is never drafted while it is off. From Phase 3 it also runs right after upload.
11. **Acceptable forms and exclusions in the Problem Info CSV:** two optional columns,
    `acceptable_forms` and `does_not_count`, one entry per line, written only when an item
    has any. It is Chen's file format, so it is also a question for him (section 7).
    *Reversed by decision 14 (2026-10-14): one `specifications` column.*
12. **Real model calls in Phase 2:** a small number, one per way in plus retries, five at
    most.
13. **Changes to the Phase 2 plan** (proposed with the design, and confirmed by the owner
    after the browser walk-through):
    - approval is refused while an AI draft is undecided or still being written;
    - "elaborate" covers one version at a time, and "from an example" carries accepted
      forms and exclusions over to the other versions, so that versions of one problem
      keep asking for the same thing;
    - what "elaborate" suggests is all inferred, so it is accepted item by item;
    - pasted material drafts one version per paste; more versions are added and drafted
      from it;
    - elaboration never runs by itself, whatever the switch says, until Phase 4 has
      measured it.

Phase 2b decisions (2026-10-14), after Chen's sample arrived:

14. **One `specifications` list per item**, as in Chen's rubric YAML, replaces the two lists
    (acceptable forms / does not count). Items saved with the two lists still load: their
    lines become specifications that start "Also counts:" or "Does not count:".
15. **A shared rubric is the default:** one rubric for all versions of a bank, as Chen's is
    written. Wording of its own for one version is the exception. In a Problem Info CSV,
    items given for one version only, or identical for every version, become the shared
    rubric, with a sentence saying so; items that differ between versions load as each
    version's own.
16. **Phase 2 stays live** (it was already deployed when this was decided). Phase 2b is
    deployed when it is done.
17. **Two PRs:** first his Question 7 files, then his bank YAML and a Rubric step for 20 to
    36 versions.
18. **A frontend test tool,** for development only (vitest with happy-dom), so the HTML
    cleaning and the file recognition in the browser are tested.
19. **Real calls:** up to five, on made-up fixtures. None with Chen's student answers; that
    is a Phase 4 decision.

Also agreed: EarnedPoints is not uploaded and not used until Chen says what it is for; his
explanations CSV is never committed (the repo is public), and fixtures are made-up files
with the same columns and markup.

## 4. Phases

| Phase | What | Done when |
|---|---|---|
| 1 | One problem bank, no AI (#1; #2 steps 1-5 and 7). Sections 5-6 below. | In the browser: upload a problem-info CSV with a partial rubric, finish it by hand, approve, add responses, grade, export. Existing tests pass, including the kit baseline check. Then deploy. |
| 2 | Rubric drafting (#2 step 6, plus the paste feature). One module, three ways in: (a) from an example version to the other versions; (b) from pasted unstructured material to a draft bank, downloadable as a Problem Info CSV; (c) elaborate existing items with acceptable forms and exclusions. Same output type and the same review screen. Turns on "let the AI fill gaps". Section 9 below. | A two-version bank with one rubric gets a generated rubric for the other, shown for approval. Pasting a problem plus solution gives a draft bank that can be edited, approved and downloaded. Unit tests use a fake model; a small number of real calls proves the path (decision 12). |
| 2b | Make Chen's real files load (#4), in two PRs. First: one specifications list per item, a shared rubric by default, his explanations export and his rubric YAML, and recognising a file before loading it. Second: his bank YAML and a Rubric step for 20 to 36 versions. Section 10. | First PR: his explanations CSV and Question 7 rubric give a 20-version bank whose answers are held until approval and whose rubric is shared by every version. Second PR: his bank YAML gives 36 versions, and the Rubric step stays usable. |
| 3 | Settings (#3): its own panel or tab. Votes, reasoning effort, parallel calls, test fraction, and a model per task from a server-provided list (gpt-5, gpt-5-mini; defaults: rubric gpt-5, grading gpt-5-mini, feedback gpt-5-mini). "Test fraction" = grade a random X% first, review, then grade the rest, by extending the same run. | A run uses the chosen grading model; drafting uses its model; "test batch, then the rest" does not pay twice for the test items while the rubric is unchanged. |
| 4 | Measure on the Chen kit: simple rubric vs AI-elaborated vs Chen's detailed. Tune on two problems, report on the third. Add Chen's #4 sample as a fixture when it arrives. | The three-way table, with counts, and a decision on the AI defaults. |
| 5 | Feedback generation, minimal, in Chen's format from the kit (`prompt_templates.csv`). | The third model setting does something. |
| 6 | Wrap up: GRADER.md, draft replies to the four issues for the owner to post, deploy. | — |

## 5. Phase 1 spec

### 5.1 Data model (`backend/app/grader/model.py`, new `rubric.py`)

An `Assessment` is now a problem bank and its `problems` are the versions. All new fields
have defaults so old files load.

- **`Assessment`** gains:
  - `kind`: `"bank"` for everything created from now on; missing means `"legacy"`.
  - `rubric_status`: `"draft"` or `"approved"`; `rubric_hash`; `rubric_revision`; `rubric_approved_at`.
  - `findings`: what intake noticed and what it filled in (5.2).
  - `held_responses_csv`: responses from the all-at-once shortcut, untouched until approval.
  - `ai_fill`: the "let the AI fill gaps" setting. Stored now, does nothing until Phase 2.
- **`Problem`** (a version): `text` may be empty after a soft upload. It is editable while
  the rubric is a draft.
- **`Criterion`** gains `accept`, `reject` (lists, used from Phase 2) and `origin`
  (`teacher`, `copied`, `ai`, `ai_inferred`; default `teacher`). `points` is the weight.
  The text sent to the model is the statement plus those lists; with empty lists it is
  exactly today's text, so existing cached samples stay valid.
- **`Response`** gains `version_label` (the version exactly as uploaded) and `excluded`.
  An empty `problem_id` means "version unknown".
- **`Run`** gains `rubric_hash`, `rubric_revision` and `rubric_copy` (the versions and
  items it graded with), and `response_fingerprints` (response id → a hash of the text it
  graded). A run grades a snapshot of the bank. Its grades are shown and exported only for
  responses that still have both that id and that text: the responses can be replaced,
  and one-time ids start again at S001 with every upload.
- **`Finding`**: `code`, `message` (a plain sentence), `filled` (`code` filled it, the
  `owner` must, or just a `note` for the details view), `scope` (rubric or responses),
  the `box` it belongs to with its ids, the `value` code filled in, and the file `row`.
- **`rubric.py`**: `check` (what still blocks approval, what blocks grading, warnings),
  `spec_hash`, `approve`, `unlock`, item and version edits, copy-between-versions.

The hash is over each version's id, text and instructions and each item's key, text,
accept, reject and weight: everything the grader is told.

### 5.2 Intake: load what you can, list what you found

`load_problem_info(csv_text, name)` returns a draft bank plus findings. It raises only
when there is nothing to load. Column names are matched loosely (case, spaces, dashes).
Tab- and semicolon-separated text is read too (text pasted from a spreadsheet is
tab-separated).

Long shape, one row per rubric item (here `v2` has no rubric yet):

```
problem_id,problem_text,rubric_item,weight
v1,"A ball of mass m...","Uses energy conservation",1
v1,,"Includes both PE terms",1
v2,"A ball is launched...",,
```

Wide shape, one row per version:

```
problem_id,problem_text,rubric_item_1,weight_1,rubric_item_2,weight_2
```

- Weight is also read from `rubric_score`, `score` or `points`. `instructions` is optional.
- The old rubric CSV (`problem_id, problem_text, item, criterion_text, points`) is the long
  shape under other names, so the same adapter reads it.
- If no column name is recognized, assume the order id, text, rubric item, weight, and say so.
- If Chen's sample (#4) puts a whole rubric in one cell with his `# Item n:` markers, split on them.
- A row without a problem id joins the version on the row above. If it brings a different
  problem text, it starts a new version, which code names. Version and item ids are
  compared ignoring case and spaces.
- When the file has no item-number column, items are numbered 1, 2, 3 in order. That is
  the format, not a gap: it is noted once in the details, not marked on every item.
  An item number the file gives is never taken by an item that has none.
- A first line without a comma, tab or semicolon means one column: nothing is split at
  commas. A file that ends inside a quoted cell still loads, with a finding saying so.

`parse_responses(csv_text, bank)` returns responses plus findings: `student_id, problem_id,
response_text`, matched loosely. It never raises for content. Responses whose version is
unknown are reported once per uploaded label, not once per response. If no column is
named like the answers, the unused column holding the most text is taken, and a finding
names it. When no column name is recognised and the first row is taken as column names,
the finding quotes that row, so a first response lost that way can be seen.

A **finding** records what was noticed, in a plain sentence; whether code filled it or
the owner must; and which box it belongs to (a version's text, an item, a weight, a
response's version), so the UI can show it in place. The file row is kept for the details
view only. Findings are history. The live `check` decides what currently blocks approval or
grading, and names the empty boxes, so the list shrinks as they are filled.

### 5.3 Who can fill which gap

| Gap | Plain code fills it (Phase 1) | AI may draft it (Phase 2+: when asked, or without asking while "let the AI fill gaps" is on; always held for acceptance) | Nobody can fill it for you |
|---|---|---|---|
| Problem id missing | Yes: `v1`, `v2`, ... in file order | | |
| Item number missing | Yes: 1, 2, ... | | |
| Weight missing | Yes: 1 | | |
| Student id missing | Yes: `R001`, ... | | |
| Response's version, bank has one version | Yes: that version | | |
| Response's version, several versions | Only an exact match, ignoring case and spaces | It may suggest one from the response text, each suggestion accepted by hand (later phase) | When the text does not reveal it: assign it or exclude the response |
| A version has no rubric, another has one | Copy the other version's items unchanged, on request (marked "copied") | Draft items adapted to that version from the example | |
| Items lack acceptable forms and exclusions | | Yes ("elaborate my existing items") | |
| No version has a rubric | | From pasted material; or, weakest, from the problem text alone, with every item marked "inferred" | Otherwise the owner writes one |
| Problem text missing | | It may extract it from pasted material. It never invents it | Otherwise the owner types it |
| Weight is not a positive number | No. The item is kept and flagged | No | The owner fixes it |
| Duplicate ids (version, item or student) | Kept, with a suffix, and flagged | | The owner removes or renames if wrong |
| File empty or unreadable | | | The one upload error |

### 5.4 Rubric lifecycle

Draft → approved (locked, hashed, revision + 1) → "Edit rubric" unlocks it as a draft of
the next revision. A revision is a change (principle 6): approving a rubric that is
identical to the last approved one keeps its revision number.

- Approval is refused, with the list of what is missing, until every version has problem
  text and at least one item, and every item has text and a positive weight.
- Versions whose item keys, counts or weights differ produce a warning, not a refusal.
- Unlocking is refused while a run is in progress. So is everything else that would
  change the bank under the run: replacing or reassigning responses, deleting the bank,
  clearing the cache, and starting a second run of the same bank. Responses stay
  attached across revisions. Earlier runs keep their own rubric copy and say so when it
  is not the current revision.

### 5.5 API (under `/grader/api`)

| Call | Behaviour |
|---|---|
| `GET /assessments` | Saved problem banks: id, name, kind, counts, status, created. A saved file that cannot be read is listed as kind `unreadable`, so that it can be deleted. |
| `POST /assessments/problem-info` | `{name, csv, responses_csv?}` → a draft bank with findings. 400 only when there is nothing to load. `responses_csv` is stored as held. |
| `POST /assessments/example` | `{problem_id, wording}` → a draft bank for Q1, Q2 or Q3 with the kit's rubric and no responses. |
| `GET /assessments/{id}` | The bank plus the live `check`. Held responses are counted, never returned. |
| `PUT /assessments/{id}/version`, `POST`, `DELETE` | Edit a version's id, text or instructions; add or remove a version. Draft only (409 when approved). 422 for a name another version already has (ignoring case and spaces) and for removing the last version. |
| `PUT /assessments/{id}/rubric` | `{problem_id, items}` replaces one version's items. Draft only. |
| `POST /assessments/{id}/rubric/copy` | Copies one version's items to versions without a rubric. No AI. Draft only. |
| `POST /assessments/{id}/rubric/approve` | 422 with what is missing, else locks and returns hash, revision, warnings. |
| `POST /assessments/{id}/rubric/unlock` | 409 while a run is in progress. |
| `POST /assessments/{id}/responses` | `{csv}` or `{use_held: true}`. 409 until approved. Replaces the current set; returns findings. Held responses are not kept afterwards, whichever was used. |
| `POST /assessments/{id}/responses/example` | The kit's responses and human labels, for an example bank. 409 until approved. |
| `PATCH /assessments/{id}/responses` | Assign a version to chosen responses, or to all with one uploaded label (`only_unknown` skips those that already have a version); exclude or include. |
| `GET /assessments/{id}/problem-info.csv` | The current rubric in the long shape. |
| `DELETE /assessments/{id}` | Removes the bank, its runs and its cached samples. 409 while a run is in progress. |
| `DELETE /cache` | Clears every cached sample. |
| `POST /runs` | 409, listing the reasons, unless the rubric is approved, there are responses, and every response that is not excluded has a known version. Stamps hash, revision and rubric copy. |
| `GET /runs?assessment_id=` | One bank's runs, without their cells (added so a bank can list its runs). |

Every refusal carries a plain sentence and the boxes still to fill, as
`{"detail": {"message": ..., "boxes": [...]}}`. The UI shows that sentence and those
boxes, never the status code.
Ids travel in the request body, never in the URL path (they may contain any character).
Removed: `POST /assessments/csv` (folded into `problem-info`) and `PATCH .../criteria`.
Run endpoints are otherwise unchanged, with one exception: the votes behind one grade are
fetched with `POST /runs/{id}/cell` and the instance id in the body. It holds a response
id, and in a query string it would reach the access log. Cached samples are tagged with
their bank id so they can be deleted with it; older rows are untagged and go only with
`DELETE /cache`. The tags are kept in a table of their own (`sample_banks`): the MVP's
`samples` table is not altered, so the server can be rolled back to the MVP build without
touching the cache file. An answer that comes back from the model after `DELETE /cache`
is not stored. Text that cannot be saved (half an emoji from a paste) is cleaned on the
way in, and a bank is written to disk before the copy in memory is replaced.

### 5.6 UI (`frontend/src/grader`)

Steps: **Problem → Rubric → Responses → Grade → Review & export.** Each step shows one
default action. Everything else is in a collapsed side box.

| Step | Default | Side box |
|---|---|---|
| Problem | "Upload a problem or problem bank" (Problem Info CSV), a name, a template link. | **Other ways to start:** the worked example (Q1, Q2 or Q3, and the wording); problem info and responses together (responses are held until approval); paste unstructured material ("not built yet"); saved problem banks (open, delete). |
| Rubric | One panel per version, made of boxes: problem text, each item, each weight. Boxes code filled carry a small "filled in for you" mark and stay editable. Empty boxes that are needed are highlighted, with a count at the top ("3 boxes still to fill") that jumps to the first. Add and remove items. **Approve & lock** stays off until nothing needed is empty, and says what is left in plain words. When locked: read-only, with "Edit rubric (starts revision N+1)". | **More options:** copy this rubric to versions without one; add or remove a version; download as Problem Info CSV; "let the AI fill gaps" ("not built yet"); details (file rows, ignored columns). |
| Responses | Locked until approval. Upload a responses CSV (one-time ids on). If responses are held: "Use the N responses uploaded at the start". For the example: "Load the kit's responses". Then counts per version. If any version is unknown: "We matched 40 responses to a version. 3 need one from you", grouped by uploaded label, each group with a version box to fill. | **Privacy options:** names to strip; the one-time-id switch; download the id key. **Other:** leave a response out of grading; details. |
| Grade | "Start grading" with the defaults. If anything blocks it, the reasons in plain words, each one a link to the box to fill. | **Run settings:** votes, effort, parallel calls, max responses. (They move to the settings panel in Phase 3.) |
| Review & export | The grid, grouped by version; the review slider and "Review next"; grades export. The drawer shows the rubric text the run used, and a note if the rubric has since been revised. | Compare with the human graders (example only); detail and benchmark exports; delete this bank's data. |

As built:
- A "filled in for you" mark shows until the first approval, and only while the box still
  holds what code put there. After that the boxes are the owner's.
- A button that is off because something is missing stays clickable: it takes the owner
  to the first empty box.
- Only the id, version and response columns of a responses file leave the browser. Other
  columns (a name, an email) are named in the upload notice and never sent.
- "Compare with the human graders" is off until ticked in the side box.
- Review shows and exports a run's grades only for responses that still have the id and
  the text that were graded. The others are counted in a note, not shown. A run that
  stopped early says how many grades are missing, and the export counts them per student.
- A file that is not UTF-8 (Excel's plain CSV) is read as Windows-1252 or UTF-16, with a
  note. A failed model call or a stopped run is described in a plain sentence; what the
  software reported sits under "Details".
- "Clear all cached model answers" sits under the saved problem banks. Each step also
  has "paste the CSV text instead" in its side box.

If no version has a rubric, the Rubric step says one is required for at least one version
(Chen's step 5). If some lack one, it says which, and offers writing by hand or the copy
option; the AI question (his step 6) arrives in Phase 2.

### 5.7 Old data

Saved files without `kind` are "legacy": listed, viewable and exportable, not editable.
They can be deleted. The UI calls their units "problems", because an MVP file held
several problems, not versions of one. Old runs show "rubric not recorded". On 2026-10-10
the server held 7 Chen-example assessments, 1 sample-CSV upload, 1 run and 27 cached
samples: no real student work.

### 5.8 Also in Phase 1

- CLI: `--problem Q1`; the whole-kit benchmark still works. The CLI approves the kit rubric
  in code, so every path goes through the same lifecycle.
- GRADER.md: correct the sentence that implies deleting a run removes the data.
- `deploy/enable-grader.sh`: remove `grader.caddy` if validation fails (on a re-run, put
  the previous working one back); print the real URL.

### 5.9 Tests

- Both CSV shapes; each soft case in 5.3 gives the expected fill or finding; an empty file
  is the only error.
- Each hard stop: approve (422), responses before approval (409), run without approval,
  without responses, or with an unknown version (409); edits and unlock while locked or
  running (409).
- The hash changes on any edit of text, weight or items, and not otherwise. A run records
  hash, revision and rubric copy.
- A saved old-format assessment and run still load. Deleting a bank removes its runs and
  its cached samples.
- The example loads one problem at a time; the kit baseline check is untouched.
- Frontend typecheck and build; then the browser walk-through in the Phase 1 "done when".

## 6. Environment notes for whoever builds this

- Windows, Git Bash. Python venv at `.venv`. Tests: `cd backend && ../.venv/Scripts/python -m pytest -q`.
  Frontend: `cd frontend && npx tsc -b && npm run build`. Run both servers with
  `python run.py`, or the `backend` and `frontend` entries in `.claude/launch.json`; the UI
  is at `http://localhost:5173/grader`.
- Norton's HTTPS scanning re-signs certificates on this PC. Installs need
  `--trusted-host pypi.org --trusted-host files.pythonhosted.org`; the app itself already
  uses `truststore`.
- Model calls cost money and share DemoBuilder's Azure quota. Tests use a fake model.
  Phase 1 needs at most one small real run.
- The kit lives in `grader_kit/rubric_grader_handoff/` and is git-ignored. Never commit it,
  and never read from or write to Chen's private repo.
- Grader code: `backend/app/grader/`, tests in `backend/tests/test_grader.py`, UI in
  `frontend/src/grader/`.

## 7. Open questions (waiting on Chen)

1. (#1, asked 2026-10-10) Is an exam rubric built one problem at a time and applied to
   completed exams, or are completed exams split into problems first?
2. (#2, to post) Problem Info CSV: one row per rubric item, or one row per problem with
   `rubric_item_1, weight_1, ...`? Both are accepted until his sample arrives.
3. (#2, to post) Will the responses file say which version each student answered?
4. (#3, to post) Does "fraction of test gradings" mean grading a random X% first so the
   instructor can review before the rest are graded?
5. (#2, to post) Is the soft-handling compromise acceptable in place of "raise error"?
6. (#2, to post) Should acceptable forms and "does not count" exclusions be columns of
   their own in the Problem Info CSV (`acceptable_forms`, `does_not_count`), or part of
   the rubric item cell, as in his detailed rubrics? Columns are written until he answers.
   *Superseded by decision 14 and question 11.*
7. (#4, to post) Is the Question 7 rubric used as is for all 20 versions, or is it the
   example for generating one per version?
8. (#4, to post) What is EarnedPoints for: grade everyone, or only those without full
   answer credit?
9. (#4, to post) For the multiple-choice bank: will students write explanations, and is a
   rubric coming?
10. (#4, to post) Will every export have these five columns?
11. (#4, to post) Is one specifications list also what the tool should write when the AI
    adds to a rubric?

## 8. Working rules

- Do not commit, push, open a PR, comment on GitHub, or change anything on the server
  without asking the owner first.
- Keep DemoBuilder working: the grader shares its backend process, its deploy and its
  Azure quota.

## 9. Phase 2 spec (as built)

Agreed 2026-10-13 (decisions 10 to 13) and built the same day on `grader-p2-drafting`.

### 9.1 The rule everything else follows

An AI draft is a **proposal**. It is stored next to the rubric (`Assessment.proposals`),
never in it. The check, the hash, runs and the CSV download do not read proposals, so
text the owner has not accepted cannot reach the grader. Accepting an item copies it into
the rubric through `rubric.accept_proposal`, which is the only way AI text gets there.

"Let the AI fill gaps" (decision 10): off, the AI drafts only when a button is pressed. On,
switching it on drafts every empty rubric it can: from the example where a version has
one; otherwise from the problem text alone, and then for the first version only (the
others are drafted from it once it is accepted). It never elaborates by itself, and never
drafts over a draft that is waiting. From Phase 3 it also becomes a default applied at
upload.

### 9.2 Three ways in, one module (`backend/app/grader/draft.py`)

| Way in (`mode`) | Works on | The model writes | Code decides | Accepted |
|---|---|---|---|---|
| (a) From an example version (`example`) | Versions without a rubric. Also versions whose own items lack acceptable forms or exclusions the example has ("carry over"). | Each item's statement, acceptable forms and exclusions, adapted to that version. | Keys, weights, order and the number of items are the example's. Forms and exclusions only where the example item has them, and no more of them than it has. An item the owner wrote keeps its statement and weight: only the lists it lacks are drafted. | All at once, or item by item |
| (b) From pasted material (`material`) | A new one-version bank, or one version without a rubric in an open bank. One version per paste. | Items, each with the words of the material that back it; the problem text, copied, when the version has none. | Every quote and the problem text are looked for in the pasted text. An item with no quote found is inferred. Items are numbered in order; the weight is the material's, else 1. At most 10 items. | Backed items and a problem text found word for word: together. Inferred ones: one by one |
| (b, weakest) From the problem text alone (`text`) | The same, with nothing pasted. Only while the switch is on. | Items. | Everything is inferred, whatever the model returns. | One by one |
| (c) Elaborate my items (`elaborate`) | One version that has items. | Acceptable forms and exclusions only (at most 5 each). | Statements, keys and weights stay the owner's: the model is not asked for them. Lines the item already has are not repeated. | Item by item: every suggestion is inferred |

Order of work for a bank with several versions: elaborate one version, accept what is
wanted, then carry it over to the others with (a). Elaborating each version separately
would give versions of one problem different acceptance rules.

### 9.3 What code decides, in detail

- **Inferred** means that nothing of the owner's backs the text. Code decides it: an item
  drafted from material is backed only by quotes that `draft.found_in` finds in the pasted
  text (the forgiving comparison the grader uses for its quotes: case, spacing and quote
  marks do not matter; a passage under 12 characters proves nothing). What (c) suggests
  and what is drafted from the problem text alone is always inferred. What (a) adapts from
  an item the owner already has is not.
- **One at a time.** `accept_proposal` takes an inferred item, or a problem text that was
  not found word for word, only when it is asked for on its own. In a call that asks for
  more, it is left waiting and named in `left`.
- **A draft never overwrites the owner.** A drafted item is refused if its place has been
  filled by hand since; suggested lists are refused if the item was reworded since
  (`ProposedItem.base`); a drafted problem text is refused if the box has text now. The
  call is refused before anything is changed.
- **A waiting draft is replaced only by a draft of the same kind** ("Draft again"). Asking
  for another kind for that version is refused, so nothing undecided disappears.
- **Stale.** Each draft carries a fingerprint of what it was made from. The screen says
  when the example's rubric or a problem text has changed since.
- **The model's key is only a label.** An answer for an item that does not exist is left
  out, and so is a second answer for the same item. Both are reported on the draft.
- **Marks.** An accepted statement has `origin` `ai` or `ai_inferred` until it is reworded
  (then it is the teacher's, as with `copied`). Accepted lines are kept in
  `Criterion.ai_lines`, and a line stays marked while it reads the same. A run's rubric
  copy records the origin. Every acceptance leaves a line in the bank's findings.

### 9.4 Data model

All new fields have defaults, so Phase 1 files load. The Phase 1 build ignores fields it
does not know, so it can still read Phase 2 files after a rollback.

- **`Proposal`** (one per version): `mode`, `problem_id`, `example_id`, `version_text` and
  `version_text_found`, `items`, `order`, `notes` (the AI's), `remarks` (what code noticed),
  `model`, `prompt_version`, `created`, `inputs` (the fingerprint).
- **`ProposedItem`**: `key`, `text` (empty when the item's own statement stays), `points`,
  `accept`, `reject`, `source_text` (what it restates), `inferred`, `note`, `base`.
- **`Assessment`** gains `proposals` and `material` (the pasted text, kept only until the
  rubric is approved, and never sent back to the browser). **`Criterion`** gains `ai_lines`.
- `check` gains `to_decide`: one entry per waiting draft item. Approval needs it empty.
- A draft in progress is kept in memory only. A restart loses a draft in flight and
  nothing else.

### 9.5 Prompts and the model

- `prompts/draft_system.txt` is shared; `draft_example.txt`, `draft_material.txt`,
  `draft_text.txt` and `draft_elaborate.txt` hold the task for each way in. Their hash,
  with the output schemas, is `DRAFT_PROMPT_VERSION`, recorded on every draft.
- The grading prompts are untouched: `PROMPT_VERSION` is still `21999e7c82`, so cached
  samples stay valid. A test pins that value.
- **No student work.** The prompt builders take versions and items, never a bank or a
  response. A test puts a marker in every response and checks that no prompt contains it.
- **Pasted text is data.** Each block of the owner's text is closed by a tag carrying a
  code made for that call, so the text cannot close it. The template is filled in one
  pass, so nothing inside the text is expanded. A pasted line that reads like an
  instruction to an AI is reported on the draft.
- Model: a new `rubric` role on the strong tier (gpt-5), reasoning effort `medium`
  (`GRADER_DRAFT_EFFORT`). One call per version, three at a time, three attempts.

### 9.6 API (under `/grader/api`)

| Call | Behaviour |
|---|---|
| `POST /assessments/material` | `{name, material}` → a draft bank (source `paste`) with one empty version, and drafting starts. 400 only for an empty paste. Material over 60,000 characters is cut, with a finding saying so. |
| `POST /assessments/{id}/drafts` | `{mode, problem_ids?, example_id?, material?}`. Starts a draft and answers at once. 409 when the rubric is locked, the bank is legacy, or grading or a draft is in progress. 422 with a plain sentence, and the boxes when there are any, when nothing can be drafted. Versions it has to skip are named in `drafting.failed`. |
| `POST /assessments/{id}/drafts/cancel` | Stops the draft, or clears what is left to say about one that failed. An answer still on its way back is not kept. |
| `POST /assessments/{id}/proposals/accept` | `{proposal_id, keys, version_text?}` → the bank, with `accepted`, `left` and `text_left`. Draft only. |
| `POST /assessments/{id}/proposals/discard` | `{proposal_id, keys?, version_text?}`. Without keys and without `version_text`: the whole draft. |
| `PUT /assessments/{id}/ai-fill` | `{on}`. Switching it on drafts as in 9.1. |
| `GET /assessments/{id}` | Adds `proposals` (each with `stale`), `drafting` (progress, and what could not be drafted), `ai` (what could be drafted now), `has_material`, and `check.to_decide`. |
| `POST /assessments/{id}/rubric/approve` | Also 422 while a draft is undecided, and 409 while one is being written. |
| `GET /assessments/{id}/problem-info.csv`, and upload | The columns `acceptable_forms` and `does_not_count`, one entry per line, written only when an item has any (decision 11). The wide shape reads `acceptable_forms_1` and so on. |
| `GET /health` | Adds `draft_model` and `draft_prompt_version`. |

Deleting a bank drops a draft that is still being written for it.

### 9.7 UI

- **Problem step.** "Paste unstructured material" in "Other ways to start" is live: a text
  box and "Draft a rubric from this". It says not to paste student work.
- **Rubric step, the question.** When versions lack a rubric and one has it: "Version v2
  has no rubric yet. Do you want the AI to draft it from version v1?" with "Yes, draft it"
  and "No, I will write it" (which goes to the empty box). Each empty version also has
  "Generate based on v1" next to "Write the first item" and "Copy".
- **Rubric step, the review.** A draft shows in its version's panel, dashed and violet,
  headed "AI draft ... Nothing here is part of the rubric until you accept it", with what
  the AI reported. Each item has Accept and Discard, what it was adapted from or the words
  of the material behind it, and "inferred" where that applies. Suggestions for an item
  the owner wrote show under that item. "Accept N items" takes everything that is not
  inferred.
- **New boxes.** Every item has "Acceptable forms and exclusions (optional)": two lists,
  one line per entry, editable by hand.
- **Counter and approval.** "N boxes still to fill" and "N AI drafts to accept or
  discard". Approve & lock stays off until both are zero and no draft is being written.
- **Side box.** The switch; which version to draft from; "Elaborate my existing items";
  "Draft from pasted material" for one version.
- **While drafting.** "The AI is drafting ..." with Cancel. It survives a reload: the page
  asks the server how far the draft is.
- The locked view and the Review drawer show the lists and the "AI draft" marks.

### 9.8 Tests and real calls

- `backend/tests/test_grader_draft.py`: 32 tests on a fake model, covering each way in,
  the rules in 9.3, the prompts (no response text; pasted text stays data), the refusals,
  the CSV columns, and the API flows of both "done when" cases, including grading with an
  accepted draft.
- Real calls, 2026-10-13, gpt-5 at `medium`, in the browser walk-through: from an example
  (about 20 s), elaborate (about 45 s), carry over (about 35 s), pasted material (about
  70 s), and the switch drafting from the problem text alone, with a page reload while it
  drafted (about 40 s). Five calls, the agreed limit; every output shape was accepted by
  Azure, and each draft was accepted or discarded through the screen.
- Not seen on screen with the real model: a failed call (the fake-model tests cover what
  the API answers).

### 9.9 Left open

- (a) was tried on a made-up two-version bank. *Correction (2026-10-14): this said Chen's
  sample had not arrived. It had, on 2026-10-13, during the build; the issues were checked
  only at the start. Phase 2b (section 10) handles it.*
- A discarded drafted item leaves a gap in the item numbers (1, 2, 4).
- The quote check is word for word: an equation the model retypes in another notation
  counts as inferred. That errs on the careful side.
- The pasted-material call is slow (about 70 s). `GRADER_DRAFT_EFFORT=low` shortens it;
  whether that costs quality has not been measured.
- The CLI cannot draft. Phase 4 needs it for the "AI-elaborated" arm.

## 10. Phase 2b spec: Chen's real files

Agreed 2026-10-14 (decisions 14 to 19). What his files are (checked on copies, never
committed):

- **Explanations export** (`..._explanations.csv`): `ID, ItemID, ProblemBody, EarnedPoints,
  Explanation`. 250 answers to 20 ItemIDs, 3 to 18 each, one ProblemBody per ItemID. The
  problem texts are plain text; the 20 versions are one physics problem told with different
  objects and numbers. 227 answers are Canvas HTML (`p`, `sub`, `sup`, `br`, lists, `span`,
  `&nbsp;`), and 57 of them hold 173 equation images, each with its LaTeX in
  `data-equation-content`. EarnedPoints is 0.0 or 2.0.
- **Rubric YAML** ("Question 7 Grading Rubric.yaml"): `judgment: binary` and `rubrics:`, a
  list of `id`, `item` and `specifications`. Five items, no weights, no versions.
- **Bank YAML** (ESTELA repo): `bank_info` and 36 `questions`, each `multiple_choice` with
  `id`, `title`, `points`, HTML `text` with `\( \)` math, a `figure`, `answers` (text and
  `correct`) and `feedback.general`. Twelve scenarios at three positions each.
- Before Phase 2b, the explanations CSV loaded as 250 versions (one per student), the rubric
  YAML as 29 empty versions and the bank YAML as 308; as answers to a 20-version bank, 0 of
  250 found a version.

### 10.1 First PR (`grader-p2b-chen-files`)

- **One specifications list (decision 14).** `Criterion.specifications` replaces `accept`
  and `reject`. The grader is told the statement, then "Specifications:" and one line per
  entry; an item without specifications is told its statement alone, as before, so cached
  samples stay valid and the grading prompt version stays `21999e7c82`. The rubric hash of
  an item without specifications is what it was. The CSV has one `specifications` column;
  the two Phase 2 columns are still read and converted. "Elaborate", "carry over" and pasted
  material write specifications.
- **Shared rubric (decision 15).** An item whose `problem_id` is empty is shared by every
  version. A version's own wording is an item of that version with the same key, and its
  weight is always the shared item's. What a version is graded with is the shared items,
  with its own wording in place of a shared item's, then any items of its own that are not
  shared. The grader, the hash, each run's rubric copy and the check use that. Banks saved
  before have only per-version items and behave as before. A new bank's rubric is shared.
  In a shared bank the AI can draft a version's own wording from the shared rubric, and
  "elaborate" works on the shared rubric with every version's problem text as context.
- **Recognising a file.** The upload box on the Problem step takes one or more files and
  names each kind in a sentence before anything loads: a Problem Info CSV, an explanations
  export, a rubric YAML, a bank YAML (recognised; read in the second PR), a responses file.
  A file of another kind is never loaded as versions; the server refuses YAML sent as a CSV.
- **Explanations export.** In the browser: versions are the ItemIDs, each with its cleaned
  ProblemBody; answers are cleaned, names stripped and ids swapped as before, and held
  until approval. EarnedPoints never leaves the browser.
- **Cleaning Canvas HTML, in the browser.** Only text with real HTML tags is changed, so
  "a < b" stays. Equation images become `\( LaTeX \)`; subscripts and superscripts become
  `_{...}` and `^{...}`; paragraphs, line breaks and list items become line breaks;
  `&nbsp;` becomes a space. Cleaning comes before name stripping.
- **Rubric YAML.** Read on the server. Item numbers from `id`, statements from `item`,
  specifications as written, weight 1 each marked "filled in for you", and a sentence when
  `judgment` is not binary. It becomes the shared rubric. It can be chosen together with
  the explanations export, or added on the Rubric step.

**As built (2026-10-14).** Besides the above:

- A bank without any rubric asks for it once ("The rubric needs at least one item. It is used
  for every version."), not once per version. A version's own wording of a shared item is
  that item: the item counts (`check.counts.items`, the saved-banks list) count it once.
- A rubric file used at upload replaces the upload's "no rubric items" notes with one
  sentence ("The rubric file gives 5 items: the rubric all 20 versions share."). On the
  Rubric step, replacing a rubric asks first, and drops the AI drafts and the notes about
  the items it replaces. A Problem Info CSV that has items of its own keeps them, and says
  the rubric file was not used.
- "Use these items for every version" (`POST .../rubric/share`, banks with a rubric per
  version): another version keeps, as its own wording, only the items it words differently.
- A version's own wording drafted by AI leaves out items the AI gives back word for word
  (a real gpt-5 call did that for a version whose problem the shared wording already fit);
  when every item comes back unchanged, there is no draft and the screen says "The AI found
  nothing to draft for version X. The shared wording fits this version as it is."
- The Problem step has one file box that takes several files; it replaces the side box's
  "problem info and responses together". A responses file is held with a Problem Info
  CSV, and left out (with a sentence) next to an explanations export, which holds the
  answers already. A bank YAML or an empty file is named and left out.
- The run drawer shows an item's specifications, and the two lists for runs made before.
- Browser-side tests: vitest and happy-dom, dev dependencies only (`npm test` in
  `frontend/`): HTML cleaning, recognising files, which files load together, and the
  explanations split, on the made-up fixtures.
- Checked locally on copies of his real files, with nothing uploaded or committed and only
  counts printed: 250 answers to 20 versions; 173 equations kept as LaTeX in 57 answers; no
  tag or entity left in any answer or problem text; only the id, the ItemID and the answer
  would be uploaded. With his rubric, the bank has 5 shared items (weight 1, 2 to 5
  specifications each) and can be approved at once.
- Real calls: three to gpt-5, on the made-up fixtures only (elaborating the shared rubric;
  a version's own wording, twice). Drafting prompt version `fb3edf4c79`; the grading prompt
  version is still `21999e7c82`.

### 10.2 Second PR

His bank YAML: one version per question (the question and its answer choices); which
choice is correct, and the feedback, kept as that version's drafting material and never
told to the grader; a sentence that the grader cannot see figures. A Rubric step for 20 to
36 versions: the shared rubric on top, a compact list of versions below, one open at a
time.
