"""Rubric drafting by AI: one module, three ways in, one kind of output.

  * ``example``: from one version's rubric (the example), draft another version's.
    For a version that already has items it only carries the example's acceptable
    forms and exclusions over to them.
  * ``material``: from pasted unstructured material, draft a version's items and find
    its problem text. ``text`` is its weakest form: from the problem text alone.
  * ``elaborate``: for a version's own items, suggest acceptable forms and "does not
    count" exclusions.

Every draft is a ``Proposal``, stored next to the rubric and never in it. Nothing
the model writes is used until the owner accepts it (``rubric.accept_proposal``).

The model writes wording. Code decides everything else:
  * item keys, weights, order and the number of items (the example's, or the
    version's own);
  * what is "inferred": a drafted item counts as backed only when code finds the
    words it quotes in the pasted material;
  * whether a problem text really was taken word for word from that material.

The drafting model is shown versions and rubric items only. No function here takes
a response, and nothing here reads one. Text the owner supplied goes into the
prompt inside blocks closed by a tag made for that one call: it is data, not
instructions. The prompts are data too (``prompts/draft_*.txt``), and
``DRAFT_PROMPT_VERSION`` hashes them with the output schemas. The grading prompts
are not touched by any of this.

A draft runs in the background, one model call per version. Its progress is kept
in memory only: a restart loses a draft in flight and nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

from pydantic import BaseModel, Field

from ..llm.factory import Role, get_model
from . import rubric, store
from .adapters import NothingToLoad
# The same forgiving comparison the grader uses to check its quotes.
from .aggregate import _ELLIPSIS, _normalize
from .grade import _RETRYABLE, _fill, _usage, criterion_text
from .model import Assessment, Criterion, Finding, Problem, Proposal, ProposedItem, clean_text

MODES = ("example", "material", "text", "elaborate")
EFFORT = os.getenv("GRADER_DRAFT_EFFORT", "medium")     # reasoning effort of the drafting model
MATERIAL_LIMIT = 60_000     # characters of pasted material that are used
MAX_ITEMS = 10              # items in a rubric drafted from material
MAX_LINES = 5               # acceptable forms, or exclusions, suggested for one item
_LINE_LIMIT = 400           # characters in one drafted line
_TEXT_LIMIT = 1500          # characters in one drafted statement
_CONCURRENCY = 3            # simultaneous model calls of one draft

_PROMPTS = Path(__file__).parent / "prompts"
SYSTEM_TEMPLATE = (_PROMPTS / "draft_system.txt").read_text(encoding="utf-8").strip()
TEMPLATES = {
    mode: (_PROMPTS / f"draft_{mode}.txt").read_text(encoding="utf-8").strip() for mode in MODES
}


# --- what the model must return ---------------------------------------------

class ExampleItem(BaseModel):
    item_key: str = Field(description="The item_key of the example item this adapts.")
    statement: str = Field(description="The item as it reads for the target version.")
    acceptable_forms: list[str] = Field(
        description="The example item's acceptable forms, adapted. Empty if it has none.")
    does_not_count: list[str] = Field(
        description="The example item's exclusions, adapted. Empty if it has none.")
    note: str = Field(
        description="Empty, unless the item has no real counterpart in the target version.")


class ExampleDraft(BaseModel):
    """A rubric adapted from the example version to another version."""

    items: list[ExampleItem]
    notes: list[str] = Field(description="What was missing or unclear, one plain sentence each.")


class MaterialItem(BaseModel):
    """Field order is deliberate: quote the material first, then write the item."""

    source_quotes: list[str] = Field(
        description="Exact words of the material that state or show this requirement, copied "
        "character for character. Empty if the material does not contain it.")
    statement: str = Field(description="What the response must show to earn the item.")
    acceptable_forms: list[str] = Field(description="Only forms the material itself supports.")
    does_not_count: list[str] = Field(description="Only exclusions the material itself supports.")
    weight_in_material: str = Field(
        description="The points the material gives this item, as written. Empty if none.")
    note: str = Field(description="Empty, unless the instructor should know something about this item.")


class MaterialDraft(BaseModel):
    """A rubric drafted from pasted material, or from the problem text alone."""

    problem_text: str = Field(
        description="The problem statement copied word for word from the material, or empty.")
    items: list[MaterialItem]
    notes: list[str] = Field(description="What was missing or unclear, one plain sentence each.")


class ElaborateItem(BaseModel):
    item_key: str = Field(description="The item_key of the rubric item these belong to.")
    acceptable_forms: list[str]
    does_not_count: list[str]
    note: str = Field(description="Empty, unless you would have to decide what the instructor meant.")


class ElaborateDraft(BaseModel):
    """Acceptable forms and exclusions suggested for a version's own items."""

    items: list[ElaborateItem]
    notes: list[str] = Field(description="What was missing or unclear, one plain sentence each.")


DRAFT_PROMPT_VERSION = hashlib.sha1(
    (SYSTEM_TEMPLATE + "".join(TEMPLATES[mode] for mode in MODES) + "".join(
        json.dumps(schema.model_json_schema(), sort_keys=True)
        for schema in (ExampleDraft, MaterialDraft, ElaborateDraft))).encode()
).hexdigest()[:10]


# --- the prompts: versions and items in, never a response --------------------

def _nonce(*texts: str) -> str:
    """A code for this call's block tags that none of the owner's text contains, so
    nothing in that text can close a block."""
    while True:
        nonce = uuid.uuid4().hex[:8]
        if not any(nonce in text for text in texts):
            return nonce


def _block(tag: str, text: str, nonce: str) -> str:
    return f"<{tag}-{nonce}>\n{text}\n</{tag}-{nonce}>"


def _instructions(problem: Problem, nonce: str) -> str:
    if not problem.instructions.strip():
        return ""
    return ("WHAT STUDENTS WERE ASKED TO WRITE\n"
            + _block("instructions", problem.instructions.strip(), nonce) + "\n")


def _items_json(items: list[Criterion]) -> str:
    return json.dumps([
        {
            "item_key": c.key, "statement": c.text.strip(),
            "acceptable_forms": [a.strip() for a in c.accept if a.strip()],
            "does_not_count": [r.strip() for r in c.reject if r.strip()],
        }
        for c in items
    ], ensure_ascii=False, indent=1)


def _owner_text(*problems: Problem, items: Iterable[Criterion] = (), more: str = "") -> list[str]:
    texts = [more]
    for p in problems:
        texts += [p.text, p.instructions]
    for c in items:
        texts += [c.key, c.text, *c.accept, *c.reject]
    return texts


def example_messages(example: Problem, example_items: list[Criterion], target: Problem,
                     target_items: list[Criterion]) -> list[tuple[str, str]]:
    nonce = _nonce(*_owner_text(example, target, items=[*example_items, *target_items]))
    own = ""
    if target_items:
        own = ("\nITEMS THE INSTRUCTOR ALREADY WROTE FOR THE TARGET VERSION\n"
               "Their statements stay exactly as they are. For an item listed here, return the "
               "instructor's statement unchanged, and adapt only the example item's "
               "acceptable_forms and does_not_count to it.\n"
               + _block("rubric", _items_json(target_items), nonce))
    user = _fill(
        TEMPLATES["example"],
        example_problem=_block("problem", example.text.strip(), nonce),
        example_instructions=_instructions(example, nonce),
        example_items=_block("rubric", _items_json(example_items), nonce),
        target_problem=_block("problem", target.text.strip(), nonce),
        target_instructions=_instructions(target, nonce),
        target_items=own,
    )
    return [("system", SYSTEM_TEMPLATE), ("human", user.strip())]


def material_messages(target: Problem, material: str) -> list[tuple[str, str]]:
    nonce = _nonce(*_owner_text(target, more=material))
    has_text = bool(target.text.strip())
    user = _fill(
        TEMPLATES["material"],
        problem_task=(
            "problem_text: leave it empty. The instructor has already entered the problem "
            "statement, shown below."
            if has_text else
            "problem_text: copy the problem statement word for word from the material. Leave it "
            "empty if the material has none. Never write, complete or tidy up a problem statement "
            "yourself."),
        max_items=str(MAX_ITEMS),
        problem=("PROBLEM (already entered by the instructor)\n"
                 + _block("problem", target.text.strip(), nonce) + "\n\n") if has_text else "",
        material=_block("material", material, nonce),
    )
    return [("system", SYSTEM_TEMPLATE), ("human", user)]


def text_messages(target: Problem) -> list[tuple[str, str]]:
    nonce = _nonce(*_owner_text(target))
    user = _fill(
        TEMPLATES["text"], max_items=str(MAX_ITEMS),
        problem="PROBLEM\n" + _block("problem", target.text.strip(), nonce),
    )
    return [("system", SYSTEM_TEMPLATE), ("human", user)]


def elaborate_messages(target: Problem, items: list[Criterion]) -> list[tuple[str, str]]:
    nonce = _nonce(*_owner_text(target, items=items))
    user = _fill(
        TEMPLATES["elaborate"], max_lines=str(MAX_LINES),
        problem=_block("problem", target.text.strip(), nonce),
        instructions=_instructions(target, nonce),
        items=_block("rubric", _items_json(items), nonce),
    )
    return [("system", SYSTEM_TEMPLATE), ("human", user)]


# --- the model call ----------------------------------------------------------

@lru_cache(maxsize=4)
def drafting_model(effort: str | None = None):
    """The rubric-role model, with reasoning effort overridden when the profile uses it."""
    base = get_model(Role.RUBRIC)
    current = getattr(base, "reasoning_effort", None)
    if effort and current is not None and effort != current:
        return base.model_copy(update={"reasoning_effort": effort})
    return base


def model_name() -> str:
    m = get_model(Role.RUBRIC)
    return (getattr(m, "deployment_name", None) or getattr(m, "model_name", None)
            or getattr(m, "model", None) or type(m).__name__)


async def draft_once(messages: list[tuple[str, str]], schema: type[BaseModel],
                     attempts: int = 3) -> tuple[BaseModel | None, str, dict[str, int]]:
    """One structured call, with backoff on rate limits and timeouts. Never raises:
    the answer comes back parsed, or as ``None`` with what went wrong."""
    usage: dict[str, int] = {}
    try:
        runnable = drafting_model(EFFORT).with_structured_output(
            schema, method="json_schema", include_raw=True)
    except Exception as exc:    # e.g. the provider is not configured
        return None, f"{type(exc).__name__}: {exc}"[:300], usage
    last = "unknown error"
    for attempt in range(attempts):
        try:
            out = await runnable.ainvoke(messages)
        except Exception as exc:  # provider errors vary; classify by name
            last = f"{type(exc).__name__}: {exc}"
            if type(exc).__name__ in _RETRYABLE and attempt < attempts - 1:
                await asyncio.sleep(min(30.0, 2 ** (attempt + 1) + random.random()))
                continue
            return None, last[:300], usage
        usage = _usage(out.get("raw"))
        parsed = out.get("parsed")
        if parsed is not None:
            return parsed, "", usage
        last = f"unparseable output: {out.get('parsing_error')}"
    return None, last[:300], usage


# --- what code decides about the model's answer ------------------------------

_SPACE = re.compile(r"\s+")
_BULLET = re.compile(r"^[-*•]+\s+")
_BLANK_LINES = re.compile(r"\n{3,}")
_NUMBER = re.compile(r"\d+\.\d+|\.\d+|\d+")
# Text in pasted material that talks to an AI instead of describing the problem.
_AI_DIRECTED = re.compile(
    r"\b(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+|your\s+)*"
    r"(previous\s+|prior\s+|above\s+|earlier\s+)?(instructions|rules|prompt)\b"
    r"|\byou\s+are\s+(now\s+)?(an?\s+)?(ai|assistant|language\s+model|chatbot)\b"
    r"|\bsystem\s+prompt\b|\bas\s+an\s+ai\b",
    re.IGNORECASE,
)


def _line(value) -> str:
    """One drafted line as it is stored: on a single line, without a bullet mark."""
    return _BULLET.sub("", _SPACE.sub(" ", clean_text(str(value or ""))).strip())[:_LINE_LIMIT].strip()


def _text(value, limit: int = _TEXT_LIMIT) -> str:
    """A drafted statement or passage as it is stored. Line breaks are kept."""
    text = clean_text(str(value or "")).replace("\r\n", "\n").replace("\r", "\n").strip()
    return _BLANK_LINES.sub("\n\n", text)[:limit].strip()


def _lines(values, limit: int, have: Iterable[str] = ()) -> list[str]:
    """Drafted lines, cleaned, without repeats and without lines the item already has."""
    seen = {_normalize(line) for line in have}
    out: list[str] = []
    for value in values or []:
        line = _line(value)
        if line and _normalize(line) not in seen:
            seen.add(_normalize(line))
            out.append(line)
    return out[:limit]


def found_in(text: str, source: str, shortest: int = 12) -> bool:
    """Is ``text`` in ``source`` word for word? Compared piece by piece, the way the
    grader checks its quotes: case, spacing and quote marks do not matter, and "..."
    joins passages. A passage too short to prove anything does not count."""
    haystack = _normalize(source)
    pieces = [
        piece for line in text.splitlines() for part in _ELLIPSIS.split(line)
        if (piece := _normalize(part))
    ]
    return sum(len(piece) for piece in pieces) >= shortest and all(p in haystack for p in pieces)


def _points(written: str) -> float | None:
    """The weight the material gives an item, or None when it gives no number above zero."""
    m = _NUMBER.search(written or "")
    value = float(m.group()) if m else 0.0
    return value if rubric.valid_weight(value) else None


def _by_key(returned: list, known: list[Criterion], remarks: list[str]) -> dict:
    """What the model returned, by the item it belongs to. The key only lines an answer
    up with an item: an answer for an item that does not exist is left out, and so is
    a second answer for the same item."""
    keys = {rubric.norm_id(c.key): c.key for c in known}
    out: dict = {}
    for answer in returned:
        key = rubric.norm_id(str(answer.item_key))
        if key not in keys:
            remarks.append(f'The AI returned something for an item "{_line(answer.item_key)[:40]}", '
                           "which does not exist. It was left out.")
        elif key in out:
            remarks.append(f"The AI returned item {keys[key]} twice. The first one was kept.")
        else:
            out[key] = answer
    return out


def _proposal(mode: str, target: Problem, items: list[ProposedItem], inputs: str, **more) -> Proposal:
    return Proposal(
        id=uuid.uuid4().hex[:12], mode=mode, problem_id=target.id, items=items,
        order=[i.key for i in items], prompt_version=DRAFT_PROMPT_VERSION, created=time.time(),
        inputs=inputs, **more,
    )


def from_example(example: Problem, example_items: list[Criterion], target: Problem,
                 target_items: list[Criterion], out: ExampleDraft, inputs: str = "") -> Proposal:
    """The draft for ``target``. Keys, weights, order and the number of items are the
    example's, whatever the model returned. Acceptable forms and exclusions are kept
    only where the example item has them, and no more of them than it has: the AI
    adapts, it does not add. Where the version already has an item of its own, that
    item's statement and weight stay, and only the lists it lacks are drafted."""
    remarks: list[str] = []
    drafted = _by_key(out.items, example_items, remarks)
    own = {rubric.norm_id(c.key): c for c in target_items}
    items: list[ProposedItem] = []
    for e in example_items:
        answer = drafted.get(rubric.norm_id(e.key))
        mine = own.get(rubric.norm_id(e.key))
        if own and mine is None:
            continue    # the version has items of its own, and none like this one
        if answer is None:
            remarks.append(f"The AI returned nothing for item {e.key}.")
            continue
        accept = _lines(answer.acceptable_forms, len(e.accept)) if e.accept else []
        reject = _lines(answer.does_not_count, len(e.reject)) if e.reject else []
        if mine is not None:
            accept = [] if mine.accept else accept
            reject = [] if mine.reject else reject
            if accept or reject:
                items.append(ProposedItem(
                    key=mine.key, points=mine.points, accept=accept, reject=reject,
                    source_text=criterion_text(e), note=_line(answer.note),
                    base=rubric.statement_print(mine.text),
                ))
            continue
        statement = _text(answer.statement)
        if not statement:
            remarks.append(f"The AI returned no statement for item {e.key}.")
            continue
        items.append(ProposedItem(
            key=e.key, text=statement, points=e.points, accept=accept, reject=reject,
            source_text=criterion_text(e), note=_line(answer.note),
        ))
    return _proposal("example", target, items, inputs, example_id=example.id,
                     notes=_lines(out.notes, 10), remarks=remarks)


def from_material(target: Problem, material: str, out: MaterialDraft, alone: bool = False,
                  inputs: str = "") -> Proposal:
    """The draft for ``target`` from pasted material. An item is backed only by the
    quotes code finds in the material; with none, it is inferred. ``alone`` means
    there was no material, only the problem text: then nothing backs any item. A
    problem text is taken only for a version that has none, and code says whether it
    is in the material word for word."""
    remarks: list[str] = []
    version_text, found = "", False
    if not alone and not target.text.strip():
        version_text = _text(out.problem_text, MATERIAL_LIMIT)
        found = bool(version_text) and found_in(version_text, material)
        if not version_text:
            remarks.append("The AI found no problem statement in your material. Type or paste "
                           "it into the problem text box.")
    items: list[ProposedItem] = []
    bad_quotes = no_weight = 0
    for answer in out.items:
        statement = _text(answer.statement)
        if not statement:
            continue
        if len(items) == MAX_ITEMS:
            remarks.append(f"The AI returned more than {MAX_ITEMS} items. Only the first "
                           f"{MAX_ITEMS} are shown.")
            break
        quotes = [] if alone else [q for q in (_text(q, 600) for q in answer.source_quotes or []) if q]
        backing = [q for q in quotes if found_in(q, material)]
        bad_quotes += len(backing) < len(quotes)
        points = None if alone else _points(answer.weight_in_material)
        no_weight += points is None
        items.append(ProposedItem(
            key=str(len(items) + 1), text=statement, points=points or 1.0,
            accept=_lines(answer.acceptable_forms, MAX_LINES),
            reject=_lines(answer.does_not_count, MAX_LINES),
            source_text="\n".join(backing), inferred=not backing, note=_line(answer.note),
        ))
    if bad_quotes:
        remarks.append(
            f"For {rubric._plural(bad_quotes, 'item')}, the AI quoted words that are not in your "
            "material. Those quotes are not shown, and an item with nothing else behind it is "
            "marked as inferred.")
    if items and no_weight and not alone:
        remarks.append(
            "Your material gives no points for the items, so each weight is set to 1."
            if no_weight == len(items) else
            f"Your material gives no points for {rubric._plural(no_weight, 'item')}, so "
            f"{'its weight is' if no_weight == 1 else 'their weights are'} set to 1.")
    if not alone and _AI_DIRECTED.search(material):
        remarks.append("A line in your pasted text reads like an instruction to an AI. It was "
                       "treated as text, and not followed.")
    return _proposal("text" if alone else "material", target, items, inputs,
                     version_text=version_text, version_text_found=found,
                     notes=_lines(out.notes, 10), remarks=remarks)


def from_elaboration(target: Problem, items: list[Criterion], out: ElaborateDraft,
                     inputs: str = "") -> Proposal:
    """The suggestions for ``target``'s own items. The model is not asked for the
    statements, so it cannot change them; keys and weights stay the items' own. What
    it suggests is its reading of the statement, not something the owner wrote, so
    every suggestion is inferred."""
    remarks: list[str] = []
    notes = _lines(out.notes, 10)
    drafted = _by_key(out.items, items, remarks)
    proposed: list[ProposedItem] = []
    for c in items:
        answer = drafted.get(rubric.norm_id(c.key))
        if answer is None:
            continue
        accept = _lines(answer.acceptable_forms, MAX_LINES, c.accept)
        reject = _lines(answer.does_not_count, MAX_LINES, c.reject)
        note = _line(answer.note)
        if accept or reject:
            proposed.append(ProposedItem(
                key=c.key, points=c.points, accept=accept, reject=reject, inferred=True,
                note=note, base=rubric.statement_print(c.text),
            ))
        elif note:      # a question about the item, with nothing to accept
            notes.append(f"Item {c.key}: {note}")
    return _proposal("elaborate", target, proposed, inputs, notes=notes, remarks=remarks)


# --- what can be drafted, and from what --------------------------------------

def _items(bank: Assessment, problem_id: str) -> list[Criterion]:
    """A version's items that have a statement. An item without one is still the owner's to fill."""
    return [c for c in bank.criteria_for(problem_id) if c.text.strip()]


def example_of(bank: Assessment) -> Problem | None:
    """The version the others are drafted from, unless one is named: the first with rubric items."""
    return next((p for p in bank.problems if _items(bank, p.id)), None)


def _lacks_lists(example_items: list[Criterion], own_items: list[Criterion]) -> bool:
    """Does the example have acceptable forms or exclusions that a version's own items lack?"""
    own = {rubric.norm_id(c.key): c for c in own_items}
    return any(
        (mine := own.get(rubric.norm_id(e.key))) is not None
        and ((e.accept and not mine.accept) or (e.reject and not mine.reject))
        for e in example_items
    )


def options(bank: Assessment, example_id: str = "") -> dict:
    """What the AI could draft for this bank as it is now. The review screen offers
    exactly this, so the screen and the API agree.

    example         the version others are drafted from ("" when no version has items)
    from_example    versions without a rubric that can be drafted from it
    carry_over      versions whose own items lack forms or exclusions the example has
    elaborate       versions whose own items can be elaborated
    from_text       versions without a rubric, when there is no example: only the
                    problem text to go on (needs "let the AI fill gaps")
    from_material   versions without a rubric: pasted material can be drafted into them
    needs_text      versions without a rubric and without a problem text
    """
    out: dict = {"example": "", "from_example": [], "carry_over": [], "elaborate": [],
                 "from_text": [], "from_material": [], "needs_text": []}
    if bank.kind != "bank" or bank.rubric_status != "draft":
        return out
    example = rubric.find_version(bank, example_id) if example_id else example_of(bank)
    example_items = _items(bank, example.id) if example else []
    if not example_items or not example.text.strip():
        example = None
    out["example"] = example.id if example else ""
    for p in bank.problems:
        own = _items(bank, p.id)
        has_text = bool(p.text.strip())
        if own:
            if has_text:
                out["elaborate"].append(p.id)
                if example is not None and p.id != example.id and _lacks_lists(example_items, own):
                    out["carry_over"].append(p.id)
        elif not bank.criteria_for(p.id):
            out["from_material"].append(p.id)
            if not has_text:
                out["needs_text"].append(p.id)
            elif example is not None:
                out["from_example"].append(p.id)
            else:
                out["from_text"].append(p.id)
    return out


def _print(*parts) -> str:
    blob = json.dumps(parts, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def inputs_print(bank: Assessment, mode: str, problem_id: str, example_id: str = "") -> str:
    """Fingerprint of what a draft for this version is made from, as the bank is now.
    A stored draft with another fingerprint was made from something that has changed
    since. An item's own statement is not part of it: each drafted item remembers the
    statement it was made for (``ProposedItem.base``)."""
    target = rubric.find_version(bank, problem_id)
    if target is None:
        return ""
    if mode == "material":      # the version's own text may come from the draft itself
        return _print(mode, bank.material)
    parts: list = [mode, target.text.strip(), target.instructions.strip()]
    if mode == "example":
        example = rubric.find_version(bank, example_id)
        parts += [] if example is None else [
            example.text.strip(), example.instructions.strip(),
            [(c.key, c.text.strip(), c.accept, c.reject, c.points) for c in _items(bank, example.id)],
        ]
    return _print(*parts)


def is_stale(bank: Assessment, proposal: Proposal) -> bool:
    return bool(proposal.inputs) and proposal.inputs != inputs_print(
        bank, proposal.mode, proposal.problem_id, proposal.example_id)


# --- planning a draft --------------------------------------------------------

@dataclass
class Call:
    """One model call of a draft: what to send, and how its answer becomes a proposal."""

    problem_id: str
    messages: list[tuple[str, str]]
    schema: type[BaseModel]
    finish: Callable[[BaseModel], Proposal]


def _needs_text(version: Problem) -> dict:
    return {"box": "version_text", "problem_id": version.id, "item_key": "",
            "message": f"Version {version.id} needs its problem text before the AI can draft for it."}


def _skip(version: Problem, message: str, box: dict | None = None) -> dict:
    return {"problem_id": version.id, "message": message, "detail": "", "box": box}


def _target(bank: Assessment, problem_id: str) -> Problem:
    version = rubric.find_version(bank, problem_id)
    if version is None:
        raise rubric.Refusal(f'This bank has no version called "{problem_id}".', kind="invalid")
    return version


def _waiting(bank: Assessment, version: Problem, mode: str) -> str:
    """The reason not to draft for a version that has a draft of another kind waiting:
    the new draft would take its place, and what the owner has not decided yet would
    be gone without a word. Drafting the same kind again is how a draft is replaced."""
    kinds = {"text": "material"}    # from the problem text alone is the weakest form of "material"
    other = next((p for p in bank.proposals if p.problem_id == version.id
                  and kinds.get(p.mode, p.mode) != kinds.get(mode, mode)), None)
    if other is None:
        return ""
    return (f"Version {version.id} has an AI draft waiting. Accept or discard it before you ask "
            "for a different kind of draft.")


def _nothing(skipped: list[dict], otherwise: str) -> rubric.Refusal:
    """The refusal for a request that comes to no model call at all."""
    boxes = [s["box"] for s in skipped if s.get("box")]
    return rubric.Refusal(skipped[0]["message"] if skipped else otherwise, boxes=boxes,
                          kind="incomplete" if boxes else "invalid")


def _plan_example(bank: Assessment, problem_ids: list[str] | None,
                  example_id: str) -> tuple[list[Call], list[dict]]:
    example = _target(bank, example_id) if example_id else example_of(bank)
    example_items = _items(bank, example.id) if example else []
    if not example_items:
        raise rubric.Refusal(
            f"Version {example.id} has no rubric items to draft from." if example else
            "No version has rubric items yet, so there is no example to draft from. Write the "
            "items for one version first.", kind="invalid")
    if not example.text.strip():
        raise rubric.Refusal(_needs_text(example)["message"], boxes=[_needs_text(example)],
                             kind="incomplete")
    wanted = options(bank, example.id)["from_example"] if problem_ids is None else problem_ids
    calls: list[Call] = []
    skipped: list[dict] = []
    for pid in dict.fromkeys(wanted):
        target = _target(bank, pid)
        own = _items(bank, target.id)
        if target.id == example.id:
            continue
        if _waiting(bank, target, "example"):
            skipped.append(_skip(target, _waiting(bank, target, "example")))
        elif not target.text.strip():
            skipped.append(_skip(target, _needs_text(target)["message"], _needs_text(target)))
        elif bank.criteria_for(target.id) and not _lacks_lists(example_items, own):
            skipped.append(_skip(
                target, f"Version {target.id} already has rubric items, and they lack none of "
                        f"version {example.id}'s acceptable forms or exclusions."))
        else:
            inputs = inputs_print(bank, "example", target.id, example.id)
            calls.append(Call(
                target.id, example_messages(example, example_items, target, own), ExampleDraft,
                lambda out, target=target, own=own, inputs=inputs: from_example(
                    example, example_items, target, own, out, inputs),
            ))
    if not calls:
        raise _nothing(skipped, "There is nothing for the AI to draft: every version already has a rubric.")
    return calls, skipped


def _plan_material(bank: Assessment, problem_ids: list[str] | None,
                   alone: bool) -> tuple[list[Call], list[dict]]:
    empty = options(bank)["from_material"]
    if problem_ids:
        target = _target(bank, problem_ids[0])
    elif empty:
        target = _target(bank, empty[0])
    else:
        raise rubric.Refusal("Every version already has rubric items. This kind of draft is for "
                             "a version without a rubric.", kind="invalid")
    if bank.criteria_for(target.id):
        raise rubric.Refusal(f"Version {target.id} already has rubric items. This kind of draft "
                             "is for a version without a rubric.", kind="invalid")
    if _waiting(bank, target, "material"):
        raise rubric.Refusal(_waiting(bank, target, "material"), kind="invalid")
    if not alone:
        material = bank.material
        if not material.strip():
            raise rubric.Refusal("There is no pasted material to draft from.", kind="invalid")
        inputs = inputs_print(bank, "material", target.id)
        return [Call(target.id, material_messages(target, material), MaterialDraft,
                     lambda out: from_material(target, material, out, inputs=inputs))], []
    if not bank.ai_fill:
        raise rubric.Refusal(
            'A rubric drafted from the problem text alone is the AI\'s own guess. It is drafted '
            'only while "Let the AI fill gaps" is on.', kind="invalid")
    if not target.text.strip():
        raise rubric.Refusal(_needs_text(target)["message"], boxes=[_needs_text(target)],
                             kind="incomplete")
    inputs = inputs_print(bank, "text", target.id)
    return [Call(target.id, text_messages(target), MaterialDraft,
                 lambda out: from_material(target, "", out, alone=True, inputs=inputs))], []


def _plan_elaborate(bank: Assessment, problem_ids: list[str] | None) -> tuple[list[Call], list[dict]]:
    first = example_of(bank)
    wanted = ([first.id] if first else []) if problem_ids is None else problem_ids
    calls: list[Call] = []
    skipped: list[dict] = []
    for pid in dict.fromkeys(wanted):
        target = _target(bank, pid)
        own = _items(bank, target.id)
        if _waiting(bank, target, "elaborate"):
            skipped.append(_skip(target, _waiting(bank, target, "elaborate")))
        elif not own:
            skipped.append(_skip(target, f"Version {target.id} has no rubric items to elaborate."))
        elif not target.text.strip():
            skipped.append(_skip(target, _needs_text(target)["message"], _needs_text(target)))
        else:
            inputs = inputs_print(bank, "elaborate", target.id)
            calls.append(Call(
                target.id, elaborate_messages(target, own), ElaborateDraft,
                lambda out, target=target, own=own, inputs=inputs: from_elaboration(
                    target, own, out, inputs),
            ))
    if not calls:
        raise _nothing(skipped, "No version has rubric items to elaborate yet.")
    return calls, skipped


def plan(bank: Assessment, mode: str, problem_ids: list[str] | None = None,
         example_id: str = "") -> tuple[list[Call], list[dict]]:
    """The model calls a draft request comes to, and the versions it has to skip, each
    with the reason. Pasted material is read from ``bank.material``. Raises
    ``rubric.Refusal`` when the rubric is locked, or when nothing can be drafted."""
    rubric.require_draft(bank)
    if mode == "example":
        return _plan_example(bank, problem_ids, example_id)
    if mode in ("material", "text"):
        return _plan_material(bank, problem_ids, alone=mode == "text")
    if mode == "elaborate":
        return _plan_elaborate(bank, problem_ids)
    raise rubric.Refusal(f"A draft has to be one of: {', '.join(MODES)}.", kind="invalid")


def plan_fill(bank: Assessment) -> tuple[list[Call], list[dict]]:
    """What "let the AI fill gaps" drafts without being asked: every empty rubric it
    can. From the example where a version has one. Otherwise from the problem text
    alone, and then for the first version only: the others are drafted from it once
    the owner has accepted it. Never an elaboration, and never over a waiting draft."""
    waiting = {p.problem_id for p in bank.proposals}
    possible = options(bank)
    try:
        targets = [pid for pid in possible["from_example"] if pid not in waiting]
        if targets:
            return plan(bank, "example", targets)
        targets = [pid for pid in possible["from_text"] if pid not in waiting]
        if targets and not possible["example"]:
            return plan(bank, "text", targets[:1])
    except rubric.Refusal:
        pass
    return [], []


# --- pasted material ---------------------------------------------------------

def set_material(bank: Assessment, material: str) -> None:
    """Keep pasted material on the bank, to draft from. It is dropped when the rubric
    is approved. Raises ``NothingToLoad`` when nothing was pasted."""
    material = (material or "").strip()
    if not material:
        raise NothingToLoad("There is no pasted text, so there is nothing to draft from.")
    bank.material = material[:MATERIAL_LIMIT]
    if len(material) > MATERIAL_LIMIT:
        bank.findings.append(Finding(
            code="material_clipped", filled="code", box="file",
            message=f"The pasted text is very long, so only its first {MATERIAL_LIMIT:,} "
                    "characters were used.",
        ))


def new_bank(material: str, name: str = "") -> Assessment:
    """A draft bank for pasted material: one empty version, waiting for the AI's draft
    of its problem text and items."""
    bank = Assessment(
        name=(name or "").strip() or "Pasted problem", source="paste", kind="bank",
        rubric_version="drafted", problems=[Problem(id="v1")],
    )
    set_material(bank, material)
    return bank


# --- running a draft ---------------------------------------------------------

@dataclass
class Job:
    """A draft in progress for one bank, or what is left to say about one that failed."""

    bank_id: str
    total: int
    model: str
    started: float
    done: int = 0
    running: bool = True
    failed: list[dict] = field(default_factory=list)    # problem_id, message, detail
    usage: dict[str, int] = field(default_factory=dict)


_JOBS: dict[str, Job] = {}
_TASKS: set[asyncio.Task] = set()   # keeps a reference so a running draft isn't garbage-collected


def is_drafting(bank_id: str | None = None) -> bool:
    """Is a draft being written, for this bank or (with no id) for any bank?"""
    return any(job.running and bank_id in (None, job.bank_id) for job in _JOBS.values())


def status(bank_id: str) -> dict | None:
    """The bank's draft as the screen shows it: progress while it runs, and afterwards
    only what could not be drafted. None when there is nothing to show."""
    job = _JOBS.get(bank_id)
    if job is None:
        return None
    return {"running": job.running, "total": job.total, "done": job.done, "model": job.model,
            "started": job.started, "usage": dict(job.usage),
            "failed": [{k: f.get(k, "") for k in ("problem_id", "message", "detail")} for f in job.failed]}


def _deliver(bank_id: str, proposal: Proposal) -> str:
    """Put a finished draft on the bank as it is now, in place of any draft waiting for
    the same version. Returns "" when it is there, or a plain sentence saying why
    there is nothing to show."""
    try:
        bank = store.get_assessment(bank_id)
    except Exception:
        return ""       # the bank was deleted meanwhile
    version = proposal.problem_id
    if bank.kind != "bank" or bank.rubric_status != "draft":
        return (f"The rubric was approved while the AI was drafting, so its draft for version "
                f"{version} was not kept.")
    if rubric.find_version(bank, version) is None:
        return (f"Version {version} was renamed or removed while the AI was drafting, so its "
                "draft was not kept.")
    if not proposal.items and not proposal.version_text:
        nothing = ("had nothing to suggest" if proposal.mode == "elaborate" else "found nothing to draft")
        return " ".join([f"The AI {nothing} for version {version}.", *proposal.notes, *proposal.remarks])
    bank = bank.model_copy(deep=True)
    bank.proposals = [p for p in bank.proposals if p.problem_id != version] + [proposal]
    store.save_assessment(bank)
    return ""


def _register(bank_id: str, calls: list[Call], skipped: list[dict] | None) -> Job:
    try:
        model = model_name()
    except Exception as exc:    # e.g. the provider is not configured
        model = f"unavailable ({type(exc).__name__})"
    job = Job(bank_id=bank_id, total=len(calls), model=model, started=time.time(),
              failed=list(skipped or []))
    _JOBS[bank_id] = job
    return job


async def _run(job: Job, calls: list[Call]) -> None:
    sem = asyncio.Semaphore(_CONCURRENCY)

    def kept() -> bool:
        """False once the draft was cancelled or replaced: what is still on its way back is dropped."""
        return job.running and _JOBS.get(job.bank_id) is job

    async def one(call: Call) -> None:
        async with sem:
            if not kept():
                return
            parsed, error, usage = await draft_once(call.messages, call.schema)
            for name, n in usage.items():
                job.usage[name] = job.usage.get(name, 0) + n
            if not kept():
                return
            if parsed is None:
                job.failed.append({
                    "problem_id": call.problem_id, "detail": error,
                    "message": f"The AI could not draft for version {call.problem_id} just now. "
                               "Nothing was changed. Try again.",
                })
            else:
                try:
                    proposal = call.finish(parsed)
                    proposal.model = job.model
                    problem, detail = _deliver(job.bank_id, proposal), ""
                except Exception as exc:    # one version's trouble must not lose the others' drafts
                    problem = (f"The draft for version {call.problem_id} could not be used. "
                               "Nothing was changed. Try again.")
                    detail = f"{type(exc).__name__}: {exc}"[:300]
                if problem:
                    job.failed.append({"problem_id": call.problem_id, "message": problem, "detail": detail})
            job.done += 1

    try:
        await asyncio.gather(*(one(call) for call in calls))
    finally:
        job.running = False
        if not job.failed and _JOBS.get(job.bank_id) is job:
            del _JOBS[job.bank_id]      # nothing left to say


async def run(bank_id: str, calls: list[Call], skipped: list[dict] | None = None) -> None:
    """Write a draft and wait for it (the CLI and the tests)."""
    await _run(_register(bank_id, calls, skipped), calls)


def start(bank_id: str, calls: list[Call], skipped: list[dict] | None = None) -> None:
    """Write a draft in the background. Each version's draft is put on the bank as the
    bank is at that moment, so what the owner typed meanwhile is kept."""
    task = asyncio.get_running_loop().create_task(_run(_register(bank_id, calls, skipped), calls))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def cancel(bank_id: str) -> None:
    """Stop a draft, or clear what is left to say about one that failed. An answer
    still on its way back from the model is not kept."""
    job = _JOBS.pop(bank_id, None)
    if job is not None:
        job.running = False


def reset_for_tests() -> None:
    _JOBS.clear()
