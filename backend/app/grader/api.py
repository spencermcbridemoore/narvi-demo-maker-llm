"""FastAPI router for the grader, mounted by app.main at /grader/api.

Flow: create an assessment (Chen example or CSV upload) -> optionally edit criteria
-> start a run -> poll it -> inspect cells, override, export.
In production Caddy puts /grader behind basic auth; DemoBuilder stays open.
"""

from __future__ import annotations

import csv
import io
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..config import LLM_PROFILE
from . import metrics, runner, store
from .adapters import CHEN_VERSIONS, kit_dir, load_chen, load_csv
from .aggregate import HARD_FLAGS
from .grade import EFFORTS, PROMPT_VERSION, model_name
from .model import Assessment

router = APIRouter(prefix="/grader/api", tags=["grader"])


def _assessment(aid: str) -> Assessment:
    try:
        return store.get_assessment(aid)
    except KeyError:
        raise HTTPException(404, "Assessment not found.")


def _run(rid: str) -> runner.Run:
    try:
        return runner.get_run(rid)
    except KeyError:
        raise HTTPException(404, "Run not found.")


def _register(a: Assessment) -> dict:
    a.id = uuid.uuid4().hex[:12]
    a.created = time.time()
    return store.save_assessment(a).model_dump()


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
    return [{
        "id": "chen",
        "name": "Chen & Wan physics (PHY 2048)",
        "description": "89 de-identified student explanations of 3 physics problems, 3 binary "
                       "rubric items each, graded independently by two humans.",
        "rubric_versions": list(CHEN_VERSIONS),
    }]


class ExampleIn(BaseModel):
    example: str = "chen"
    rubric_version: str = "simple"


@router.post("/assessments/example")
async def assessment_from_example(body: ExampleIn):
    if body.example != "chen":
        raise HTTPException(404, "Unknown example.")
    try:
        return _register(load_chen(body.rubric_version))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc))


class CsvIn(BaseModel):
    name: str = "Uploaded exam"
    rubric_csv: str = Field(min_length=1)
    responses_csv: str = Field(min_length=1)


@router.post("/assessments/csv")
async def assessment_from_csv(body: CsvIn):
    try:
        return _register(load_csv(body.rubric_csv, body.responses_csv, body.name or "Uploaded exam"))
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))


@router.get("/assessments/{aid}")
async def get_assessment(aid: str):
    return _assessment(aid).model_dump()


class CriterionEdit(BaseModel):
    criterion_id: str
    text: str = Field(min_length=1)


@router.patch("/assessments/{aid}/criteria")
async def edit_criterion(aid: str, body: CriterionEdit):
    a = _assessment(aid)
    crit = next((c for c in a.criteria if c.id == body.criterion_id), None)
    if crit is None:
        raise HTTPException(404, "Criterion not found.")
    crit.text = body.text.strip()
    store.save_assessment(a)
    return crit.model_dump()


class RunIn(BaseModel):
    assessment_id: str
    k: int = Field(3, ge=1, le=9)
    effort: str = "minimal"
    concurrency: int = Field(6, ge=1, le=32)
    problem_ids: list[str] | None = None
    max_responses: int | None = Field(None, ge=1)


@router.post("/runs")
async def create_run(body: RunIn):
    a = _assessment(body.assessment_id)
    if body.effort not in EFFORTS:
        raise HTTPException(400, f"effort must be one of {list(EFFORTS)}")
    config = runner.RunConfig(**body.model_dump(exclude={"assessment_id"}))
    try:
        run = runner.new_run(a, config)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    runner.start(run, a)
    return _run_payload(run)


@router.get("/runs")
async def list_runs():
    out = []
    for rid in store.list_run_ids()[:20]:
        try:
            out.append(runner.get_run(rid).summary())
        except Exception:  # unreadable file; skip it
            continue
    return out


@router.get("/runs/{rid}")
async def get_run(rid: str):
    return _run_payload(_run(rid))


@router.get("/runs/{rid}/cell")
async def get_cell(rid: str, instance_id: str):
    cell = _run(rid).cells.get(instance_id)
    if cell is None:
        raise HTTPException(404, "Cell not found.")
    return cell.model_dump()


class OverrideIn(BaseModel):
    instance_id: str
    final: int | None = Field(None, ge=0, le=1)  # None clears the override


@router.post("/runs/{rid}/override")
async def override(rid: str, body: OverrideIn):
    run = _run(rid)
    if body.instance_id not in run.cells:
        raise HTTPException(404, "Cell not found.")
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
    run = _run(rid)
    if run.status == "running":
        raise HTTPException(409, "Cancel the run first.")
    runner._RUNS.pop(rid, None)
    store.delete_run(rid)
    return {"deleted": rid}
