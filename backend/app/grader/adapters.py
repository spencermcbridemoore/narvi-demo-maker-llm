"""Adapters: outside formats -> Assessment.

Two today:
  * ``load_chen``: the Chen & Wan handoff kit (``data/instances.csv``), with the
    two reference graders kept as evaluation-only labels.
  * ``load_csv``: a teacher's rubric CSV + responses CSV, with forgiving column
    names. Names/ids should already be stripped by the browser before upload.

A new format costs one function here; nothing downstream changes.
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path

from ..config import REPO_ROOT
from .model import Assessment, Criterion, Problem, Response

# Rubric versions available in the kit's instances.csv. ``simple`` is the wording
# the human graders used; the detailed ones are Chen's model-facing rewrites, which
# he tuned on these same responses (so scores with them are optimistic).
CHEN_VERSIONS = {
    "simple": "rubric_item_simple",
    "detailed": "rubric_item_detailed",
    "detailed_latest": "rubric_item_detailed_latest",
}


def kit_dir() -> Path | None:
    """The unpacked handoff kit, or None if it isn't on this machine."""
    default = REPO_ROOT / "grader_kit" / "rubric_grader_handoff"
    path = Path(os.getenv("GRADER_KIT_DIR", str(default)))
    return path if (path / "data" / "instances.csv").is_file() else None


def load_chen(rubric_version: str = "simple", kit: Path | None = None) -> Assessment:
    kit = kit or kit_dir()
    if kit is None:
        raise FileNotFoundError(
            "Chen kit not found. Unzip rubric_grader_handoff_*.zip into grader_kit/ "
            "(or set GRADER_KIT_DIR)."
        )
    if rubric_version not in CHEN_VERSIONS:
        raise ValueError(f"rubric_version must be one of {sorted(CHEN_VERSIONS)}")
    column = CHEN_VERSIONS[rubric_version]

    with open(kit / "data" / "instances.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    problems: dict[str, Problem] = {}
    criteria: dict[str, Criterion] = {}
    responses: dict[str, Response] = {}
    references: dict[str, dict[str, int]] = {}
    for r in rows:
        pid = r["problem_id"]
        problems.setdefault(pid, Problem(
            id=pid, text=r["problem_text"].strip(), instructions=r["student_instructions"].strip(),
        ))
        cid = f"{pid}#{r['item_no']}"
        criteria.setdefault(cid, Criterion(
            id=cid, problem_id=pid, key=r["item_no"], label=f"Item {r['item_no']}",
            text=r[column].strip(), source_text=r["rubric_item_simple"].strip(),
        ))
        responses.setdefault(r["response_id"], Response(
            id=r["response_id"], problem_id=pid, text=r["response_text"],
            external_id=r["response_id"],
        ))
        references[r["instance_id"]] = {
            "ref_a": int(r["label_ref_a"]), "ref_b": int(r["label_ref_b"]),
        }

    return Assessment(
        name=f"Chen & Wan physics (PHY 2048), {rubric_version} rubric",
        source="example:chen",
        rubric_version=rubric_version,
        problems=sorted(problems.values(), key=lambda p: p.id),
        criteria=sorted(criteria.values(), key=lambda c: (c.problem_id, _num(c.key))),
        responses=list(responses.values()),
        references=references,
    )


# --- generic CSV -------------------------------------------------------------

# Accepted header names, first match wins (case/space-insensitive).
_RUBRIC_COLUMNS = {
    "problem_id": ["problem_id", "problem", "question_id", "question", "q"],
    "problem_text": ["problem_text", "question_text", "prompt", "problem_body"],
    "instructions": ["instructions", "student_instructions"],
    "key": ["item_no", "item", "item_id", "criterion_id", "key", "no", "number"],
    "text": ["criterion_text", "item_text", "rubric_item", "criterion", "description",
             "rubric", "text"],
    "points": ["points", "max_points", "weight"],
}
_RESPONSE_COLUMNS = {
    "id": ["response_id", "id", "student_id", "student", "submission_id", "pseudonym"],
    "problem_id": ["problem_id", "problem", "question_id", "question", "q"],
    "text": ["response_text", "response", "answer", "explanation", "text"],
}


def _norm(h: str) -> str:
    return h.strip().lower().replace(" ", "_").replace("-", "_")


def _pick(headers: list[str], wanted: dict[str, list[str]]) -> dict[str, str]:
    """Map our field names to the file's actual headers."""
    by_norm = {_norm(h): h for h in headers}
    found: dict[str, str] = {}
    used: set[str] = set()
    for field, aliases in wanted.items():
        for a in aliases:
            h = by_norm.get(a)
            if h and h not in used:
                found[field] = h
                used.add(h)
                break
    return found


def _rows(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    rows = [r for r in reader if any((v or "").strip() for v in r.values())]
    return list(reader.fieldnames or []), rows


def _num(s: str) -> tuple[int, str]:
    return (int(s), "") if s.isdigit() else (10**9, s)


def load_csv(rubric_csv: str, responses_csv: str, name: str = "Uploaded exam") -> Assessment:
    r_headers, r_rows = _rows(rubric_csv)
    cols = _pick(r_headers, _RUBRIC_COLUMNS)
    if "text" not in cols:
        raise ValueError(
            f"Rubric CSV needs a criterion text column (e.g. 'criterion_text'); got {r_headers}"
        )
    if not r_rows:
        raise ValueError("Rubric CSV has no rows.")

    problems: dict[str, Problem] = {}
    criteria: list[Criterion] = []
    per_problem: dict[str, int] = {}
    for row in r_rows:
        pid = (row.get(cols.get("problem_id", ""), "") or "").strip() or "P1"
        ptext = (row.get(cols.get("problem_text", ""), "") or "").strip()
        instr = (row.get(cols.get("instructions", ""), "") or "").strip()
        p = problems.setdefault(pid, Problem(id=pid, text=ptext, instructions=instr))
        p.text = p.text or ptext            # first non-empty wins
        p.instructions = p.instructions or instr
        per_problem[pid] = per_problem.get(pid, 0) + 1
        key = (row.get(cols.get("key", ""), "") or "").strip() or str(per_problem[pid])
        text = (row.get(cols["text"]) or "").strip()
        if not text:
            continue
        points_raw = (row.get(cols.get("points", ""), "") or "").strip()
        cid = f"{pid}#{key}"
        if any(c.id == cid for c in criteria):
            raise ValueError(f"Duplicate rubric item {key!r} for problem {pid!r}.")
        criteria.append(Criterion(
            id=cid, problem_id=pid, key=key, label=f"Item {key}", text=text,
            source_text=text, points=float(points_raw) if points_raw else 1.0,
        ))

    s_headers, s_rows = _rows(responses_csv)
    scols = _pick(s_headers, _RESPONSE_COLUMNS)
    if "text" not in scols:
        raise ValueError(
            f"Responses CSV needs a response text column (e.g. 'response_text'); got {s_headers}"
        )
    responses: list[Response] = []
    seen: set[tuple[str, str]] = set()
    for i, row in enumerate(s_rows, start=1):
        pid = (row.get(scols.get("problem_id", ""), "") or "").strip()
        if not pid:
            if len(problems) != 1:
                raise ValueError(
                    "Responses CSV needs a problem_id column when the rubric has several problems."
                )
            pid = next(iter(problems))
        if pid not in problems:
            raise ValueError(f"Response row {i} refers to unknown problem {pid!r}.")
        rid = (row.get(scols.get("id", ""), "") or "").strip() or f"R{i:03d}"
        if (rid, pid) in seen:
            raise ValueError(f"Duplicate response id {rid!r} for problem {pid!r}.")
        seen.add((rid, pid))
        # Make ids unique across problems so instance ids never collide.
        full_id = rid if len(problems) == 1 else f"{pid}-{rid}"
        responses.append(Response(
            id=full_id, problem_id=pid, text=row.get(scols["text"]) or "", external_id=rid,
        ))

    if not responses:
        raise ValueError("Responses CSV has no rows.")
    return Assessment(
        name=name, source="csv", rubric_version="uploaded",
        problems=list(problems.values()), criteria=criteria, responses=responses,
    )
