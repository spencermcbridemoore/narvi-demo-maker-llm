"""The rubric lifecycle of one problem bank: draft -> approved (locked) -> next revision.

``check`` is the single source of truth for what still blocks approval and what blocks
grading. Everything that changes a rubric goes through this module, so the API, the
CLI and the tests all meet the same hard stops:

  * no approval while the bank is incomplete (every version needs problem text and
    at least one item, and every item needs text and a weight above zero);
  * no edits while the rubric is approved and locked ("Edit rubric" unlocks it);
  * no grading without an approved rubric, responses, and a known version for every
    response that is not left out.

A ``Refusal`` carries a plain sentence and the boxes still to fill. Callers show the
sentence; nothing here knows about HTTP.
"""

from __future__ import annotations

import hashlib
import json
import math
import time

from .model import Assessment, Criterion, Problem

LEGACY_SENTENCE = (
    "This problem bank was saved by an earlier version of the grader. "
    "You can view and export it, but not edit or grade it."
)
LOCKED_SENTENCE = 'The rubric is approved and locked. Choose "Edit rubric" to change it.'


class Refusal(Exception):
    """A request the bank's current state does not allow.

    kind: incomplete (approval), invalid (a value that cannot be used), locked,
    legacy, running, not_ready (responses or grading asked for too early).
    """

    def __init__(self, message: str, boxes: list[dict] | None = None, kind: str = "locked"):
        super().__init__(message)
        self.message = message
        self.boxes = boxes or []
        self.kind = kind


def norm_id(text: str) -> str:
    """Ids match "exactly, ignoring case and spaces"."""
    return "".join(text.split()).lower()


def find_version(bank: Assessment, problem_id: str) -> Problem | None:
    return next((p for p in bank.problems if p.id == problem_id), None)


def _version(bank: Assessment, problem_id: str) -> Problem:
    version = find_version(bank, problem_id)
    if version is None:
        raise Refusal(f'This bank has no version called "{problem_id}".', kind="invalid")
    return version


def valid_weight(points: float) -> bool:
    return isinstance(points, (int, float)) and math.isfinite(points) and points > 0


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _num(x: float) -> str:
    return f"{x:g}"


# --- check -------------------------------------------------------------------

def _box(box: str, message: str, problem_id: str = "", item_key: str = "") -> dict:
    return {"box": box, "problem_id": problem_id, "item_key": item_key, "message": message}


def _warnings(bank: Assessment) -> list[str]:
    """Versions of one problem should mirror each other. Differences warn, never block."""
    with_rubric = [p for p in bank.problems if bank.criteria_for(p.id)]
    if len(with_rubric) < 2:
        return []
    ref = with_rubric[0]
    ref_items = {c.key: c for c in bank.criteria_for(ref.id)}
    out: list[str] = []
    for p in with_rubric[1:]:
        items = {c.key: c for c in bank.criteria_for(p.id)}
        if set(items) != set(ref_items):
            out.append(
                f"Version {p.id} has {_plural(len(items), 'item')} ({', '.join(items)}) and version "
                f"{ref.id} has {_plural(len(ref_items), 'item')} ({', '.join(ref_items)}). "
                "Versions of one problem usually share the same items."
            )
        for key, c in items.items():
            other = ref_items.get(key)
            if other is not None and c.points != other.points:
                out.append(
                    f"Item {key} is worth {_num(c.points)} in version {p.id} and "
                    f"{_num(other.points)} in version {ref.id}."
                )
    return out


def check(bank: Assessment) -> dict:
    """What still blocks approval (``to_fill``), what blocks grading
    (``grade_blockers``), and warnings. Computed from the bank as it is now."""
    legacy = bank.kind != "bank"
    approved = bank.rubric_status == "approved"
    to_fill: list[dict] = []
    without_rubric: list[str] = []

    if legacy:
        to_fill.append(_box("bank", LEGACY_SENTENCE))
    else:
        if not bank.problems:
            to_fill.append(_box("bank", "This bank has no version yet. Add one."))
        for p in bank.problems:
            if not p.text.strip():
                to_fill.append(_box("version_text", f"Version {p.id} needs its problem text.", p.id))
            items = bank.criteria_for(p.id)
            if not items:
                without_rubric.append(p.id)
                to_fill.append(_box("items", f"Version {p.id} needs at least one rubric item.", p.id))
            for c in items:
                if not c.text.strip():
                    to_fill.append(_box(
                        "item_text", f"Item {c.key} of version {p.id} needs its text.", p.id, c.key))
                if not valid_weight(c.points):
                    to_fill.append(_box(
                        "weight", f"Item {c.key} of version {p.id} needs a weight above zero.",
                        p.id, c.key))

    active = [r for r in bank.responses if not r.excluded]
    per_version = {p.id: 0 for p in bank.problems}
    unknown = [r for r in active if r.problem_id not in per_version]
    for r in active:
        if r.problem_id in per_version:
            per_version[r.problem_id] += 1

    blockers: list[dict] = []
    if legacy:
        blockers.append({"step": "rubric", "message": LEGACY_SENTENCE})
    else:
        if not approved:
            blockers.append({
                "step": "rubric",
                "message": "The rubric is not approved yet. Approve it on the Rubric step.",
            })
        if not bank.responses:
            blockers.append({"step": "responses", "message": "There are no responses yet."})
        elif not active:
            blockers.append({
                "step": "responses",
                "message": "Every response is left out, so there is nothing to grade.",
            })
        if unknown:
            n = len(unknown)
            blockers.append({
                "step": "responses",
                "message": (
                    f"{_plural(n, 'response does', 'responses do')} not have a version yet. "
                    "Give each one a version, or leave it out."
                ),
                "response_ids": [r.id for r in unknown],
            })

    with_rubric = len(bank.problems) - len(without_rubric)
    return {
        "legacy": legacy,
        "editable": not legacy and not approved,
        "can_approve": not legacy and not approved and not to_fill,
        "can_grade": not blockers,
        "to_fill": to_fill,
        "grade_blockers": blockers,
        "warnings": [] if legacy else _warnings(bank),
        # none | some | all versions have a rubric (Chen's steps 5 and 6)
        "rubric_state": "all" if not without_rubric else ("some" if with_rubric else "none"),
        "without_rubric": without_rubric,
        "counts": {
            "versions": len(bank.problems),
            "items": len(bank.criteria),
            "responses": len(bank.responses),
            "excluded": len(bank.responses) - len(active),
            "unknown_version": len(unknown),
            "per_version": per_version,
        },
    }


# --- hash, approve, unlock ---------------------------------------------------

def rubric_copy(bank: Assessment) -> dict:
    """The versions and items exactly as a run grades with them."""
    return {"versions": [
        {
            "id": p.id, "text": p.text, "instructions": p.instructions,
            "items": [
                c.model_dump(include={"id", "key", "label", "text", "accept", "reject",
                                      "points", "origin"})
                for c in bank.criteria_for(p.id)
            ],
        }
        for p in bank.problems
    ]}


def spec_hash(bank: Assessment) -> str:
    """Hash of everything the grader is told: each version's id, text and
    instructions, and each item's key, text, accept, reject and weight."""
    spec = [
        {
            "id": p.id, "text": p.text.strip(), "instructions": p.instructions.strip(),
            "items": [
                {"key": c.key, "text": c.text.strip(), "accept": c.accept, "reject": c.reject,
                 "weight": float(c.points)}
                for c in bank.criteria_for(p.id)
            ],
        }
        for p in bank.problems
    ]
    blob = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def approve(bank: Assessment) -> dict:
    """Lock the rubric. A revision is a change to an approved rubric, so the revision
    number goes up only when the rubric differs from the last approved one."""
    if bank.kind != "bank":
        raise Refusal(LEGACY_SENTENCE, kind="legacy")
    result = check(bank)
    if bank.rubric_status == "approved":
        return result
    if result["to_fill"]:
        n = len(result["to_fill"])
        raise Refusal(
            f"The rubric cannot be approved yet: {_plural(n, 'box is', 'boxes are')} still empty.",
            boxes=result["to_fill"], kind="incomplete",
        )
    new_hash = spec_hash(bank)
    if bank.rubric_revision == 0 or new_hash != bank.rubric_hash:
        bank.rubric_revision += 1
    bank.rubric_hash = new_hash
    bank.rubric_status = "approved"
    bank.rubric_approved_at = time.time()
    return check(bank)


def unlock(bank: Assessment) -> None:
    """Back to a draft, to edit towards the next revision. Responses stay attached."""
    if bank.kind != "bank":
        raise Refusal(LEGACY_SENTENCE, kind="legacy")
    bank.rubric_status = "draft"


def require_draft(bank: Assessment) -> None:
    if bank.kind != "bank":
        raise Refusal(LEGACY_SENTENCE, kind="legacy")
    if bank.rubric_status == "approved":
        raise Refusal(LOCKED_SENTENCE, kind="locked")


# --- version edits (draft only) ----------------------------------------------

def unique_id(wanted: str, taken: set[str]) -> str:
    """``wanted``, or ``wanted-2``, ``wanted-3`` ... if it is already in use."""
    if norm_id(wanted) not in taken:
        return wanted
    n = 2
    while norm_id(f"{wanted}-{n}") in taken:
        n += 1
    return f"{wanted}-{n}"


def next_version_id(taken: set[str], position: int) -> str:
    """``v<position>``, moving on to the next free number if that id is in use."""
    n = position
    while norm_id(f"v{n}") in taken:
        n += 1
    return f"v{n}"


def _reorder(bank: Assessment) -> None:
    """Keep criteria grouped in version order."""
    bank.criteria = [c for p in bank.problems for c in bank.criteria_for(p.id)]


def add_version(bank: Assessment, new_id: str = "", text: str = "",
                instructions: str = "") -> Problem:
    require_draft(bank)
    taken = {norm_id(p.id) for p in bank.problems}
    new_id = new_id.strip()
    if not new_id:
        new_id = next_version_id(taken, len(bank.problems) + 1)
    elif norm_id(new_id) in taken:
        raise Refusal(f'Another version is already called "{new_id}". Pick a different name.',
                      kind="invalid")
    version = Problem(id=new_id, text=text, instructions=instructions)
    bank.problems.append(version)
    return version


def edit_version(bank: Assessment, problem_id: str, new_id: str | None = None,
                 text: str | None = None, instructions: str | None = None) -> Problem:
    require_draft(bank)
    version = _version(bank, problem_id)
    rename = new_id is not None and new_id.strip() != version.id
    if rename:      # refuse before anything is changed
        new_id = new_id.strip()
        if not new_id:
            raise Refusal("A version needs a name.", kind="invalid")
        if any(p is not version and norm_id(p.id) == norm_id(new_id) for p in bank.problems):
            raise Refusal(f'Another version is already called "{new_id}". Pick a different name.',
                          kind="invalid")
    if text is not None:
        version.text = text
    if instructions is not None:
        version.instructions = instructions
    if rename:
        for c in bank.criteria:
            if c.problem_id == version.id:
                c.problem_id, c.id = new_id, f"{new_id}#{c.key}"
        for r in bank.responses:            # the same version under a new name
            if r.problem_id == version.id:
                r.problem_id = new_id
        for f in bank.findings:             # keep "filled in for you" marks on their boxes
            if f.problem_id == version.id:
                f.problem_id = new_id
        version.id = new_id
    return version


def remove_version(bank: Assessment, problem_id: str) -> int:
    """Remove a version and its items. Responses that were matched to it go back to
    "version unknown" (they are kept). Returns how many responses that was."""
    require_draft(bank)
    version = _version(bank, problem_id)
    if len(bank.problems) == 1:
        raise Refusal("A problem bank needs at least one version. Edit this one instead.",
                      kind="invalid")
    bank.problems.remove(version)
    bank.criteria = [c for c in bank.criteria if c.problem_id != problem_id]
    orphaned = [r for r in bank.responses if r.problem_id == problem_id]
    for r in orphaned:
        r.problem_id = ""
    return len(orphaned)


# --- item edits (draft only) -------------------------------------------------

def next_item_key(taken: set[str]) -> str:
    """The lowest item number that is not in use."""
    n = 1
    while str(n) in taken:
        n += 1
    return str(n)


def set_items(bank: Assessment, problem_id: str, items: list[dict]) -> list[Criterion]:
    """Replace one version's items. Each item: key (optional), text, points
    (None = not set yet), accept, reject."""
    require_draft(bank)
    version = _version(bank, problem_id)
    before = {c.key: c for c in bank.criteria_for(version.id)}
    # Keys that are given are kept (a repeat gets a suffix); only then are the items
    # without a key numbered, so a new item never takes an existing item's number.
    keys: list[str] = []
    taken: set[str] = set()
    for item in items:
        key = str(item.get("key") or "").strip()
        key = unique_id(key, taken) if key else ""
        keys.append(key)
        taken.add(norm_id(key))
    taken.discard("")
    after: list[Criterion] = []
    for item, key in zip(items, keys):
        if not key:
            key = next_item_key(taken)
            taken.add(norm_id(key))
        text = str(item.get("text") or "")
        points = item.get("points")
        if not isinstance(points, (int, float)) or isinstance(points, bool) or not math.isfinite(points):
            points = 0.0
        old = before.get(key)
        unchanged = old is not None and old.text.strip() == text.strip()
        # The teacher's own wording follows their edits, unless a distinct original
        # (e.g. the simple wording behind Chen's detailed one) is already kept.
        keeps_original = old is not None and old.source_text.strip() not in ("", old.text.strip())
        after.append(Criterion(
            id=f"{version.id}#{key}", problem_id=version.id, key=key, label=f"Item {key}",
            text=text, points=float(points),
            accept=[str(a) for a in item.get("accept") or []],
            reject=[str(r) for r in item.get("reject") or []],
            source_text=old.source_text if keeps_original else text,
            origin=old.origin if unchanged else "teacher",
        ))
    bank.criteria = [c for c in bank.criteria if c.problem_id != version.id] + after
    _reorder(bank)
    return after


def copy_items(bank: Assessment, from_id: str, to_ids: list[str] | None = None) -> list[str]:
    """Copy one version's items, unchanged, to versions that have no rubric yet.
    Copies are marked ``origin="copied"``. Returns the versions that received them."""
    require_draft(bank)
    source = _version(bank, from_id)
    items = bank.criteria_for(source.id)
    if not items:
        raise Refusal(f"Version {source.id} has no rubric items to copy.", kind="invalid")
    targets = [p for p in bank.problems if p.id != source.id and not bank.criteria_for(p.id)]
    if to_ids is not None:
        targets = [p for p in targets if p.id in to_ids]
    for p in targets:
        bank.criteria.extend(
            c.model_copy(update={"id": f"{p.id}#{c.key}", "problem_id": p.id, "origin": "copied"},
                         deep=True)
            for c in items
        )
    _reorder(bank)
    return [p.id for p in targets]
