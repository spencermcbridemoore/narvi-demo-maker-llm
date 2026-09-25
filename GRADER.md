# Rubric Grader (MVP)

A second tool that lives inside DemoBuilder for now, sharing its Azure access,
config and deployment. It grades free-text student responses against **binary**
rubric items (credit 1 / no credit 0). Each item is graded *k* times. The
majority vote is the grade, and how strongly the votes agree is the confidence.
A teacher reviews the uncertain or flagged items, and everything exports as CSV.

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

1. **Load exam**: the Chen & Wan example (pick the rubric wording), or upload two CSVs.
   Names you list are replaced with `[STUDENT]` **in the browser**, and student ids
   can be swapped for one-time ids (S001…). The id key never leaves the tab. Exports
   map ids back locally, and a reload loses the key.
2. **Rubric**: edit the wording the grader uses per item. The original is kept.
3. **Grade**: choose votes per item (k), reasoning effort, parallel calls and scope.
   Identical calls are cached, so re-runs are free and an interrupted run resumes.
4. **Review & export**: a students × items grid. Amber outline = needs review (vote
   agreement below the slider, or a hard flag). Red dashes = both human graders
   disagree with the final grade (example data only). Click a cell for every vote,
   its reasoning and quotes, and press 1 / 0 to decide. Exports: grades per
   student, detail per item, and `predictions.csv` in the kit's benchmark format.

Hard flags always go to review: `error`, `tie`, `quote_missing` (a quote the model
gave isn't in the response), `no_evidence` (credit without a quote),
`addresses_grader` ("note to the grader…").

### CSV formats

Rubric (one row per binary item; column names are matched loosely):

```
problem_id,problem_text,item,criterion_text,points
P1,Explain why ice floats.,1,Mentions density.,1
```

Responses (text only):

```
student_id,problem_id,response_text
jdoe,P1,"Ice is less dense than water, so it floats."
```

`problem_id` may be omitted when there is one problem. Sample files download from
the Load step.

## Benchmark from the terminal

Same runner, cache and scorer as the UI:

```
cd backend
../.venv/Scripts/python -m app.grader.cli --rubric simple --k 5
../.venv/Scripts/python -m app.grader.cli --rubric detailed_latest --problems Q2 --max-responses 5
```

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

Then open `https://<SITE_ADDRESS>/grader`. DemoBuilder stays open and unchanged.

Cost control: grading shares DemoBuilder's Azure deployments and TPM quota.
Keep "parallel calls" modest, or give the grader its own deployment later.

## Privacy status (read before real exams)

This MVP is for the consented, de-identified Chen data and for testing. Before
real, identifiable student work:

- Jetstream2's AUP bars FERPA-protected data without university authorization.
  Browser-side name stripping helps, but a student's own story can still identify
  them.
- Azure may keep flagged prompts for abuse review unless you're approved for
  modified abuse monitoring.
- The instance has a 4 GB swapfile, Docker logs aren't rotated, and runs and cached
  samples persist in `backend/app/data/grader/` until deleted
  (`DELETE /grader/api/runs/{id}`).

## Not in the MVP yet

Rubric *building* from free text (the LLM "compiler" and its "infer" checkbox),
splitting criteria into AND/OR/NOT checks, automatic purge, per-teacher accounts,
a separate Azure deployment, and file formats other than text CSV.
