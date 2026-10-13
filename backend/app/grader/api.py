"""FastAPI router for the grader, mounted by app.main at /grader/api.

Flow: create a problem bank (a Problem Info CSV, or one problem of the Chen example)
-> finish and approve its rubric -> attach responses -> start a run -> poll it ->
inspect cells, override, export.

The hard stops live here and in ``rubric``/``runner``, not only in the UI. Every
refusal answers with ``{"detail": {"message": <a plain sentence>, "boxes": [...]}}``:
the UI shows the sentence and the boxes still to fill, never the status code.
Ids chosen by the owner (versions, items, responses) travel in the request body,
never in the path or the query string, so they stay out of access logs.
In production Caddy puts /grader behind basic auth; DemoBuilder stays open.
"""

from __future__ import annotations

import csv
import io
import re
import time
import uuid
from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..config import LLM_PROFILE
from . import metrics, rubric, runner, store
from .adapters import (
    CHEN_SOURCE, CHEN_VERSIONS, NothingToLoad, chen_problems, chen_responses, kit_dir, load_chen,
    load_problem_info, parse_responses, problem_info_csv,
)
from .aggregate import HARD_FLAGS
from .grade import EFFORTS, PROMPT_VERSION, model_name
from .model import Assessment, Finding, Text, text_fingerprint

router = APIRouter(prefix="/grader/api", tags=["grader"])

GRADING_IN_PROGRESS = (
    "Grading is in progress for this problem bank. Wait for it to finish, or cancel it first."
)
UNREADABLE = "This saved problem bank cannot be read. It can only be deleted."


def _stop(status: int, message: str, boxes: list[dict] | None = None) -> HTTPException:
    return HTTPException(status, {"message": message, "boxes": boxes or []})


def _refusal(exc: rubric.Refusal) -> HTTPException:
    return _stop(422 if exc.kind in ("incomplete", "invalid") else 409, exc.message, exc.boxes)


def _assessment(aid: str) -> Assessment:
    try:
        return store.get_assessment(aid)
    except KeyError:
        raise _stop(404, "That problem bank was not found. It may have been deleted.")
    except Exception:   # the file is there but is not a bank we can read
        raise _stop(409, UNREADABLE)


def _working_copy(aid: str) -> Assessment:
    """A copy of the bank to change. It replaces the saved bank only once it has
    been written to disk, so a change that fails half-way changes nothing."""
    return _assessment(aid).model_copy(deep=True)


def _run(rid: str) -> runner.Run:
    try:
        return runner.get_run(rid)
    except KeyError:
        raise _stop(404, "That run was not found. It may have been deleted.")


def _held_count(a: Assessment) -> int:
    if not a.held_responses_csv:
        return 0
    try:
        return len(parse_responses(a.held_responses_csv, a)[0])
    except NothingToLoad:
        return 0


def _bank(a: Assessment, **extra) -> dict:
    """A bank as the UI sees it: the saved bank plus the live check. Held responses
    are counted, not shown: they stay untouched until the rubric is approved. Each
    response carries the fingerprint of its text, to line it up with a run's grades."""
    out = a.model_dump(exclude={"held_responses_csv"})
    for response, shown in zip(a.responses, out["responses"]):
        shown["fingerprint"] = text_fingerprint(response.text)
    out["held_responses"] = _held_count(a)
    out["check"] = rubric.check(a)
    out["running"] = runner.is_running(a.id)
    out.update(extra)
    return out


def _register(a: Assessment) -> Assessment:
    a.id = uuid.uuid4().hex[:12]
    a.created = time.time()
    return store.save_assessment(a)


def _change(aid: str, change: Callable[[Assessment], dict | None]) -> dict:
    """Apply one change to a bank, save it, and answer with the bank."""
    a = _working_copy(aid)
    try:
        extra = change(a) or {}
    except rubric.Refusal as exc:
        raise _refusal(exc)
    store.save_assessment(a)
    return _bank(a, **extra)


def _not_while_grading(aid: str) -> None:
    if runner.is_running(aid):
        raise _stop(409, GRADING_IN_PROGRESS)


def _require_approved(a: Assessment) -> None:
    """No responses are attached before the rubric is approved."""
    if a.kind != "bank":
        raise _stop(409, rubric.LEGACY_SENTENCE)
    if a.rubric_status != "approved":
        raise _stop(409, "Responses can be added once the rubric is approved.",
                    rubric.check(a)["to_fill"])


def _run_payload(run: runner.Run) -> dict:
    out = run.summary()
    out["cells"] = [c.compact() for c in run.cells.values()]
    return out


@router.get("/health")
async def health():
    try:
        model = model_name()
    except Exception as exc:  # e.g. Azure env vars missing
        model = f"unavailable ({type(exc).__name__})"
    return {
        "ok": True, "profile": LLM_PROFILE, "model": model, "prompt_version": PROMPT_VERSION,
        "kit": kit_dir() is not None, "efforts": list(EFFORTS), "hard_flags": list(HARD_FLAGS),
    }


@router.get("/examples")
async def examples():
    if kit_dir() is None:
        return []
    problems = chen_problems()
    return [{
        "id": "chen",
        "name": "Chen & Wan physics (PHY 2048)",
        "description": f"{sum(p['responses'] for p in problems)} de-identified student explanations "
                       f"of {len(problems)} physics problems, 3 binary rubric items each, graded "
                       "independently by two humans. It loads one problem at a time.",
        "problems": problems,
        "wordings": list(CHEN_VERSIONS),
    }]


# --- problem banks -----------------------------------------------------------

@router.get("/assessments")
async def list_assessments():
    """Saved problem banks, newest first. A saved file that cannot be read is listed
    as ``kind: "unreadable"``, so that it can still be deleted."""
    runs: dict[str, int] = {}
    for head in runner.summaries():
        runs[head.get("assessment_id", "")] = runs.get(head.get("assessment_id", ""), 0) + 1
    out = []
    for aid, a, mtime in store.list_assessments():
        out.append({
            "id": aid, "name": a.name if a else aid,
            "kind": "unreadable" if a is None else "bank" if a.kind == "bank" else "legacy",
            "source": a.source if a else "", "versions": len(a.problems) if a else 0,
            "items": len(a.criteria) if a else 0, "responses": len(a.responses) if a else 0,
            "runs": runs.get(aid, 0),
            "rubric_status": a.rubric_status if a else "draft",
            "rubric_revision": a.rubric_revision if a else 0,
            "created": (a.created if a else 0) or mtime,
        })
    return sorted(out, key=lambda b: b["created"], reverse=True)


class ProblemInfoIn(BaseModel):
    name: Text = ""
    csv: Text = ""
    responses_csv: Text = ""    # the all-at-once shortcut: held until the rubric is approved


@router.post("/assessments/problem-info")
async def assessment_from_problem_info(body: ProblemInfoIn):
    """A draft bank with findings. The only error is a file with nothing to load."""
    try:
        bank, _ = load_problem_info(body.csv, body.name)
    except NothingToLoad as exc:
        raise _stop(400, str(exc))
    if body.responses_csv.strip():
        try:
            parse_responses(body.responses_csv, bank)   # only to see that it holds responses
        except NothingToLoad as exc:
            raise _stop(400, str(exc))
        bank.held_responses_csv = body.responses_csv
    return _bank(_register(bank))


class ExampleIn(BaseModel):
    problem_id: Text = "Q2"
    wording: Text = "simple"


@router.post("/assessments/example")
async def assessment_from_example(body: ExampleIn):
    """One problem of the Chen example as a draft bank: the kit's rubric, no responses."""
    try:
        bank = load_chen(body.wording, problem=body.problem_id, with_responses=False)
    except FileNotFoundError:
        raise _stop(400, "The example data is not installed on this server.")
    except ValueError:
        raise _stop(400, "The example has no such problem or rubric wording.")
    return _bank(_register(bank))


@router.get("/assessments/{aid}")
async def get_assessment(aid: str):
    return _bank(_assessment(aid))


class VersionEdit(BaseModel):
    problem_id: Text
    new_id: Text | None = None
    text: Text | None = None
    instructions: Text | None = None


@router.put("/assessments/{aid}/version")
async def edit_version(aid: str, body: VersionEdit):
    def change(a: Assessment) -> None:
        rubric.edit_version(a, body.problem_id, body.new_id, body.text, body.instructions)

    return _change(aid, change)


class VersionAdd(BaseModel):
    id: Text = ""
    text: Text = ""
    instructions: Text = ""


@router.post("/assessments/{aid}/version")
async def add_version(aid: str, body: VersionAdd):
    return _change(aid, lambda a: {"added": rubric.add_version(a, body.id, body.text, body.instructions).id})


class VersionRef(BaseModel):
    problem_id: Text


@router.delete("/assessments/{aid}/version")
async def remove_version(aid: str, body: VersionRef):
    return _change(aid, lambda a: {"unassigned_responses": rubric.remove_version(a, body.problem_id)})


class ItemIn(BaseModel):
    key: Text = ""
    text: Text = ""
    points: float | None = None     # None = not set yet
    accept: list[Text] = Field(default_factory=list)
    reject: list[Text] = Field(default_factory=list)


class RubricIn(BaseModel):
    problem_id: Text
    items: list[ItemIn]


@router.put("/assessments/{aid}/rubric")
async def replace_rubric(aid: str, body: RubricIn):
    """Replace one version's items."""
    def change(a: Assessment) -> None:
        rubric.set_items(a, body.problem_id, [i.model_dump() for i in body.items])

    return _change(aid, change)


class CopyIn(BaseModel):
    problem_id: Text                # the version to copy from
    to: list[Text] | None = None    # default: every version without a rubric


@router.post("/assessments/{aid}/rubric/copy")
async def copy_rubric(aid: str, body: CopyIn):
    return _change(aid, lambda a: {"copied_to": rubric.copy_items(a, body.problem_id, body.to)})


@router.post("/assessments/{aid}/rubric/approve")
async def approve_rubric(aid: str):
    """422 with the boxes still to fill, else lock: hash, revision and warnings."""
    return _change(aid, lambda a: {"warnings": rubric.approve(a)["warnings"]})


@router.post("/assessments/{aid}/rubric/unlock")
async def unlock_rubric(aid: str):
    _not_while_grading(aid)
    return _change(aid, rubric.unlock)


# --- responses ---------------------------------------------------------------

def _set_responses(a: Assessment, responses: list, references: dict, findings: list[Finding]) -> dict:
    a.responses = responses
    a.references = references
    a.findings = [f for f in a.findings if f.scope != "responses"] + findings
    a.held_responses_csv = ""   # used, or set aside for another file: either way not kept
    store.save_assessment(a)
    return _bank(a, findings=[f.model_dump() for f in findings])


class ResponsesIn(BaseModel):
    csv: Text = ""
    use_held: bool = False


@router.post("/assessments/{aid}/responses")
async def attach_responses(aid: str, body: ResponsesIn):
    """Replace the bank's responses. Refused until the rubric is approved."""
    a = _working_copy(aid)
    _require_approved(a)
    _not_while_grading(aid)
    if body.use_held and not a.held_responses_csv:
        raise _stop(409, "No responses were uploaded together with this problem bank.")
    try:
        responses, findings = parse_responses(a.held_responses_csv if body.use_held else body.csv, a)
    except NothingToLoad as exc:
        raise _stop(400, str(exc))
    return _set_responses(a, responses, {}, findings)


@router.post("/assessments/{aid}/responses/example")
async def attach_example_responses(aid: str):
    """The kit's responses and human labels, for a bank made from the example."""
    a = _working_copy(aid)
    _require_approved(a)
    _not_while_grading(aid)
    if not a.source.startswith(CHEN_SOURCE):
        raise _stop(409, "Only a bank made from the worked example has example responses.")
    try:
        responses, references = chen_responses(a)
    except FileNotFoundError:
        raise _stop(400, "The example data is not installed on this server.")
    note = Finding(
        code="example_responses", scope="responses", filled="note", box="file",
        message=f"Loaded the example's {len(responses)} responses, each with the grades two "
                "human graders gave it.",
    )
    return _set_responses(a, responses, references, [note])


class ResponsesPatch(BaseModel):
    response_ids: list[Text] | None = None  # these responses, or ...
    version_label: Text | None = None       # ... every response uploaded with this label
    only_unknown: bool = False              # with version_label: skip responses that have a version
    problem_id: Text | None = None          # the version to give them ("" = none)
    excluded: bool | None = None            # leave them out of grading, or back in


@router.patch("/assessments/{aid}/responses")
async def edit_responses(aid: str, body: ResponsesPatch):
    """Give responses a version, or leave them out of grading."""
    a = _working_copy(aid)
    if a.kind != "bank":
        raise _stop(409, rubric.LEGACY_SENTENCE)
    _not_while_grading(aid)
    if body.response_ids is None and body.version_label is None:
        raise _stop(422, "Say which responses to change.")
    if body.problem_id and rubric.find_version(a, body.problem_id) is None:
        raise _stop(422, f'This bank has no version called "{body.problem_id}".')
    ids = set(body.response_ids or [])
    changed = 0
    for r in a.responses:
        by_label = (body.version_label is not None and r.version_label == body.version_label
                    and not (body.only_unknown and r.problem_id))
        if r.id not in ids and not by_label:
            continue
        if body.problem_id is not None:
            r.problem_id = body.problem_id
        if body.excluded is not None:
            r.excluded = body.excluded
        changed += 1
    store.save_assessment(a)
    return _bank(a, changed=changed)


def _filename(name: str, fallback: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")[:60] or fallback


@router.get("/assessments/{aid}/problem-info.csv")
async def problem_info(aid: str):
    """The bank's current rubric as a Problem Info CSV (long shape)."""
    a = _assessment(aid)
    return Response(
        problem_info_csv(a), media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="{_filename(a.name, "problem_bank")}_problem_info.csv"'},
    )


@router.delete("/assessments/{aid}")
async def delete_assessment(aid: str):
    """Remove the bank, its runs, and the cached samples tagged with it. A saved
    file that cannot be read is removed the same way."""
    if not store.has_assessment_file(aid):
        raise _stop(404, "That problem bank was not found. It may have been deleted.")
    _not_while_grading(aid)
    runs = runner.delete_runs_for(aid)
    samples = store.cache().delete_bank(aid)
    store.delete_assessment(aid)
    return {"deleted": aid, "runs": runs, "cached_samples": samples}


@router.delete("/cache")
async def clear_cache():
    """Remove every cached sample, for every bank. Answers still on their way back
    from a run that was just cancelled are not stored afterwards."""
    if runner.is_running():
        raise _stop(409, "Grading is in progress. Wait for it to finish, or cancel it first.")
    return {"deleted": store.cache().clear()}


# --- runs --------------------------------------------------------------------

class RunIn(BaseModel):
    assessment_id: Text
    k: int = Field(3, ge=1, le=9)
    effort: Text = "minimal"
    concurrency: int = Field(6, ge=1, le=32)
    problem_ids: list[Text] | None = None
    max_responses: int | None = Field(None, ge=1)


@router.post("/runs")
async def create_run(body: RunIn):
    """Start grading. 409 with the reasons unless the rubric is approved, there are
    responses, and every response that is not left out has a known version."""
    a = _assessment(body.assessment_id)
    if body.effort not in EFFORTS:
        raise _stop(422, f"Reasoning effort must be one of: {', '.join(EFFORTS)}.")
    _not_while_grading(a.id)
    config = runner.RunConfig(**body.model_dump(exclude={"assessment_id"}))
    try:
        run = runner.new_run(a, config)
    except rubric.Refusal as exc:
        raise _stop(409, exc.message, exc.boxes)
    runner.start(run, a)
    return _run_payload(run)


@router.get("/runs")
async def list_runs(assessment_id: str | None = None):
    """Runs without their cells, newest first: one bank's, or the 20 most recent."""
    heads = runner.summaries(assessment_id)
    return heads if assessment_id else heads[:20]


@router.get("/runs/{rid}")
async def get_run(rid: str):
    return _run_payload(_run(rid))


class CellIn(BaseModel):
    instance_id: Text


@router.post("/runs/{rid}/cell")
async def get_cell(rid: str, body: CellIn):
    """One grade with every vote. The instance id holds a response id, so it comes
    in the body: a query string would put it into the access log."""
    cell = _run(rid).cells.get(body.instance_id)
    if cell is None:
        raise _stop(404, "That grade was not found in this run.")
    return cell.model_dump()


class OverrideIn(BaseModel):
    instance_id: Text
    final: int | None = Field(None, ge=0, le=1)  # None clears the override


@router.post("/runs/{rid}/override")
async def override(rid: str, body: OverrideIn):
    run = _run(rid)
    if body.instance_id not in run.cells:
        raise _stop(404, "That grade was not found in this run.")
    return runner.set_override(rid, body.instance_id, body.final).compact()


@router.post("/runs/{rid}/cancel")
async def cancel(rid: str):
    _run(rid)
    return runner.cancel(rid).summary()


@router.get("/runs/{rid}/metrics")
async def run_metrics(rid: str):
    run = _run(rid)
    return metrics.evaluate_run(run, _assessment(run.assessment_id)) or {}


@router.get("/runs/{rid}/predictions.csv")
async def predictions_csv(rid: str):
    """The kit's harness format: instance_id, pred, confidence."""
    run = _run(rid)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["instance_id", "pred", "confidence"])
    for c in run.cells.values():
        if c.final is not None:
            w.writerow([c.instance_id, c.final, c.confidence])
    return Response(
        buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="predictions_{rid}.csv"'},
    )


@router.delete("/runs/{rid}")
async def delete_run(rid: str):
    """Remove one run: its grades and votes. The bank and cached samples stay."""
    run = _run(rid)
    if run.status == "running":
        raise _stop(409, "Cancel the run first.")
    runner.delete_run(rid)
    return {"deleted": rid}
