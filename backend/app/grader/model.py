"""Internal data model.

Every input format (the Chen & Wan kit, a Problem Info CSV, later a free-text rubric)
is translated into these objects by an adapter; nothing downstream knows where the
data came from. Ids are opaque strings — never parse meaning out of them.

An ``Assessment`` is one **problem bank**: one problem in one or more isomorphic
versions. Its ``problems`` are the versions. Every field added after the MVP has a
default, so files saved by the MVP still load (they come back as ``kind="legacy"``).

A rubric item either belongs to one version or, with an empty ``problem_id``, is
shared by every version (the default since Phase 2b). A version's own wording of a
shared item is an item of that version with the same key. ``criteria_for`` gives
what a version is graded with.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, model_validator

SHARED = ""     # the problem_id of a rubric item shared by every version
# Phase 2 kept acceptable forms and exclusions in two lists. They load as one list of
# specifications, each line saying which it was.
ALSO_COUNTS = "Also counts: "
DOES_NOT_COUNT = "Does not count: "


def every_version(n: int) -> str:
    """Every version, in a sentence: "both versions" or "all 20 versions"."""
    return "both versions" if n == 2 else f"all {n} versions"


def clean_text(text: str) -> str:
    """Text as it can be saved. A lone surrogate (half of an emoji, which a paste or
    a cut-off string can carry) cannot be written as UTF-8, so it becomes U+FFFD."""
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


# For text that arrives from outside (request bodies): cleaned when it is validated.
Text = Annotated[str, AfterValidator(clean_text)]


def text_fingerprint(text: str) -> str:
    """A short hash of a response's exact text."""
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def one_list(data):
    """An item saved with Phase 2's two lists (``accept``, ``reject``) as one list of
    specifications. A line the AI wrote stays marked as the AI's."""
    if not isinstance(data, dict) or not {"accept", "reject"} & data.keys():
        return data
    data = dict(data)
    accept = [str(a) for a in data.pop("accept", None) or []]
    reject = [str(r) for r in data.pop("reject", None) or []]
    if "specifications" not in data:
        lines = [(ALSO_COUNTS + a, a) for a in accept] + [(DOES_NOT_COUNT + r, r) for r in reject]
        data["specifications"] = [line for line, _ in lines]
        if "ai_lines" in data:
            written = set(data["ai_lines"] or [])
            data["ai_lines"] = [line for line, old in lines if old in written]
    return data


class Problem(BaseModel):
    """One version of the bank's problem."""

    id: str
    text: str = ""          # may be empty after a soft upload; editable while the rubric is a draft
    instructions: str = ""  # what students were told to write, if known


class Criterion(BaseModel):
    """One binary rubric item: the response earns it (1) or doesn't (0)."""

    id: str                 # unique within the bank: "v1#1", or "*#1" for a shared item
    problem_id: str         # the version this item belongs to; SHARED ("") = every version
    key: str                # instance ids are "<response_id>#<key>"
    label: str              # display label, e.g. "Item 1"
    text: str = ""          # the statement the grader is told
    source_text: str = ""   # the teacher's original wording, kept for reference
    points: float = 1.0     # the weight; 0 means "not set yet"
    # What the item takes to earn, one line each: requirements, forms that also count,
    # near misses that do not (Chen's "specifications").
    specifications: list[str] = Field(default_factory=list)
    origin: str = "teacher"  # who wrote the statement: teacher | copied | ai | ai_inferred
    # Specification lines exactly as an accepted AI draft wrote them. A line stays
    # marked as the AI's while it still reads the same.
    ai_lines: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def two_lists_to_one(cls, data):
        return one_list(data)


class Response(BaseModel):
    id: str                 # opaque; a one-time pseudonym is fine
    problem_id: str = ""    # the version it answers; "" means "version unknown"
    text: str = ""
    external_id: str = ""   # the id exactly as uploaded
    version_label: str = ""  # the version exactly as uploaded
    excluded: bool = False  # deliberately left out of grading


class Finding(BaseModel):
    """Something intake noticed in an upload. Findings are history: the live
    ``rubric.check`` decides what currently blocks approval or grading."""

    code: str                                   # stable tag, e.g. "weight_filled"
    message: str                                # one plain sentence
    filled: Literal["code", "owner", "note"]    # code filled it | the owner must | just a note
    scope: Literal["rubric", "responses"] = "rubric"
    # Which box it belongs to: version_id, version_text, items, item_key, item_text,
    # weight, student_id, response_version, or file (the upload as a whole).
    box: str = "file"
    problem_id: str = ""
    item_key: str = ""
    response_id: str = ""
    value: str = ""         # what code filled in; the mark shows while the box still holds it
    row: int | None = None  # spreadsheet row, for the details view only


class ProposedItem(BaseModel):
    """One rubric item as the AI drafted it, waiting for the owner."""

    key: str                # from code, never from the model
    text: str = ""          # the drafted statement; "" when the item's own statement stays as it is
    points: float = 1.0     # from code: the example's weight, the item's own, or the material's (else 1)
    specifications: list[str] = Field(default_factory=list)
    source_text: str = ""   # what it restates: the example item's wording, or words found in the pasted material
    inferred: bool = False  # nothing of the owner's backs it, so it is accepted on its own
    note: str = ""          # the AI's remark on this item
    base: str = ""          # fingerprint of the statement it was drafted for ("" = the item did not exist)

    @model_validator(mode="before")
    @classmethod
    def two_lists_to_one(cls, data):
        return one_list(data)


class Proposal(BaseModel):
    """An AI draft for one version. It is kept apart from ``criteria``: the check, the
    hash, runs and exports never read it, so nothing in it is used until the owner
    accepts it (``rubric.accept_proposal``)."""

    id: str
    mode: str                           # example | material | text | elaborate (see draft.py)
    problem_id: str                     # where its items go: a version, or SHARED (the shared rubric)
    example_id: str = ""                # mode "example": the version adapted from ("" = the shared rubric)
    version_text: str = ""              # a problem text taken from the pasted material, for a version without one
    version_text_found: bool = False    # ... and code found it word for word in that material
    text_for: str = ""                  # the version that problem text is for ("" = problem_id)
    items: list[ProposedItem] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)     # every drafted key, in order, to place an accepted item
    notes: list[str] = Field(default_factory=list)     # the AI's own notes: where it filled in or was unsure
    remarks: list[str] = Field(default_factory=list)   # what code noticed about the draft
    reported: bool = False              # its notes have been added to the bank's findings
    model: str = ""
    prompt_version: str = ""
    created: float = 0.0
    inputs: str = ""                    # fingerprint of what it was drafted from (draft.inputs_print)


class Assessment(BaseModel):
    id: str = ""
    name: str
    source: str             # "example:chen" | "csv"
    kind: str = "legacy"    # "bank" for everything created from now on
    rubric_version: str = ""  # the example's rubric wording, or "uploaded"
    problems: list[Problem] = Field(default_factory=list)
    criteria: list[Criterion] = Field(default_factory=list)
    responses: list[Response] = Field(default_factory=list)
    # instance_id -> {"ref_a": 0/1, "ref_b": 0/1}. Evaluation only; never sent to the model.
    references: dict[str, dict[str, int]] = Field(default_factory=dict)
    created: float = 0.0

    rubric_status: str = "draft"            # draft | approved (locked)
    rubric_hash: str = ""                   # of the last approved rubric (rubric.spec_hash)
    rubric_revision: int = 0                # 0 until the first approval
    rubric_approved_at: float | None = None
    findings: list[Finding] = Field(default_factory=list)
    held_responses_csv: str = ""            # from the all-at-once shortcut; untouched until approval
    ai_fill: bool = False                   # "let the AI fill gaps": draft empty rubrics without asking
    proposals: list[Proposal] = Field(default_factory=list)    # AI drafts waiting for the owner
    material: str = ""                      # pasted material a draft was made from; dropped at approval

    def shared_items(self) -> list[Criterion]:
        """The rubric every version shares."""
        return [c for c in self.criteria if c.problem_id == SHARED]

    def own_items(self, problem_id: str) -> list[Criterion]:
        """The items stored for one version (or, with SHARED, the shared ones)."""
        return [c for c in self.criteria if c.problem_id == problem_id]

    def criteria_for(self, problem_id: str) -> list[Criterion]:
        """What one version is graded with: the shared items, with the version's own
        wording in place of a shared item where it has one, then any items of its own
        that are not shared. A bank without shared items gives each version its own."""
        own = self.own_items(problem_id)
        shared = self.shared_items()
        if not shared:
            return own
        mine = {c.key: c for c in own}
        keys = {c.key for c in shared}
        return [mine.get(c.key, c) for c in shared] + [c for c in own if c.key not in keys]

    def item_count(self) -> int:
        """How many rubric items the bank has. A version's own wording of a shared
        item is that item, not another one."""
        keys = {c.key for c in self.shared_items()}
        return sum(1 for c in self.criteria if c.problem_id == SHARED or c.key not in keys)

    def text_for(self, proposal: "Proposal") -> str:
        """The version a drafted problem text is for."""
        return proposal.text_for or proposal.problem_id

    def instances(
        self,
        problem_ids: list[str] | None = None,
        max_responses: int | None = None,
    ) -> list[tuple[Response, Criterion]]:
        """Every (response, criterion) pair to grade, optionally narrowed. Responses
        that are left out, or whose version is unknown, are never graded."""
        out: list[tuple[Response, Criterion]] = []
        seen: dict[str, int] = {}
        for r in self.responses:
            if r.excluded or not r.problem_id:
                continue
            if problem_ids and r.problem_id not in problem_ids:
                continue
            seen[r.problem_id] = seen.get(r.problem_id, 0) + 1
            if max_responses and seen[r.problem_id] > max_responses:
                continue
            out.extend((r, c) for c in self.criteria_for(r.problem_id))
        return out


def instance_id(response: Response, criterion: Criterion) -> str:
    return f"{response.id}#{criterion.key}"


class Sample(BaseModel):
    """One model call's judgment of one (response, criterion) pair."""

    verdict: int | None = None          # 1 credit, 0 no credit, None = the call failed
    comparison: str = ""
    evidence: list[str] = Field(default_factory=list)
    quotes_found: bool | None = None    # None when there were no quotes to check
    error: str | None = None
    cached: bool = False


class Cell(BaseModel):
    """All samples for one (response, criterion) pair, resolved to a binary final."""

    instance_id: str
    response_id: str
    criterion_id: str
    samples: list[Sample] = Field(default_factory=list)
    final: int | None = None            # always 0/1 once any sample succeeded
    confidence: float | None = None     # share of votes agreeing with the majority
    votes: str = ""                     # e.g. "3/3", "2/3"
    flags: list[str] = Field(default_factory=list)
    resolved_by: str = ""               # majority | tie_default | teacher
    override: int | None = None         # teacher's decision, wins over the model

    def compact(self) -> dict:
        return self.model_dump(exclude={"samples"})
