"""FastAPI router for the grader, mounted by app.main at /grader/api.

Flow: create a problem bank (a Problem Info CSV, one problem of the Chen example, or
pasted material) -> finish and approve its rubric, by hand or by accepting AI drafts
-> attach responses -> start a run -> poll it -> inspect cells, override, export.

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
from . import draft, metrics, rubric, runner, store
from .adapters import (
    CHEN_SOURCE, CHEN_VERSIONS, NothingToLoad, chen_problems, chen_responses, kit_dir, load_chen,
    load_problem_info, parse_responses, problem_info_csv, read_rubric_yaml,
)
from .aggregate import HARD_FLAGS
from .grade import EFFORTS, PROMPT_VERSION, model_name
from .model import Assessment, Finding, Text, every_version, text_fingerprint

router = APIRouter(prefix="/grader/api", tags=["grader"])

GRADING_IN_PROGRESS = (
    "Grading is in progress for this problem bank. Wait for it to finish, or cancel it first."
)
DRAFTING_IN_PROGRESS = (
    "The AI is still drafting for this problem bank. Wait for it to finish, or cancel it first."
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
    response carries the fingerprint of its text, to line it up with a run's grades.
    Each AI draft says whether what it was made from has changed since (``stale``);
    ``drafting`` is the draft being written, and ``ai`` what could be drafted now."""
    out = a.model_dump(exclude={"held_responses_csv", "material"})
    for response, shown in zip(a.responses, out["responses"]):
        shown["fingerprint"] = text_fingerprint(response.text)
    for proposal, shown in zip(a.proposals, out["proposals"]):
        shown["stale"] = draft.is_stale(a, proposal)
    out["held_responses"] = _held_count(a)
    out["has_material"] = bool(a.material)
    out["check"] = rubric.check(a)
    out["running"] = runner.is_running(a.id)
    out["drafting"] = draft.status(a.id)
    out["ai"] = draft.options(a)
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


def _not_while_drafting(aid: str) -> None:
    if draft.is_drafting(aid):
        raise _stop(409, DRAFTING_IN_PROGRESS)


def _require_approved(a: Assessment) -> None:
    """No responses are attached before the rubric is approved."""
    if a.kind != "bank":
        raise _stop(409, rubric.LEGACY_SENTENCE)
    if a.rubric_status != "approved":
        check = rubric.check(a)
        raise _stop(409, "Responses can be added once the rubric is approved.",
                    check["to_fill"] + check["to_decide"])


def _run_payload(run: runner.Run) -> dict:
    out = run.summary()
    out["cells"] = [c.compact() for c in run.cells.values()]
    return out


@router.get("/health")
async def health():
    def name(of: Callable[[], str]) -> str:
        try:
            return of()
        except Exception as exc:  # e.g. Azure env vars missing
            return f"unavailable ({type(exc).__name__})"

    return {
        "ok": True, "profile": LLM_PROFILE, "model": name(model_name), "prompt_version": PROMPT_VERSION,
        "draft_model": name(draft.model_name), "draft_prompt_version": draft.DRAFT_PROMPT_VERSION,
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
            "items": a.item_count() if a else 0, "responses": len(a.responses) if a else 0,
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
    rubric: Text = ""           # a rubric file (Chen's YAML), as the rubric every version shares


_ITEM_BOXES = {"items", "item", "item_key", "item_text", "weight"}


def _use_rubric_file(bank: Assessment, items: list, findings: list[Finding]) -> None:
    """A rubric file's items as the rubric every version shares. What was noted about
    the items it replaces, or about versions without any, no longer holds."""
    bank.criteria = items
    bank.findings = [f for f in bank.findings if not (f.scope == "rubric" and (
        f.box in _ITEM_BOXES or f.code in ("rubric_shared", "rubric_file_not_used")))]
    n, versions = len(items), len(bank.problems)
    bank.findings += findings + [Finding(
        code="rubric_from_file", filled="code", box="file",
        message=f"The rubric file gives {n} item{'' if n == 1 else 's'}: the rubric "
                + (f"{every_version(versions)} share." if versions > 1 else "of the problem."))]


@router.post("/assessments/problem-info")
async def assessment_from_problem_info(body: ProblemInfoIn):
    """A draft bank with findings. The only errors are a file with nothing to load as
    problems, and one of Chen's YAML files sent as the problems."""
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
    if body.rubric.strip():
        try:
            items, findings = read_rubric_yaml(body.rubric)
        except NothingToLoad as exc:
            raise _stop(400, str(exc))
        if bank.criteria:
            bank.findings.append(Finding(
                code="rubric_file_not_used", filled="code", box="file",
                message="The problem file has rubric items of its own, so the rubric file was not "
                        "used. To use it instead, add it on the Rubric step."))
        else:
            _use_rubric_file(bank, items, findings)
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


class MaterialIn(BaseModel):
    name: Text = ""
    material: Text = ""


@router.post("/assessments/material")
async def assessment_from_material(body: MaterialIn):
    """A draft bank from pasted unstructured material: one empty version, for which
    the AI starts drafting a problem text and a rubric. The only error is an empty
    paste. Nothing it drafts is part of the rubric until it is accepted."""
    try:
        bank = _register(draft.new_bank(body.material, body.name))
    except NothingToLoad as exc:
        raise _stop(400, str(exc))
    draft.start(bank.id, *draft.plan(bank, "material"))
    return _bank(bank)


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
    specifications: list[Text] = Field(default_factory=list)
    # A page still open from Phase 2 sends two lists; their lines are kept as specifications.
    accept: list[Text] = Field(default_factory=list)
    reject: list[Text] = Field(default_factory=list)


class RubricIn(BaseModel):
    problem_id: Text                # "" = the rubric every version shares
    items: list[ItemIn]


@router.put("/assessments/{aid}/rubric")
async def replace_rubric(aid: str, body: RubricIn):
    """Replace the shared items, or one version's own."""
    def change(a: Assessment) -> None:
        rubric.set_items(a, body.problem_id, [i.model_dump() for i in body.items])

    return _change(aid, change)


class RubricFileIn(BaseModel):
    text: Text = ""
    replace: bool = False           # the bank has rubric items already: replace them


@router.post("/assessments/{aid}/rubric/file")
async def rubric_from_file(aid: str, body: RubricFileIn):
    """A rubric file (Chen's YAML) as the rubric every version shares. 400 when the
    text is not such a rubric; 409, asking first, when the bank has rubric items and
    ``replace`` is not set. Replacing drops the AI drafts made for the old items."""
    try:
        items, findings = read_rubric_yaml(body.text)
    except NothingToLoad as exc:
        raise _stop(400, str(exc))

    def change(a: Assessment) -> dict:
        rubric.require_draft(a)
        if a.criteria and not body.replace:
            raise rubric.Refusal("This bank already has rubric items. Replace them with the rubric "
                                 "in the file?", kind="exists")
        _use_rubric_file(a, items, findings)
        a.proposals = [p.model_copy(update={"items": [], "order": []}) for p in a.proposals if p.version_text]
        return {"items": len(items)}

    return _change(aid, change)


class ShareIn(BaseModel):
    problem_id: Text                # the version whose items every version is to share


@router.post("/assessments/{aid}/rubric/share")
async def share_rubric(aid: str, body: ShareIn):
    """Make one version's items the rubric every version shares. Versions with the same
    items, or none, use it as it is; the others keep theirs as their own wording."""
    return _change(aid, lambda a: {"sharing": rubric.share_items(a, body.problem_id)})


class CopyIn(BaseModel):
    problem_id: Text                # the version to copy from
    to: list[Text] | None = None    # default: every version without a rubric


@router.post("/assessments/{aid}/rubric/copy")
async def copy_rubric(aid: str, body: CopyIn):
    return _change(aid, lambda a: {"copied_to": rubric.copy_items(a, body.problem_id, body.to)})


@router.post("/assessments/{aid}/rubric/approve")
async def approve_rubric(aid: str):
    """422 with the boxes still to fill and the AI drafts still to decide, else lock:
    hash, revision and warnings."""
    _not_while_drafting(aid)
    return _change(aid, lambda a: {"warnings": rubric.approve(a)["warnings"]})


@router.post("/assessments/{aid}/rubric/unlock")
async def unlock_rubric(aid: str):
    _not_while_grading(aid)
    return _change(aid, rubric.unlock)


# --- AI drafts ---------------------------------------------------------------

class DraftIn(BaseModel):
    mode: Text                              # example | material | text | elaborate (draft.py)
    problem_ids: list[Text] | None = None   # the versions to draft for (default: what the mode is for)
    example_id: Text = ""                   # example: the version to draft from (default: the first with items)
    material: Text | None = None            # material: the pasted text (default: what was pasted before)


@router.post("/assessments/{aid}/drafts")
async def start_draft(aid: str, body: DraftIn):
    """Ask the AI for a draft. It is written in the background: this answers at once,
    and the bank's ``drafting`` says how far it is. What it writes is stored as
    proposals, which are not part of the rubric until they are accepted."""
    a = _working_copy(aid)
    _not_while_grading(aid)
    _not_while_drafting(aid)
    try:
        rubric.require_draft(a)
        if body.mode == "material" and (body.material or "").strip():
            draft.set_material(a, body.material)
        calls, skipped = draft.plan(a, body.mode, body.problem_ids, body.example_id)
    except rubric.Refusal as exc:
        raise _refusal(exc)
    store.save_assessment(a)    # with the pasted material, when there is any
    draft.start(a.id, calls, skipped)
    return _bank(a)


@router.post("/assessments/{aid}/drafts/cancel")
async def cancel_draft(aid: str):
    """Stop the draft being written, or clear what is left to say about one that
    failed. Proposals that are already there stay."""
    a = _assessment(aid)
    draft.cancel(aid)
    return _bank(a)


class AcceptIn(BaseModel):
    proposal_id: Text
    keys: list[Text] = Field(default_factory=list)  # the drafted items to accept
    version_text: bool = False                      # ... and the drafted problem text


@router.post("/assessments/{aid}/proposals/accept")
async def accept_proposal(aid: str, body: AcceptIn):
    """Copy drafted items into the rubric. What is inferred is accepted only when it
    is asked for on its own; otherwise it is left waiting and named in ``left``."""
    return _change(aid, lambda a: rubric.accept_proposal(
        a, body.proposal_id, body.keys, body.version_text))


class DiscardIn(BaseModel):
    proposal_id: Text
    keys: list[Text] | None = None      # None, and no version_text: the whole draft
    version_text: bool = False


@router.post("/assessments/{aid}/proposals/discard")
async def discard_proposal(aid: str, body: DiscardIn):
    def change(a: Assessment) -> None:
        rubric.discard_proposal(a, body.proposal_id, body.keys, body.version_text)

    return _change(aid, change)


class AiFillIn(BaseModel):
    on: bool


@router.put("/assessments/{aid}/ai-fill")
async def set_ai_fill(aid: str, body: AiFillIn):
    """ "Let the AI fill gaps". Off, the AI drafts only when it is asked. Switching it
    on drafts every empty rubric the AI can draft, without asking again; what it
    drafts still waits to be accepted."""
    a = _working_copy(aid)
    if a.kind != "bank":
        raise _stop(409, rubric.LEGACY_SENTENCE)
    a.ai_fill = body.on
    store.save_assessment(a)
    if (body.on and a.rubric_status == "draft" and not draft.is_drafting(aid)
            and not runner.is_running(aid)):
        calls, skipped = draft.plan_fill(a)
        if calls:
            draft.start(a.id, calls, skipped)
    return _bank(a)


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
    draft.cancel(aid)   # a draft still being written for it is dropped, not written back
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
