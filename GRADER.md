# Rubric Grader (MVP)

A second tool that lives inside DemoBuilder for now, sharing its Azure access,
config and deployment. It grades free-text student responses against **binary**
rubric items (credit 1 / no credit 0). Each item is graded *k* times. The
majority vote is the grade, and how strongly the votes agree is the confidence.
A teacher reviews the uncertain or flagged items, and everything exports as CSV.

It works on **one problem bank at a time**: one problem, in one or more isomorphic
versions, with a rubric per version. The plan for the round that follows the MVP
is in [GRADER_PLAN.md](GRADER_PLAN.md); this file describes what is built.

- UI: `/grader` (same SPA as DemoBuilder, lazy-loaded)
- API: `/grader/api/*` (`backend/app/grader/api.py`)
- Code: `backend/app/grader/` (self-contained; only imports `app.config` and `app.llm`,
  so it can move to its own repo by copying the package)

## Run locally

```
python run.py                 # DemoBuilder + grader, same as always
# open http://localhost:5173/grader
```

To use the worked example, unzip the handoff kit so this path exists (it stays out
of git and out of Docker images):

```
grader_kit/rubric_grader_handoff/data/instances.csv
```

## Flow

Each step shows one default action. Everything else is in a collapsed side box.

1. **Problem**: upload a problem or problem bank as a Problem Info CSV. Side box:
   the Chen & Wan example (one problem at a time, pick the rubric wording), problem
   info and responses together (the responses are held until the rubric is
   approved), pasting the CSV text, and the saved problem banks (open, delete).
2. **Rubric**: one panel per version, made of boxes: problem text, each item, each
   weight. An upload is never rejected for what it lacks. Boxes that plain code
   could fill (a missing problem id, item number or weight) are filled and marked
   "filled in for you"; empty boxes that are needed are highlighted. **Approve &
   lock** stays off until nothing needed is empty. A locked rubric is read-only;
   "Edit rubric" unlocks it as a draft of the next revision. Side box: copy a rubric
   to versions without one, add or remove a version, download the rubric as a
   Problem Info CSV, and the file details.
3. **Responses**: locked until the rubric is approved. Names you list are replaced
   with `[STUDENT]` **in the browser**, student ids are swapped for one-time ids
   (S001…), and only the id, version and response columns are uploaded. The id key
   never leaves the tab. Exports map ids back locally, and a reload loses the key.
   A response whose version matches none of the bank's (exactly, ignoring case and
   spaces) is held with a version box to fill, or can be left out of grading.
4. **Grade**: starts only with an approved rubric, responses, and a version for
   every response that is not left out. Side box: votes per item (k), reasoning
   effort, parallel calls, max responses. Identical calls are cached, so re-runs
   are free and an interrupted run resumes.
5. **Review & export**: a students × items grid per version. Amber outline = needs
   review (vote agreement below the slider, or a hard flag). Click a cell for every
   vote, its reasoning and quotes, and press 1 / 0 to decide. Each run keeps a copy
   of the rubric it used and says so when the rubric has been revised since. If the
   responses are replaced later, a run's grades are shown and exported only for
   responses that still have the same id and the same text. Side
   box: the comparison with the human graders (example data only; red dashes = both
   humans disagree with the final grade), the detail and `predictions.csv` exports,
   and deleting the bank's data.

Hard flags always go to review: `error`, `tie`, `quote_missing` (a quote the model
gave isn't in the response), `no_evidence` (credit without a quote),
`addresses_grader` ("note to the grader…").

The hard stops are enforced by the API, not only by the screen: no approval while
a needed box is empty, no responses before approval, no grading without both, and
no grading of a response whose version is unknown.

### CSV formats

Column names are matched loosely (case, spaces, dashes). Tab-separated text pasted
from a spreadsheet works too. The only upload error is a file with nothing to load.

Problem Info, long shape (one row per binary rubric item; here `v2` has no rubric yet):

```
problem_id,problem_text,rubric_item,weight
v1,"A ball of mass m...","Uses energy conservation",1
v1,,"Includes both PE terms",1
v2,"A ball is launched...",,
```

Problem Info, wide shape (one row per version):

```
problem_id,problem_text,rubric_item_1,weight_1,rubric_item_2,weight_2
```

`weight` may also be called `rubric_score`, `score` or `points`; `instructions` is
optional. The MVP's rubric CSV (`problem_id, problem_text, item, criterion_text,
points`) is still read. All versions in one file are versions of the same problem.

Responses (text only):

```
student_id,problem_id,response_text
jdoe,v1,"Energy is conserved, so mgh = 1/2 k x^2."
```

`problem_id` is the version the student answered. It may be omitted when the bank
has one version. Templates download from the Problem and Responses steps.

Banks saved by the MVP (which held several problems each) still open: they can be
viewed and exported, not edited or graded, and their runs show "rubric not
recorded".

## Benchmark from the terminal

Same runner, cache and scorer as the UI:

```
cd backend
../.venv/Scripts/python -m app.grader.cli --rubric simple --k 5
../.venv/Scripts/python -m app.grader.cli --rubric detailed_latest --problem Q2 --max-responses 5
```

`--problem Q2` grades one problem as its own bank, the way the app loads the
example; without it the whole kit is graded. The kit's rubric is approved in code
first, so the CLI goes through the same lifecycle as the app.

It prints agreement with both human graders, next to the human-vs-human ceiling,
using the kit's own `tools/evaluate.py`. It writes `predictions.csv` under
`backend/app/data/grader/cli/`. `tests/test_grader.py` includes the kit's
milestone-0 check: re-scoring Chen's predictions through our pipeline reproduces
`baselines.json` exactly.

## Deploy (alongside DemoBuilder, same instance)

The grader ships with the normal DemoBuilder deploy. It is **closed by default**:
until you enable it, `/grader/api/*` isn't proxied.

```
cd /opt/app
git fetch && git checkout grader-mvp && git pull
mkdir -p grader_kit && unzip ~/rubric_grader_handoff_1.zip -d grader_kit   # optional example data
docker compose -f deploy/docker-compose.yml up -d --build
bash deploy/enable-grader.sh teacher      # asks for a password, adds the login, reloads Caddy
```

Then open the address the script prints (`https://<SITE_ADDRESS>/grader`).
DemoBuilder stays open and unchanged. If Caddy rejects the generated route, the
script removes it again, so a bad route cannot stop Caddy at the next restart.

Cost control: grading shares DemoBuilder's Azure deployments and TPM quota.
Keep "parallel calls" modest, or give the grader its own deployment later.

## Privacy status (read before real student work)

The grader is for the consented, de-identified Chen data and for testing. Before
real, identifiable student work:

- Jetstream2's AUP bars FERPA-protected data without university authorization.
  Browser-side name stripping helps, but a student's own story can still identify
  them.
- Azure may keep flagged prompts for abuse review unless you're approved for
  modified abuse monitoring.
- The instance has a 4 GB swapfile and Docker logs aren't rotated.
- Problem banks (problem text, rubric, responses), runs and cached model answers
  persist in `backend/app/data/grader/` until deleted. Nothing is purged
  automatically. What each delete call removes:
  - `DELETE /grader/api/runs/{id}` removes that one run (its grades and votes)
    and nothing else. The bank, its responses and the cached model answers stay.
  - `DELETE /grader/api/assessments/{id}` (in the UI: "Delete" on a saved problem
    bank) removes the bank with its responses, every run of it, and the cached
    model answers that were first stored for it.
  - `DELETE /grader/api/cache` ("Clear all cached model answers") removes every
    cached model answer, for every bank.
  - Two limits. A cached answer is tagged with the bank it was first stored for,
    so answers a bank reused from an identical earlier bank stay until that bank
    or the whole cache is deleted. Answers cached before this tagging existed are
    untagged and go only with `DELETE /grader/api/cache`.

## Not built yet

Rubric drafting by AI (from an example version, from pasted material, or by
elaborating existing items), the settings panel, and feedback generation: see the
phases in [GRADER_PLAN.md](GRADER_PLAN.md). Also not built: whole-exam grading on
top of problem banks, splitting criteria into AND/OR/NOT checks, automatic purge,
per-teacher accounts, a separate Azure deployment, and file formats other than
text CSV.
