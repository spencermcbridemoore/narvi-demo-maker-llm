"""Batch runs: every (response, criterion) pair x k samples, under a concurrency cap.

Runs execute as asyncio tasks inside the API process (the model calls are async,
so DemoBuilder's streams keep flowing). Each sample is cached by a hash of
(model, effort, prompt version, exact messages, sample #): re-running the same
settings is free, and a run interrupted by a restart resumes by starting it again.

A run starts only when ``rubric.check`` finds nothing that blocks grading, and it is
stamped with the rubric it used (hash, revision and a copy), so it stays readable
after the rubric is revised.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import Callable, Literal

from pydantic import BaseModel, Field

from . import rubric, store
from .aggregate import addresses_grader, quotes_found, resolve
from .grade import PROMPT_VERSION, build_messages, grade_once, model_name
from .model import Assessment, Cell, Sample, instance_id, text_fingerprint

INTERRUPTED = "Interrupted by a server restart. Start it again: finished samples are cached."


class RunConfig(BaseModel):
    k: int = Field(3, ge=1, le=9)                       # samples per pair; odd avoids ties
    effort: str = "minimal"                             # reasoning effort for the grade model
    concurrency: int = Field(6, ge=1, le=32)            # simultaneous model calls
    problem_ids: list[str] | None = None                # None = all versions
    max_responses: int | None = Field(None, ge=1)       # per version; None = all


class Run(BaseModel):
    id: str
    assessment_id: str
    config: RunConfig
    status: Literal["running", "done", "cancelled", "error"] = "running"
    model: str = ""
    prompt_version: str = ""
    total_calls: int = 0
    done_calls: int = 0
    failed_calls: int = 0
    cached_calls: int = 0
    started: float = 0.0
    finished: float | None = None
    usage: dict[str, int] = Field(default_factory=lambda: {
        "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0,
    })
    cells: dict[str, Cell] = Field(default_factory=dict)
    error: str | None = None
    # The rubric this run graded with. Runs saved before this was recorded have none.
    rubric_hash: str = ""
    rubric_revision: int = 0
    rubric_copy: dict | None = None
    # response id -> fingerprint of the text that was graded. The bank's responses can
    # be replaced later, and one-time ids start again at S001 with every upload, so a
    # grade is shown only next to a response that still has both the id and the text.
    response_fingerprints: dict[str, str] | None = None

    def summary(self) -> dict:
        return self.model_dump(exclude={"cells"})


_RUNS: dict[str, Run] = {}
_TASKS: dict[str, asyncio.Task] = {}


def new_run(assessment: Assessment, config: RunConfig) -> Run:
    if assessment.kind != "bank":
        raise rubric.Refusal(rubric.LEGACY_SENTENCE, kind="legacy")
    blockers = rubric.check(assessment)["grade_blockers"]
    if blockers:
        raise rubric.Refusal("Grading cannot start yet.", boxes=blockers, kind="not_ready")
    pairs = assessment.instances(config.problem_ids, config.max_responses)
    if not pairs:
        raise rubric.Refusal("There is nothing to grade with these settings.", kind="not_ready")
    run = Run(
        id=uuid.uuid4().hex[:12], assessment_id=assessment.id, config=config,
        model=model_name(), prompt_version=PROMPT_VERSION, started=time.time(),
        total_calls=len(pairs) * config.k,
        rubric_hash=assessment.rubric_hash, rubric_revision=assessment.rubric_revision,
        rubric_copy=rubric.rubric_copy(assessment),
        response_fingerprints={resp.id: text_fingerprint(resp.text) for resp, _ in pairs},
    )
    for resp, crit in pairs:
        iid = instance_id(resp, crit)
        run.cells[iid] = Cell(instance_id=iid, response_id=resp.id, criterion_id=crit.id)
    _RUNS[run.id] = run
    persist(run)
    return run


def get_run(run_id: str) -> Run:
    if run_id not in _RUNS:
        run = Run.model_validate_json(store.load_run_json(run_id))
        if run.status == "running":  # on disk only => the process that ran it is gone
            run.status = "cancelled"
            run.error = INTERRUPTED
        _RUNS[run_id] = run
    return _RUNS[run_id]


def persist(run: Run) -> None:
    store.save_run_json(run.id, run.model_dump_json())


def _kept(run: Run) -> bool:
    """False once the run has been deleted. Calls still in flight at that moment must
    not write it, or its samples, back."""
    return _RUNS.get(run.id) is run


def is_running(assessment_id: str | None = None) -> bool:
    """Is a run in progress, for this bank or (with no id) for any bank?"""
    return any(
        r.status == "running" and assessment_id in (None, r.assessment_id)
        for r in _RUNS.values()
    )


def summaries(assessment_id: str | None = None) -> list[dict]:
    """Every saved run without its cells, newest first, optionally for one bank.
    Runs this process holds come from memory; the rest are read from disk without
    building the full run. A file that cannot be read is skipped."""
    out: list[dict] = []
    for rid in store.list_run_ids():
        try:
            if rid in _RUNS:
                head = _RUNS[rid].summary()
            else:
                head = store.run_header(rid)
                if head.get("status") == "running":   # on disk only: see get_run
                    head["status"], head["error"] = "cancelled", INTERRUPTED
        except Exception:
            continue
        if assessment_id is None or head.get("assessment_id") == assessment_id:
            head.pop("rubric_copy", None)
            head.pop("response_fingerprints", None)
            out.append(head)
    # By start time, not by file time: deciding a grade on an older run rewrites its file.
    return sorted(out, key=lambda head: head.get("started") or 0, reverse=True)


def delete_run(run_id: str) -> None:
    _RUNS.pop(run_id, None)
    store.delete_run(run_id)


def delete_runs_for(assessment_id: str) -> int:
    ids = [head["id"] for head in summaries(assessment_id)]
    for rid in ids:
        delete_run(rid)
    return len(ids)


def cache_key(messages: list[tuple[str, str]], effort: str, idx: int, model: str) -> str:
    blob = json.dumps([model, effort, PROMPT_VERSION, messages, idx], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def execute(run: Run, assessment: Assessment,
                  on_progress: Callable[[Run], None] | None = None) -> Run:
    cfg = run.config
    sem = asyncio.Semaphore(cfg.concurrency)
    cache = store.cache()
    generation = cache.generation   # answers that arrive after a clear are not stored
    problems = {p.id: p for p in assessment.problems}
    criteria = {c.id: c for c in assessment.criteria}
    responses = {r.id: r for r in assessment.responses}
    directed = {rid: addresses_grader(r.text) for rid, r in responses.items()}
    last_save = time.time()

    async def one(cell: Cell, idx: int) -> None:
        nonlocal last_save
        async with sem:
            if run.status != "running":
                return
            resp = responses[cell.response_id]
            msgs = build_messages(problems[resp.problem_id], criteria[cell.criterion_id], resp)
            key = cache_key(msgs, cfg.effort, idx, run.model)
            hit = cache.get(key)
            if hit is not None:
                sample = Sample(**hit, cached=True)
                run.cached_calls += 1
            else:
                sample, usage = await grade_once(msgs, cfg.effort)
                for name, n in usage.items():
                    run.usage[name] = run.usage.get(name, 0) + n
                if sample.verdict is not None and _kept(run):
                    cache.put(key, sample.model_dump(include={"verdict", "comparison", "evidence"}),
                              bank=run.assessment_id, generation=generation)
            sample.quotes_found = quotes_found(sample.evidence, resp.text)
            if sample.verdict is None:
                run.failed_calls += 1
            cell.samples.append(sample)
            resolve(cell, directed[resp.id])
            run.done_calls += 1
            if on_progress:
                on_progress(run)
            if time.time() - last_save > 5 and _kept(run):
                persist(run)
                last_save = time.time()

    try:
        await asyncio.gather(*(one(c, i) for c in run.cells.values() for i in range(cfg.k)))
        if run.status == "running":
            run.status = "done"
    except Exception as exc:  # unexpected bug, not a model error (those become samples)
        run.status = "error"
        run.error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        run.finished = time.time()
        if _kept(run):
            persist(run)
    return run


def start(run: Run, assessment: Assessment) -> None:
    # The run grades a snapshot, so nothing done to the bank afterwards can reach it.
    snapshot = assessment.model_copy(deep=True)
    task = asyncio.get_running_loop().create_task(execute(run, snapshot))
    _TASKS[run.id] = task  # keep a reference so the task isn't garbage-collected
    task.add_done_callback(lambda _t: _TASKS.pop(run.id, None))


def cancel(run_id: str) -> Run:
    run = get_run(run_id)
    if run.status == "running":
        run.status = "cancelled"
    return run


def set_override(run_id: str, iid: str, final: int | None) -> Cell:
    run = get_run(run_id)
    cell = run.cells[iid]
    cell.override = final
    # The flag was set from the response as it was graded; keep it rather than
    # re-reading a bank whose responses may have been replaced since.
    resolve(cell, "addresses_grader" in cell.flags)
    persist(run)
    return cell
