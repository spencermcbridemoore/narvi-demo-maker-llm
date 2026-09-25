"""Internal data model.

Every input format (the Chen & Wan kit, a teacher's CSV, later a free-text rubric)
is translated into these objects by an adapter; nothing downstream knows where the
data came from. Ids are opaque strings — never parse meaning out of them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Problem(BaseModel):
    id: str
    text: str
    instructions: str = ""  # what students were told to write, if known


class Criterion(BaseModel):
    """One binary rubric item: the response earns it (1) or doesn't (0)."""

    id: str                 # unique within the assessment, e.g. "Q1#1"
    problem_id: str
    key: str                # per-problem key; instance ids are "<response_id>#<key>"
    label: str              # display label, e.g. "Item 1"
    text: str               # the wording the grader uses (editable in review)
    source_text: str = ""   # the teacher's original wording, kept for reference
    points: float = 1.0


class Response(BaseModel):
    id: str                 # opaque; a one-time pseudonym is fine
    problem_id: str
    text: str
    external_id: str = ""   # the id exactly as uploaded (before any problem prefix)


class Assessment(BaseModel):
    id: str = ""
    name: str
    source: str             # "example:chen" | "csv"
    rubric_version: str = ""
    problems: list[Problem]
    criteria: list[Criterion]
    responses: list[Response]
    # instance_id -> {"ref_a": 0/1, "ref_b": 0/1}. Evaluation only; never sent to the model.
    references: dict[str, dict[str, int]] = Field(default_factory=dict)
    created: float = 0.0

    def problem(self, problem_id: str) -> Problem:
        return next(p for p in self.problems if p.id == problem_id)

    def criteria_for(self, problem_id: str) -> list[Criterion]:
        return [c for c in self.criteria if c.problem_id == problem_id]

    def instances(
        self,
        problem_ids: list[str] | None = None,
        max_responses: int | None = None,
    ) -> list[tuple[Response, Criterion]]:
        """Every (response, criterion) pair to grade, optionally narrowed."""
        out: list[tuple[Response, Criterion]] = []
        seen: dict[str, int] = {}
        for r in self.responses:
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
