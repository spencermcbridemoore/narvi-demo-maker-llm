"""Grade written-response exports folder by folder, from the terminal.

    cd backend
    ../.venv/Scripts/python -m app.grader.batch <question folder | package folder> [options]

A question folder is found by its content, never by its names:
  * an explanations export: a CSV with ID, ItemID, ProblemBody and Explanation columns
    (matched loosely), optionally a figure column (Problem_figure), EarnedPoints, and
    human grades rubric_1 .. rubric_N ("" = not graded);
  * a rubric YAML beside it ("rubrics:" with id, item, specifications), any number of items;
  * optionally the figure files the figure column names, anywhere under the folder.
A package is any folder with question folders under it, at any depth.

Answers are prepared by the web app's own code (frontend/scripts/prepare_explanations.mjs),
so the grader reads exactly what the live grader would. An Explanation of "NA" (or empty)
is no answer: it gets 0 on every item without a model call. Grading uses the app's runner,
sample cache and vote counting; the rubric is graded exactly as written.

Outputs, per question, under scratch/grading-runs/<package>/<question path>/ (git-ignored):
  <export>_ai.csv          the export's own columns, rubric_1..N holding the AI's grades
  <export>_ai_details.csv  per item: grade, confidence, votes, review flags, human grade.
                           No answer text.
  <export>_ai_reasons.csv  the model's reasons and quotes per graded item, for reviewing
                           disagreements. Contains quotes from answers.
  report.md, report.json   counts, agreement with human grades, tokens, cost, time
  answers.json             the prepared answers (student text; stays in scratch)
The grader's own data (runs, sample cache) goes to scratch/grading-runs/.grader unless
GRADER_DATA_DIR is set. Re-running the same settings is free: samples are cached.

Figures: when the export names figures, each needs a written description (see figures.py).
--describe-figures drafts them with the strong model for the teacher to approve;
--figures text grades without them and the report says so.

Start with --estimate (no model calls), then a small --sample.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import REPO_ROOT
from . import figures, grade, rubric, runner, store
from .adapters import NothingToLoad, _norm, read_rubric_yaml, yaml_kind
from .agreement import num, pct, stats
from .aggregate import needs_review
from .grade import EFFORTS, PROMPT_VERSION, build_messages
from .model import Assessment, Problem, Response

# USD per 1M tokens: (input, cached input, output), by model name (a dated name such as
# gpt-4.1-mini-2025-04-14 counts as its model). List prices as of 2026; check your Azure
# contract, and pass --price for a model not listed here (or a deployment named differently).
PRICES = {
    "gpt-5": (1.25, 0.125, 10.0),
    "gpt-5-mini": (0.25, 0.025, 2.0),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-4.1": (2.0, 0.50, 8.0),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-4o": (2.50, 1.25, 10.0),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "o4-mini": (1.10, 0.275, 4.40),
    "o3": (2.0, 0.50, 8.0),
    "gemini-2.5-flash": (0.30, 0.03, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.01, 0.40),
    "gemini-2.5-pro": (1.25, 0.125, 10.0),
}
# Models that take a reasoning effort, when --reasoning is auto. Anything else (gpt-4.1,
# gpt-4o, gpt-5-chat and most non-OpenAI models) is called without one.
REASONING = re.compile(r"^(gpt-5(?!-chat)|gpt-6|o\d)", re.I)
OUT_GUESS = 200          # output tokens per call assumed by --estimate before anything is measured
PREPARE = REPO_ROOT / "frontend" / "scripts" / "prepare_explanations.mjs"

_ID = ["response_id", "id", "student_id", "student", "submission_id", "pseudonym"]
_ANSWER = ["response_text", "response", "answer", "explanation"]
_FIGURE = ["problem_figure", "figure", "figure_file", "problem_image", "image"]
_POINTS = ["earned_points", "points", "score"]
_HUMAN = re.compile(r"^rubric_?([a-z0-9]+)$")


def say(*parts) -> None:
    print(*parts, flush=True)


# --- finding the files -------------------------------------------------------------

@dataclass
class Question:
    export: Path
    rubric: Path | None = None
    problem: str = ""                    # why it cannot be graded, in a sentence


def _header(path: Path) -> list[str]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return next(csv.reader(fh), [])
    except (UnicodeDecodeError, csv.Error, OSError):
        return []


def is_export(path: Path) -> bool:
    """The same test the web app uses (csv.ts recognize): ItemID, ProblemBody and an answer column."""
    names = [_norm(h) for h in _header(path)]
    return "item_id" in names and "problem_body" in names and any(n in names for n in _ANSWER)


def _rubrics_beside(export: Path) -> list[Path]:
    out = []
    for p in sorted(export.parent.iterdir()):
        if p.is_file() and p.suffix.lower() in (".yaml", ".yml"):
            try:
                if yaml_kind(p.read_text(encoding="utf-8"))[0] == "rubric":
                    out.append(p)
            except (UnicodeDecodeError, OSError):
                continue
    return out


def discover(path: Path) -> list[Question]:
    """Every explanations export at or under ``path``, each with the rubric beside it."""
    candidates = [path] if path.is_file() else sorted(p for p in path.rglob("*.csv") if p.is_file())
    out = []
    for p in candidates:
        if not is_export(p):
            continue
        rubrics = _rubrics_beside(p)
        q = Question(export=p, rubric=rubrics[0] if len(rubrics) == 1 else None)
        if not rubrics:
            q.problem = "There is no rubric YAML (a file with \"rubrics:\") in its folder."
        elif len(rubrics) > 1:
            q.problem = (f"Its folder has {len(rubrics)} rubric YAML files "
                         f"({', '.join(r.name for r in rubrics)}); keep one.")
        out.append(q)
    return out


def package_of(path: Path) -> Path:
    """The package folder: the nearest folder at or above ``path`` holding a README (the
    export format ships one), else ``path`` itself."""
    start = path if path.is_dir() else path.parent
    for d in [start, *list(start.parents)[:3]]:
        if any(d.glob("README*")):
            return d
    return start


# --- one question ------------------------------------------------------------------

@dataclass
class Table:
    header: list[str]
    rows: list[list[str]]
    col: dict[str, int | None] = field(default_factory=dict)
    human: dict[str, int] = field(default_factory=dict)      # rubric key -> column

    def get(self, row: list[str], name: str) -> str:
        i = self.col.get(name)
        return row[i] if i is not None and i < len(row) else ""


def read_table(path: Path) -> Table:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = [r for r in reader if any(c.strip() for c in r)]
    names = [_norm(h) for h in header]

    def find(wanted: list[str]) -> int | None:
        for w in wanted:
            if w in names:
                return names.index(w)
        return None

    t = Table(header=header, rows=rows)
    t.col = {"id": find(_ID), "item": find(["item_id"]), "body": find(["problem_body"]),
             "answer": find(_ANSWER), "figure": find(_FIGURE), "points": find(_POINTS)}
    for i, n in enumerate(names):
        m = _HUMAN.match(n)
        if m:
            t.human[m.group(1)] = i
    return t


def prepare(export: Path, out: Path) -> dict:
    """The web app's own preparation of the export, run in Node."""
    done = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(PREPARE), str(export), str(out)],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise RuntimeError(f"preparing the answers failed: {done.stderr.strip()[:500]}")
    for line in done.stdout.strip().splitlines():
        say(f"  {line}")
    return json.loads(out.read_text(encoding="utf-8"))


def spread(indices: list[int], version_of: dict[int, str], n: int, seed: int) -> list[int]:
    """``n`` rows spread across versions as evenly as possible, chosen reproducibly."""
    rng = random.Random(seed)
    by: dict[str, list[int]] = {}
    for i in indices:
        by.setdefault(version_of[i], []).append(i)
    order = sorted(by)
    rng.shuffle(order)
    for v in order:
        rng.shuffle(by[v])
    out: list[int] = []
    while len(out) < n and any(by.values()):
        for v in order:
            if by[v] and len(out) < n:
                out.append(by[v].pop())
    return sorted(out)


def _price(model: str, override: str | None) -> tuple[float, float, float] | None:
    if override:
        parts = [float(x) for x in override.split(",")]
        if len(parts) == 2:
            return parts[0], parts[0], parts[1]     # no separate cached price: charge cached as input
        return parts[0], parts[1], parts[2]
    # Exact names only (plus a date suffix): "gpt-5" must not price "gpt-5.4-mini".
    m = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model.lower().rsplit("/", 1)[-1])   # "@group/model" -> model
    return PRICES.get(m)


def _cost(usage: dict, price) -> float | None:
    if price is None:
        return None
    p_in, p_cached, p_out = price
    cached = usage.get("cached_tokens", 0)
    return ((usage.get("input_tokens", 0) - cached) * p_in + cached * p_cached
            + usage.get("output_tokens", 0) * p_out) / 1e6


def _unique(wanted: str, taken: set[str]) -> str:
    n, out = 2, wanted
    while out in taken:
        out, n = f"{wanted}-{n}", n + 1
    return out


def grade_question(q: Question, package: Path, pkg_name: str, out_root: Path, args) -> dict | None:
    rel = q.export.parent.relative_to(package) if q.export.is_relative_to(package) else Path(q.export.parent.name)
    qdir = out_root / pkg_name / rel          # figure descriptions live here, shared by every run
    out = qdir / args.label if args.label else qdir
    out.mkdir(parents=True, exist_ok=True)
    name = q.export.stem
    say(f"\n== {rel.as_posix() if rel.parts else pkg_name}  ({q.export.name})")
    if q.problem:
        say(f"  skipped: {q.problem}")
        return {"question": rel.as_posix(), "skipped": q.problem}

    # --- read ---------------------------------------------------------------------
    table = read_table(q.export)
    rubric_text = q.rubric.read_text(encoding="utf-8")
    try:
        criteria, rubric_findings = read_rubric_yaml(rubric_text)
    except NothingToLoad as exc:
        say(f"  skipped: {exc}")
        return {"question": rel.as_posix(), "skipped": str(exc)}
    keys = [c.key for c in criteria]
    human = {k: table.human[k.lower()] for k in keys if k.lower() in table.human}
    extra_human = sorted(set(table.human) - {k.lower() for k in keys})
    data = prepare(q.export, out / "answers.json")
    answers = data["answers"]
    if len(answers) != len(table.rows):
        raise RuntimeError(f"the prepared answers ({len(answers)}) do not line up with the export's rows "
                           f"({len(table.rows)})")
    for row, a in zip(table.rows, answers):
        if table.col["id"] is not None and a["original"] != table.get(row, "id").strip():
            raise RuntimeError("the prepared answers are not in the export's row order")

    # --- which rows are graded ------------------------------------------------------
    na = args.na_token.strip()
    status: dict[int, str] = {}
    for i, row in enumerate(table.rows):
        raw = table.get(row, "answer").strip()
        status[i] = "no_answer" if raw in (na, "") else "answer"
    has_human = {i: any(c < len(r) and r[c].strip() for c in human.values()) for i, r in enumerate(table.rows)}
    eligible = [i for i, s in status.items() if s == "answer"]
    if args.rows == "graded":
        eligible = [i for i in eligible if has_human[i]]
    elif args.rows == "incorrect":
        if table.col["points"] is None:
            say("  skipped: --rows incorrect needs an EarnedPoints column, and this export has none.")
            return {"question": rel.as_posix(), "skipped": "no EarnedPoints column"}

        def zero(i: int) -> bool:
            try:
                return float(table.get(table.rows[i], "points")) == 0.0
            except ValueError:
                return False
        eligible = [i for i in eligible if zero(i)]
    version_of = {i: answers[i]["item"] for i in range(len(answers))}
    chosen = spread(eligible, version_of, args.sample, args.seed) if args.sample else eligible
    chosen_set = set(chosen)
    for i in range(len(table.rows)):
        if status[i] == "answer" and i not in chosen_set:
            status[i] = "not_selected"

    # --- figures ----------------------------------------------------------------------
    versions_used = sorted({version_of[i] for i in chosen})
    problem_text = {v: data["versions"].get(v, "") for v in versions_used}
    figure_note = "The export names no figures."
    if table.col["figure"] is not None:
        fig_of: dict[str, str] = {}
        for i in chosen:
            f = table.get(table.rows[i], "figure").strip()
            if f:
                fig_of.setdefault(version_of[i], f)
        names = set(fig_of.values())
        if args.figures == "text":
            figure_note = (f"{len(names)} figures are named in the export; they were NOT shown to the "
                           "grader (--figures text).")
        elif names:
            files = figures.locate(q.export.parent, names)
            missing = sorted(names - set(files))
            if missing:
                say(f"  stopped: {len(missing)} figures named in the export are not in its folder "
                    f"({', '.join(missing[:5])}{' ...' if len(missing) > 5 else ''}). Add them, or pass "
                    "--figures text to grade without them.")
                return {"question": rel.as_posix(), "skipped": "figure files missing"}
            if args.describe_figures:
                first = {}
                for v, f in sorted(fig_of.items()):
                    first.setdefault(f, problem_text.get(v, ""))
                t0 = time.time()
                path, n = figures.draft(files, first, qdir)
                say(f"  drafted {n} figure descriptions in {time.time() - t0:.0f}s -> {path}")
                say("  Read each against its image, edit if needed, set status: approved, then run again.")
                return {"question": rel.as_posix(), "skipped": "figure descriptions drafted, waiting for approval"}
            have, _drafts = figures.descriptions(files, qdir)
            lacking = sorted(names - set(have))
            if lacking and not args.estimate:
                say(f"  stopped: {len(lacking)} of {len(names)} figures have no approved description yet. "
                    f"Run with --describe-figures to draft them, approve them in {qdir / figures.DESCRIPTIONS}, "
                    "or pass --figures text to grade without them.")
                return {"question": rel.as_posix(), "skipped": "figure descriptions not approved"}
            for v, f in fig_of.items():
                if f in have:
                    problem_text[v] = figures.with_figure(problem_text[v], have[f][0])
            sources = sorted({src for _, src in have.values()})
            figure_note = (f"{len(names)} figures, each told to the grader as a written description "
                           f"({'; '.join(sources) or 'none yet'}).")
            if lacking:
                figure_note += f" {len(lacking)} still lack an approved description (estimate only)."
    say(f"  {figure_note}")

    # --- the bank ------------------------------------------------------------------------
    taken: set[str] = set()
    rid_of: dict[int, str] = {}
    responses = []
    for i in chosen:
        rid = _unique(answers[i]["sid"], taken)
        taken.add(rid)
        rid_of[i] = rid
        responses.append(Response(id=rid, problem_id=version_of[i], text=answers[i]["text"],
                                  external_id=answers[i]["original"], version_label=version_of[i]))
    bank = Assessment(
        id="batch-" + hashlib.sha1(str(q.export.resolve()).encode()).hexdigest()[:10],
        name=f"{pkg_name}/{rel.as_posix()}", source="batch", kind="bank", rubric_version="uploaded",
        problems=[Problem(id=v, text=problem_text[v]) for v in versions_used],
        criteria=criteria, responses=responses, created=time.time(),
    )
    counts = {
        "rows": len(table.rows), "no_answer": sum(s == "no_answer" for s in status.values()),
        "answers": sum(s != "no_answer" for s in status.values()), "selected": len(chosen),
        "human_graded_rows": sum(has_human.values()), "versions": len(set(version_of.values())),
        "versions_graded": len(versions_used), "items": len(keys),
    }
    say(f"  {counts['rows']} rows: {counts['no_answer']} without an answer (0 on every item, no call), "
        f"{counts['selected']} answers to grade across {counts['versions_graded']} versions, "
        f"{len(keys)} items; human grades on {counts['human_graded_rows']} rows")
    if not chosen:
        say("  nothing to grade with these settings.")
        return {"question": rel.as_posix(), "skipped": "nothing to grade"}
    try:
        rubric.approve(bank)
    except rubric.Refusal as exc:
        why = "; ".join([exc.message, *(b["message"] for b in exc.boxes)])
        say(f"  stopped: {why}")
        return {"question": rel.as_posix(), "skipped": why}
    config = runner.RunConfig(k=args.k, effort=args.effort, concurrency=args.concurrency)
    model = runner.model_name()
    price = _price(model, args.price)

    if args.estimate:
        cache = store.cache()
        problems = {p.id: p for p in bank.problems}
        calls = cached = chars = 0
        for resp, crit in bank.instances():
            msgs = build_messages(problems[resp.problem_id], crit, resp)
            size = sum(len(text) for _, text in msgs)
            for idx in range(args.k):
                calls += 1
                if cache.get(runner.cache_key(msgs, args.effort, idx, model)) is not None:
                    cached += 1
                else:
                    chars += size
        new = calls - cached
        usage = {"input_tokens": chars // 4, "output_tokens": new * OUT_GUESS}
        cost = _cost(usage, price)
        say(f"  estimate: {calls} calls ({len(bank.instances())} answer x item pairs x {args.k} votes), "
            f"{cached} already cached; about {usage['input_tokens']:,} input and {usage['output_tokens']:,} "
            f"output tokens on {model} = about "
            + (f"${cost:.2f}" if cost is not None else "unknown cost (pass --price)"))
        return {"question": rel.as_posix(), "estimate": True, "calls": calls, "cached": cached,
                "usage": usage, "cost": cost, "counts": counts, "model": model}

    # --- grade ---------------------------------------------------------------------------
    run = runner.new_run(bank, config)
    say(f"  run {run.id}: {run.total_calls} calls on {model} (effort {args.effort}, k {args.k}, "
        f"prompt {PROMPT_VERSION}, rubric {run.rubric_hash})")
    t0, last = time.time(), [time.time()]

    def progress(r: runner.Run) -> None:
        if time.time() - last[0] > 15 or r.done_calls == r.total_calls:
            last[0] = time.time()
            say(f"    {r.done_calls}/{r.total_calls} calls, {r.cached_calls} cached, "
                f"{r.failed_calls} failed, {time.time() - t0:.0f}s")

    asyncio.run(runner.execute(run, bank, progress))
    seconds = time.time() - t0
    cost = _cost(run.usage, price)

    # --- results -------------------------------------------------------------------------
    def cell(i: int, k: str):
        return run.cells.get(f"{rid_of[i]}#{k}") if i in rid_of else None

    def ai(i: int, k: str) -> int | None:
        if status[i] == "no_answer":
            return 0
        c = cell(i, k)
        return None if c is None else c.final

    def hum(i: int, k: str) -> int | None:
        if k not in human:
            return None
        v = (table.rows[i][human[k]] if human[k] < len(table.rows[i]) else "").strip()
        return int(float(v)) if v in ("0", "1", "0.0", "1.0") else None

    # the export, shaped as it came, with the AI's grades in rubric_1..N
    header = list(table.header)
    ai_col = {}
    for k in keys:
        if k in human:
            ai_col[k] = human[k]
        else:
            header.append(f"rubric_{k}")
            ai_col[k] = len(header) - 1
    with open(out / f"{name}_ai.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for i, row in enumerate(table.rows):
            row = row + [""] * (len(header) - len(row))
            for k in keys:
                v = ai(i, k)
                row[ai_col[k]] = "" if v is None else str(v)
            w.writerow(row)

    flagged_items: dict[int, list[str]] = {}
    with open(out / f"{name}_ai_details.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        cols = ["ID", "ItemID", "one_time_id"] + (["EarnedPoints"] if table.col["points"] is not None else [])
        cols += ["status"]
        for k in keys:
            cols += [f"ai_{k}", f"conf_{k}", f"votes_{k}", f"flags_{k}"] + ([f"human_{k}"] if k in human else [])
        cols += ["needs_review"] + (["disagrees"] if human else [])
        w.writerow(cols)
        for i, row in enumerate(table.rows):
            st = status[i]
            c0 = [table.get(row, "id"), version_of[i], answers[i]["sid"]]
            c0 += [table.get(row, "points")] if table.col["points"] is not None else []
            line = c0 + ["graded" if st == "answer" else st]
            review, differ = [], []
            for k in keys:
                c = cell(i, k)
                v = ai(i, k)
                flags = "no_answer" if st == "no_answer" else (" ".join(c.flags) if c else "")
                if c is not None and c.final is None:
                    line[len(c0)] = "failed"
                line += ["" if v is None else v, "" if c is None or c.confidence is None else c.confidence,
                         c.votes if c else "", flags]
                if k in human:
                    h = hum(i, k)
                    line.append("" if h is None else h)
                    if h is not None and v is not None and h != v:
                        differ.append(k)
                if c is not None and needs_review(c, args.threshold):
                    review.append(k)
            flagged_items[i] = review
            line += [" ".join(review)] + ([" ".join(differ)] if human else [])
            w.writerow(line)

    with open(out / f"{name}_ai_reasons.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ID", "ItemID", "item", "ai", "human", "votes", "flags", "reason", "quotes"])
        for i in chosen:
            for k in keys:
                c = cell(i, k)
                if c is None:
                    continue
                win = next((s for s in c.samples if s.verdict == c.final), None)
                h = hum(i, k)
                w.writerow([table.get(table.rows[i], "id"), version_of[i], k, "" if c.final is None else c.final,
                            "" if h is None else h, c.votes, " ".join(c.flags),
                            win.comparison if win else "", " | ".join(win.evidence) if win else ""])

    # --- agreement -----------------------------------------------------------------------
    graded = [i for i in chosen]
    per_item = {}
    for k in keys:
        cs = [cell(i, k) for i in graded]
        done = [c for c in cs if c is not None and c.final is not None]
        per_item[k] = {
            "credit": sum(c.final for c in done) / len(done) if done else None,
            "unanimous": sum(c.confidence == 1.0 for c in done) / len(done) if done else None,
            "to_review": sum(needs_review(c, args.threshold) for c in cs if c is not None),
            "failed": len(cs) - len(done),
        }
    result: dict = {
        "question": rel.as_posix(), "export": q.export.name, "rubric": q.rubric.name, "out": str(out),
        "counts": counts, "figures": figure_note, "model": model, "effort": args.effort if grade.uses_reasoning() else "none", "k": args.k,
        "prompt_version": PROMPT_VERSION, "rubric_hash": run.rubric_hash, "run_id": run.id,
        "rows_mode": args.rows, "sample": args.sample, "seed": args.seed, "threshold": args.threshold,
        "calls": run.total_calls, "cached_calls": run.cached_calls, "failed_calls": run.failed_calls,
        "usage": run.usage, "cost": cost, "price": price, "seconds": seconds, "items": per_item,
        "answers_to_review": sum(bool(v) for i, v in flagged_items.items() if i in chosen_set),
        "rubric_notes": [f.message for f in rubric_findings if f.code != "weight_filled"],
        "prepare_notes": data.get("notes", []),
        "human_columns_not_in_rubric": extra_human,
    }
    if human:
        answered = {k: [(hum(i, k), ai(i, k)) for i in graded
                        if hum(i, k) is not None and ai(i, k) is not None] for k in human}
        blank = {k: [(hum(i, k), 0) for i, s in status.items() if s == "no_answer" and hum(i, k) is not None]
                 for k in human}
        unanimous, split, caught, disagreements = [], [], 0, 0
        for i in graded:
            for k in human:
                c, h = cell(i, k), hum(i, k)
                if c is None or c.final is None or h is None:
                    continue
                (unanimous if c.confidence == 1.0 else split).append((h, c.final))
                if h != c.final:
                    disagreements += 1
                    caught += needs_review(c, args.threshold)
        full = [i for i in graded if all(hum(i, k) is not None and ai(i, k) is not None for k in human)]
        result["agreement"] = {
            "items": {k: stats(v) for k, v in answered.items()},
            "all": stats([p for v in answered.values() for p in v]),
            "no_answer_rows": stats([p for v in blank.values() for p in v]),
            "unanimous": stats(unanimous), "split": stats(split),
            "disagreements": disagreements, "caught_by_review": caught,
            "answers_all_items_match": (sum(all(hum(i, k) == ai(i, k) for k in human) for i in full) / len(full)
                                        if full else None),
            "answers_compared": len(full),
        }
    (out / "report.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    (out / "report.md").write_text(report_md(result, human), encoding="utf-8")
    say(f"  done in {seconds:.0f}s: {run.total_calls} calls ({run.cached_calls} cached, {run.failed_calls} failed), "
        f"tokens in {run.usage.get('input_tokens', 0):,} out {run.usage.get('output_tokens', 0):,}, cost "
        + (f"${cost:.2f}" if cost is not None else "unknown"))
    if human:
        a = result["agreement"]["all"]
        say(f"  vs human, answered rows: n {a['n']}, agreement {pct(a['agree'])}, kappa {num(a['kappa'])}, "
            f"AI too strict {a['too_strict']}, too generous {a['too_generous']}")
        for k, s in result["agreement"]["items"].items():
            say(f"    item {k}: n {s['n']}, agreement {pct(s['agree'])}, kappa {num(s['kappa'])}, "
                f"human yes {pct(s['human_yes'])}, AI yes {pct(s['ai_yes'])}, "
                f"too strict {s['too_strict']}, too generous {s['too_generous']}")
    say(f"  -> {out}")
    return result


def report_md(r: dict, human: dict) -> str:
    c = r["counts"]
    u = r["usage"]
    lines = [
        f"# {r['question']}", "",
        f"Export `{r['export']}`, rubric `{r['rubric']}`.", "",
        "## What was graded", "",
        f"- {c['rows']} rows; {c['no_answer']} without an answer (0 on every item by rule, no model call).",
        f"- {c['selected']} answers graded (rows: {r['rows_mode']}"
        + (f", sample of {r['sample']} spread across versions, seed {r['seed']}" if r["sample"] else "") + ").",
        f"- {c['versions_graded']} of {c['versions']} versions; {c['items']} rubric items.",
        f"- Figures: {r['figures']}",
    ]
    lines += [f"- Rubric: {n}" for n in r["rubric_notes"]]
    if r["human_columns_not_in_rubric"]:
        lines.append(f"- Human grade columns with no rubric item: {', '.join(r['human_columns_not_in_rubric'])}.")
    lines += [
        "", "## Settings and cost", "",
        f"- Model {r['model']}, reasoning effort {r['effort']}, {r['k']} votes per answer and item, "
        f"review threshold {r['threshold']}.",
        f"- Prompt version {r['prompt_version']}, rubric hash {r['rubric_hash']}, run {r['run_id']}.",
        f"- {r['calls']} calls: {r['cached_calls']} from cache, {r['failed_calls']} failed.",
        f"- Tokens: input {u.get('input_tokens', 0):,} (cached {u.get('cached_tokens', 0):,}), output "
        f"{u.get('output_tokens', 0):,} (reasoning {u.get('reasoning_tokens', 0):,}).",
        f"- Cost: " + (f"${r['cost']:.2f} at ${r['price'][0]}/${r['price'][2]} per 1M tokens in/out (list "
                       "prices; check your contract)" if r["cost"] is not None else "unknown (no price for this model)") + ".",
        f"- Time: {r['seconds']:.0f} s.", "",
        "## AI grades", "",
        "| Item | Credit | Unanimous votes | To review | Failed |", "|---|---|---|---|---|",
    ]
    for k, s in r["items"].items():
        lines.append(f"| {k} | {pct(s['credit'])} | {pct(s['unanimous'])} | {s['to_review']} | {s['failed']} |")
    lines += ["", f"Answers with at least one item to review: {r['answers_to_review']} of {c['selected']}."]
    if "agreement" in r:
        a = r["agreement"]
        lines += [
            "", "## Agreement with the human grades", "",
            "Answered rows only; rows without an answer are below. Too strict = the human gave credit "
            "and the AI did not; too generous = the reverse.", "",
            "| Item | n | Agreement | Kappa | Human yes | AI yes | Too strict | Too generous |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for k, s in [*a["items"].items(), ("all", a["all"]), ("unanimous votes", a["unanimous"]),
                     ("split votes", a["split"]), ("no answer (NA)", a["no_answer_rows"])]:
            lines.append(f"| {k} | {s['n']} | {pct(s['agree'])} | {num(s['kappa'])} | {pct(s['human_yes'])} | "
                         f"{pct(s['ai_yes'])} | {s['too_strict']} | {s['too_generous']} |")
        lines += [
            "",
            f"- Disagreements flagged for review: {a['caught_by_review']} of {a['disagreements']}.",
            f"- Answers matching the human on every item: {pct(a['answers_all_items_match'])} "
            f"of {a['answers_compared']}.",
        ]
    lines += ["", "Files: `*_ai.csv` (the export with AI grades), `*_ai_details.csv` (per item, no answer text), "
              "`*_ai_reasons.csv` (the model's reasons, with quotes from answers).", ""]
    return "\n".join(lines)


def summary_md(package: str, results: list[dict]) -> str:
    lines = [f"# {package}", "",
             "| Question | Answers graded | Calls | Cached | Failed | Cost | Time | Agreement | Kappa |",
             "|---|---|---|---|---|---|---|---|---|"]
    total_cost, total_time = 0.0, 0.0
    for r in results:
        if r.get("skipped") or r.get("estimate"):
            lines.append(f"| {r['question']} | {r.get('skipped') or 'estimate only'} |  |  |  |  |  |  |  |")
            continue
        a = r.get("agreement", {}).get("all", {})
        total_cost += r["cost"] or 0.0
        total_time += r["seconds"]
        lines.append(f"| {r['question']} | {r['counts']['selected']} | {r['calls']} | {r['cached_calls']} | "
                     f"{r['failed_calls']} | " + (f"${r['cost']:.2f}" if r["cost"] is not None else "-")
                     + f" | {r['seconds']:.0f} s | {pct(a.get('agree'))} | {num(a.get('kappa'))} |")
    lines += ["", f"Total cost ${total_cost:.2f}, time {total_time:.0f} s.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="a question folder, a package folder, or one export CSV")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "scratch" / "grading-runs",
                    help="output root (default: the repo's git-ignored scratch/grading-runs)")
    ap.add_argument("--package", default="", help="package name for the output folder (default: found)")
    ap.add_argument("--k", type=int, default=3, help="votes per answer and item; odd avoids ties")
    ap.add_argument("--effort", default="minimal", choices=EFFORTS)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--rows", default="all", choices=["all", "graded", "incorrect"],
                    help="all answers; only rows with human grades; only EarnedPoints == 0")
    ap.add_argument("--sample", type=int, default=0, help="grade only N answers, spread across versions")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--na-token", default="NA", help='an Explanation meaning "no answer" (default NA)')
    ap.add_argument("--figures", default="describe", choices=["describe", "text"],
                    help="describe: figures go to the grader as approved descriptions; text: without them")
    ap.add_argument("--describe-figures", action="store_true",
                    help="draft descriptions of figures that have none (strong model), then stop")
    ap.add_argument("--threshold", type=float, default=0.8, help="review below this vote share")
    ap.add_argument("--price", default=None, help="USD per 1M tokens: in,out or in,cached,out")
    ap.add_argument("--estimate", action="store_true", help="count calls and cost; no model calls")
    ap.add_argument("--label", default="", help="write this run's outputs to a subfolder of the question's "
                    "output folder (e.g. effort-low), so runs with different settings sit side by side")
    ap.add_argument("--model", default="", help="grade with this model instead of the grader's own: on Azure, "
                    "the name of any deployment in the same resource (e.g. gpt-4.1-nano). Its outputs go to a "
                    "subfolder named after it unless --label says otherwise")
    ap.add_argument("--profile", default="", help="the model profile --model belongs to (models.yaml), "
                    "e.g. orion for UCF's Orion gateway with --model @vertex-general/gemini-2.5-flash; "
                    "default: the app's own (LLM_PROFILE)")
    ap.add_argument("--reasoning", default="auto", choices=["auto", "yes", "no"],
                    help="whether --model takes a reasoning effort (auto: gpt-5*, gpt-6*, o-series do)")
    args = ap.parse_args(argv)

    path = args.path.resolve()
    if not path.exists():
        ap.error(f"{path} does not exist")
    if args.model:
        reasoning = args.reasoning == "yes" or (args.reasoning == "auto" and bool(REASONING.match(args.model)))
        grade.use_model(args.model, reasoning=reasoning, profile=args.profile or None)
        if not args.label:
            short = args.model.rsplit("/", 1)[-1]          # "@vertex-general/gemini-2.5-flash" -> gemini-2.5-flash
            args.label = ((f"{args.profile}-" if args.profile else "") + re.sub(r"[^A-Za-z0-9._-]+", "-", short)
                          + (f"-{args.effort}" if reasoning else ""))
        say(f"model {args.model} ({'reasoning effort ' + args.effort if reasoning else 'no reasoning effort'})")
    os.environ.setdefault("GRADER_DATA_DIR", str(args.out.resolve() / ".grader"))
    if not args.estimate and not args.describe_figures:
        say(f"grader data and sample cache: {store.data_dir()}")
    questions = discover(path)
    if not questions:
        ap.error("no explanations export (a CSV with ItemID, ProblemBody and Explanation columns) found there")
    package = package_of(path)
    if path.is_dir() and not path.is_relative_to(package):
        package = path
    out_root = args.out.resolve()
    pkg_name = args.package or package.name
    say(f"package {pkg_name}: {len(questions)} question(s)")
    results = []
    for q in questions:
        results.append(grade_question(q, package, pkg_name, out_root, args) or {})
    if len(questions) > 1 and not args.estimate:
        target = out_root / pkg_name / "summary.md"
        target.write_text(summary_md(pkg_name, results), encoding="utf-8")
        say(f"\nsummary -> {target}")
    if args.estimate:
        calls = sum(r.get("calls", 0) for r in results)
        costs = [r.get("cost") for r in results if r.get("estimate")]
        say(f"\nestimate, all questions: {calls} calls, about "
            + (f"${sum(c for c in costs if c is not None):.2f}" if costs else "-"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
