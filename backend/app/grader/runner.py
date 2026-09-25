"""Batch runs: every (response, criterion) pair x k samples, under a concurrency cap.

Runs execute as asyncio tasks inside the API process (the model calls are async,
so DemoBuilder's streams keep flowing). Each sample is cached by a hash of
(model, effort, prompt version, exact messages, sample #): re-running the same
settings is free, and a run interrupted by a restart resumes by starting it again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import Callable, Literal

from pydantic import BaseModel, Field

from . import store
from .aggregate import addresses_grader, quotes_found, resolve
from .grade import PROMPT_VERSION, build_messages, grade_once, model_name
from .model import Assessment, Cell, Sample, instance_id


class RunConfig(BaseModel):
    k: int = Field(3, ge=1, le=9)                       # samples per pair; odd avoids ties
    effort: str = "minimal"                             # reasoning effort for the grade model
    concurrency: int = Field(6, ge=1, le=32)            # simultaneous model calls
    problem_ids: list[str] | None = None                # None = all problems
    max_responses: int | None = Field(None, ge=1)       # per problem; None = all


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

    def summary(self) -> dict:
        return self.model_dump(exclude={"cells"})


_RUNS: dict[str, Run] = {}
_TASKS: dict[str, asyncio.Task] = {}


def new_run(assessment: Assessment, config: RunConfig) -> Run:
    pairs = assessment.instances(config.problem_ids, config.max_responses)
    if not pairs:
        raise ValueError("Nothing to grade with these settings.")
    run = Run(
        id=uuid.uuid4().hex[:12], assessment_id=assessment.id, config=config,
        model=model_name(), prompt_version=PROMPT_VERSION, started=time.time(),
        total_calls=len(pairs) * config.k,
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
            run.error = "Interrupted by a server restart. Start it again: finished samples are cached."
        _RUNS[run_id] = run
    return _RUNS[run_id]


def persist(run: Run) -> None:
    store.save_run_json(run.id, run.model_dump_json())


def cache_key(messages: list[tuple[str, str]], effort: str, idx: int, model: str) -> str:
    blob = json.dumps([model, effort, PROMPT_VERSION, messages, idx], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def execute(run: Run, assessment: Assessment,
                  on_progress: Callable[[Run], None] | None = None) -> Run:
    cfg = run.config
    sem = asyncio.Semaphore(cfg.concurrency)
    cache = store.cache()
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
                if sample.verdict is not None:
                    cache.put(key, sample.model_dump(include={"verdict", "comparison", "evidence"}))
            sample.quotes_found = quotes_found(sample.evidence, resp.text)
            if sample.verdict is None:
                run.failed_calls += 1
            cell.samples.append(sample)
            resolve(cell, directed[resp.id])
            run.done_calls += 1
            if on_progress:
                on_progress(run)
            if time.time() - last_save > 5:
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
        persist(run)
    return run


def start(run: Run, assessment: Assessment) -> None:
    task = asyncio.get_running_loop().create_task(execute(run, assessment))
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
    assessment = store.get_assessment(run.assessment_id)
    text = next((r.text for r in assessment.responses if r.id == cell.response_id), "")
    resolve(cell, addresses_grader(text))
    persist(run)
    return cell
