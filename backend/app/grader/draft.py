"""Rubric drafting by AI: one module, three ways in, one kind of output.

  * ``example``: from one rubric to a version's. In a bank whose versions share a
    rubric, it drafts one version's own wording of the shared items. In a bank whose
    versions each have their own, it drafts a version's rubric from another
    version's (the example), or carries the example's specifications over to items
    a version already has.
  * ``material``: from pasted unstructured material, draft the rubric and find the
    problem text. ``text`` is its weakest form: from the problem text alone.
  * ``elaborate``: for existing items, suggest specifications.

A draft is for the shared rubric (``problem_id`` SHARED) or for one version. It is
a ``Proposal``, stored next to the rubric and never in it. Nothing the model writes
is used until the owner accepts it (``rubric.accept_proposal``).

The model writes wording. Code decides everything else:
  * item keys, weights, order and the number of items (the example's, or the
    items' own);
  * what is "inferred": a drafted item counts as backed only when code finds the
    words it quotes in the pasted material;
  * whether a problem text really was taken word for word from that material.

The drafting model is shown versions and rubric items only. No function here takes
a response, and nothing here reads one. Text the owner supplied goes into the
prompt inside blocks closed by a tag made for that one call: it is data, not
instructions. The prompts are data too (``prompts/draft_*.txt``), and
``DRAFT_PROMPT_VERSION`` hashes them with the output schemas. The grading prompts
are not touched by any of this.

A draft runs in the background, one model call per target. Its progress is kept in
memory only: a restart loses a draft in flight and nothing else.
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
from .model import SHARED, Assessment, Criterion, Finding, Problem, Proposal, ProposedItem, clean_text

MODES = ("example", "material", "text", "elaborate")
EFFORT = os.getenv("GRADER_DRAFT_EFFORT", "medium")     # reasoning effort of the drafting model
MATERIAL_LIMIT = 60_000     # characters of pasted material that are used
MAX_ITEMS = 10              # items in a rubric drafted from material
MAX_LINES = 5               # specifications suggested for one item
_LINE_LIMIT = 400           # characters in one drafted line
_TEXT_LIMIT = 1500          # characters in one drafted statement
_CONTEXT_LIMIT = 12_000     # characters of the versions' problem texts shown for a shared rubric
_CONCURRENCY = 3            # simultaneous model calls of one draft

_PROMPTS = Path(__file__).parent / "prompts"
SYSTEM_TEMPLATE = (_PROMPTS / "draft_system.txt").read_text(encoding="utf-8").strip()
TEMPLATES = {
    mode: (_PROMPTS / f"draft_{mode}.txt").read_text(encoding="utf-8").strip() for mode in MODES
}
_SHARED_NOTE = (" The rubric is shared by every version of the problem, shown below: write what "
                "holds for all of them.")


# --- what the model must return ---------------------------------------------

class ExampleItem(BaseModel):
    item_key: str = Field(description="The item_key of the example item this adapts.")
    statement: str = Field(description="The item as it reads for the target version.")
    specifications: list[str] = Field(
        description="The example item's specifications, adapted. Empty if it has none.")
    note: str = Field(
        description="Empty, unless the item has no real counterpart in the target version.")


class ExampleDraft(BaseModel):
    """A rubric adapted to one version."""

    items: list[ExampleItem]
    notes: list[str] = Field(description="What was missing or unclear, one plain sentence each.")


class MaterialItem(BaseModel):
    """Field order is deliberate: quote the material first, then write the item."""

    source_quotes: list[str] = Field(
        description="Exact words of the material that state or show this requirement, copied "
        "character for character. Empty if the material does not contain it.")
    statement: str = Field(description="What the response must show to earn the item.")
    specifications: list[str] = Field(description="Only lines the material itself supports.")
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
    specifications: list[str]
    note: str = Field(description="Empty, unless you would have to decide what the instructor meant.")


class ElaborateDraft(BaseModel):
    """Specifications suggested for existing items."""

    items: list[ElaborateItem]
    notes: list[str] = Field(description="What was missing or unclear, one plain sentence each.")


DRAFT_PROMPT_VERSION = hashlib.sha1(
    (SYSTEM_TEMPLATE + _SHARED_NOTE + "".join(TEMPLATES[mode] for mode in MODES) + "".join(
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
        {"item_key": c.key, "statement": c.text.strip(),
         "specifications": [s.strip() for s in c.specifications if s.strip()]}
        for c in items
    ], ensure_ascii=False, indent=1)


def _owner_text(*problems: Problem, items: Iterable[Criterion] = (), more: str = "") -> list[str]:
    texts = [more]
    for p in problems:
        texts += [p.id, p.text, p.instructions]
    for c in items:
        texts += [c.key, c.text, *c.specifications]
    return texts


def _versions(versions: list[Problem], nonce: str) -> str:
    """The problem as the model is shown it: one version with what students were asked
    to write, or, for a rubric they share, the versions one after the other."""
    if len(versions) == 1:
        return "PROBLEM\n" + _block("problem", versions[0].text.strip(), nonce) + "\n" + \
            _instructions(versions[0], nonce)
    parts: list[str] = []
    size = 0
    for v in versions:
        part = f"Version {v.id}:\n{v.text.strip()}"
        if v.instructions.strip():
            part += f"\nWhat students were asked to write: {v.instructions.strip()}"
        if parts and size + len(part) > _CONTEXT_LIMIT:
            break
        parts.append(part)
        size += len(part)
    more = len(versions) - len(parts)
    tail = f"({more} more versions like these are not shown.)\n" if more else ""
    return "VERSIONS OF THE PROBLEM\n" + _block("problem", "\n\n".join(parts), nonce) + "\n" + tail


def example_messages(example: Problem | None, example_items: list[Criterion], target: Problem,
                     target_items: list[Criterion]) -> list[tuple[str, str]]:
    """``example`` None: the example rubric is the one every version shares."""
    nonce = _nonce(*_owner_text(*([example] if example else []), target,
                                items=[*example_items, *target_items]))
    own = ""
    if target_items:
        own = ("\nITEMS THE INSTRUCTOR ALREADY WROTE FOR THE TARGET VERSION\n"
               "Their statements stay exactly as they are. For an item listed here, return the "
               "instructor's statement unchanged, and adapt only the example item's "
               "specifications to it.\n"
               + _block("rubric", _items_json(target_items), nonce))
    user = _fill(
        TEMPLATES["example"],
        source=("The example rubric is shared by every version of the problem. Write the target "
                "version's own wording of it." if example is None else
                "The example rubric belongs to another version of the same problem, shown first."),
        example_problem="" if example is None else (
            "EXAMPLE VERSION\n" + _block("problem", example.text.strip(), nonce) + "\n"
            + _instructions(example, nonce)),
        example_items=_block("rubric", _items_json(example_items), nonce),
        target_problem=_block("problem", target.text.strip(), nonce),
        target_instructions=_instructions(target, nonce),
        target_items=own,
    )
    return [("system", SYSTEM_TEMPLATE), ("human", user.strip())]


def material_messages(versions: list[Problem], text_for: Problem | None,
                      material: str) -> list[tuple[str, str]]:
    """``versions`` the rubric is for (several: a shared rubric). ``text_for`` the one
    version whose problem text the material should supply, if any."""
    nonce = _nonce(*_owner_text(*versions, more=material))
    shown = [v for v in versions if v.text.strip()]
    task = (
        "problem_text: copy the problem statement word for word from the material. Leave it "
        "empty if the material has none. Never write, complete or tidy up a problem statement "
        "yourself."
        if text_for is not None else
        "problem_text: leave it empty. The instructor has already entered the problem "
        "statement" + (", shown below." if shown else "."))
    user = _fill(
        TEMPLATES["material"], problem_task=task, max_items=str(MAX_ITEMS),
        shared_note=_SHARED_NOTE if len(versions) > 1 else "",
        problem=_versions(shown, nonce) + "\n" if shown else "",
        material=_block("material", material, nonce),
    )
    return [("system", SYSTEM_TEMPLATE), ("human", user)]


def text_messages(versions: list[Problem]) -> list[tuple[str, str]]:
    nonce = _nonce(*_owner_text(*versions))
    user = _fill(
        TEMPLATES["text"], max_items=str(MAX_ITEMS),
        shared_note=_SHARED_NOTE if len(versions) > 1 else "",
        problem=_versions(versions, nonce),
    )
    return [("system", SYSTEM_TEMPLATE), ("human", user.strip())]


def elaborate_messages(versions: list[Problem], items: list[Criterion]) -> list[tuple[str, str]]:
    nonce = _nonce(*_owner_text(*versions, items=items))
    user = _fill(
        TEMPLATES["elaborate"], max_lines=str(MAX_LINES),
        shared_note=_SHARED_NOTE if len(versions) > 1 else "",
        problem=_versions(versions, nonce),
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


def _proposal(mode: str, problem_id: str, items: list[ProposedItem], inputs: str, **more) -> Proposal:
    return Proposal(
        id=uuid.uuid4().hex[:12], mode=mode, problem_id=problem_id, items=items,
        order=[i.key for i in items], prompt_version=DRAFT_PROMPT_VERSION, created=time.time(),
        inputs=inputs, **more,
    )


def from_example(example: Problem | None, example_items: list[Criterion], target: Problem,
                 target_items: list[Criterion], out: ExampleDraft, inputs: str = "") -> Proposal:
    """The draft for ``target``. Keys, weights, order and the number of items are the
    example's, whatever the model returned. Specifications are kept only where the
    example item has them, and no more of them than it has: the AI adapts, it does
    not add.

    ``example`` None: the example is the shared rubric, and the draft is the
    version's own wording of the shared items it has no wording of yet. An item the
    AI gives back word for word is left out: the version keeps the shared wording.
    Otherwise, where the version already has an item of its own, that item's
    statement and weight stay, and only the specifications it lacks are drafted."""
    remarks: list[str] = []
    drafted = _by_key(out.items, example_items, remarks)
    own = {rubric.norm_id(c.key): c for c in target_items}
    items: list[ProposedItem] = []
    unchanged: list[str] = []
    for e in example_items:
        answer = drafted.get(rubric.norm_id(e.key))
        mine = own.get(rubric.norm_id(e.key))
        if example is None and mine is not None:
            continue    # the version has its own wording of this item already
        if example is not None and own and mine is None:
            continue    # the version has items of its own, and none like this one
        if answer is None:
            remarks.append(f"The AI returned nothing for item {e.key}.")
            continue
        specifications = _lines(answer.specifications, len(e.specifications)) if e.specifications else []
        if mine is not None:
            if specifications and not mine.specifications:
                items.append(ProposedItem(
                    key=mine.key, points=mine.points, specifications=specifications,
                    source_text=criterion_text(e), note=_line(answer.note),
                    base=rubric.statement_print(mine.text),
                ))
            continue
        statement = _text(answer.statement)
        if not statement:
            remarks.append(f"The AI returned no statement for item {e.key}.")
            continue
        if example is None and _same(statement, e.text) and len(specifications) == len(e.specifications) \
                and all(_same(a, b) for a, b in zip(specifications, e.specifications)):
            unchanged.append(e.key)
            continue
        items.append(ProposedItem(
            key=e.key, text=statement, points=e.points, specifications=specifications,
            source_text=criterion_text(e), note=_line(answer.note),
        ))
    if unchanged:
        remarks.append(
            "The shared wording fits this version as it is." if not items else
            f"The AI kept {_keys(unchanged)} word for word, so {'it is' if len(unchanged) == 1 else 'they are'} "
            "left out: this version keeps the shared wording.")
    return _proposal("example", target.id, items, inputs, example_id=example.id if example else SHARED,
                     notes=_lines(out.notes, 10), remarks=remarks)


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def _keys(keys: list[str]) -> str:
    """'item 2' or 'items 1, 2 and 4'."""
    return f"item {keys[0]}" if len(keys) == 1 else f"items {', '.join(keys[:-1])} and {keys[-1]}"


def from_material(problem_id: str, text_for: Problem | None, material: str, out: MaterialDraft,
                  alone: bool = False, inputs: str = "") -> Proposal:
    """The draft for the rubric of ``problem_id`` (SHARED, or one version) from pasted
    material. An item is backed only by the quotes code finds in the material; with
    none, it is inferred. ``alone`` means there was no material, only the problem
    text: then nothing backs any item. A problem text is taken only for ``text_for``,
    a version that has none, and code says whether it is in the material word for
    word."""
    remarks: list[str] = []
    version_text, found = "", False
    if not alone and text_for is not None and not text_for.text.strip():
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
            specifications=_lines(answer.specifications, MAX_LINES),
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
    return _proposal("text" if alone else "material", problem_id, items, inputs,
                     version_text=version_text, version_text_found=found,
                     text_for=text_for.id if text_for is not None else "",
                     notes=_lines(out.notes, 10), remarks=remarks)


def from_elaboration(problem_id: str, items: list[Criterion], out: ElaborateDraft,
                     inputs: str = "") -> Proposal:
    """The suggestions for the items of ``problem_id`` (SHARED, or one version). The
    model is not asked for the statements, so it cannot change them; keys and weights
    stay the items' own. What it suggests is its reading of the statement, not
    something the owner wrote, so every suggestion is inferred."""
    remarks: list[str] = []
    notes = _lines(out.notes, 10)
    drafted = _by_key(out.items, items, remarks)
    proposed: list[ProposedItem] = []
    for c in items:
        answer = drafted.get(rubric.norm_id(c.key))
        if answer is None:
            continue
        specifications = _lines(answer.specifications, MAX_LINES, c.specifications)
        note = _line(answer.note)
        if specifications:
            proposed.append(ProposedItem(
                key=c.key, points=c.points, specifications=specifications, inferred=True,
                note=note, base=rubric.statement_print(c.text),
            ))
        elif note:      # a question about the item, with nothing to accept
            notes.append(f"Item {c.key}: {note}")
    return _proposal("elaborate", problem_id, proposed, inputs, notes=notes, remarks=remarks)


# --- what can be drafted, and from what --------------------------------------

def _items(bank: Assessment, problem_id: str) -> list[Criterion]:
    """The items stored for a version (or, with SHARED, the shared ones) that have a
    statement. An item without one is still the owner's to fill."""
    return [c for c in bank.own_items(problem_id) if c.text.strip()]


def example_of(bank: Assessment) -> Problem | None:
    """In a bank whose versions each have their own items: the version the others are
    drafted from, unless one is named. The first with rubric items."""
    return next((p for p in bank.problems if _items(bank, p.id)), None)


def _lacks_specifications(example_items: list[Criterion], own_items: list[Criterion]) -> bool:
    """Does the example have specifications that a version's own items lack?"""
    own = {rubric.norm_id(c.key): c for c in own_items}
    return any(
        (mine := own.get(rubric.norm_id(e.key))) is not None and e.specifications and not mine.specifications
        for e in example_items
    )


def options(bank: Assessment, example_id: str = "") -> dict:
    """What the AI could draft for this bank as it is now. The review screen offers
    exactly this, so the screen and the API agree. SHARED ("") in a list stands for
    the rubric every version shares.

    shared          the versions share a rubric
    own_wording     shared rubric: versions it can draft their own wording for
    example         own rubrics: the version others are drafted from ("" when none)
    from_example    own rubrics: versions without a rubric that can be drafted from it
    carry_over      own rubrics: versions whose items lack specifications the example has
    elaborate       what can be elaborated: SHARED, or versions with items
    from_text       no rubric yet: SHARED, from the problem texts alone
                    (needs "let the AI fill gaps")
    from_material   no rubric yet: SHARED; own rubrics: versions without one
    needs_text      versions without a problem text
    """
    out: dict = {"shared": False, "own_wording": [], "example": "", "from_example": [],
                 "carry_over": [], "elaborate": [], "from_text": [], "from_material": [],
                 "needs_text": []}
    if bank.kind != "bank" or bank.rubric_status != "draft":
        return out
    texts = [p.id for p in bank.problems if p.text.strip()]
    out["needs_text"] = [p.id for p in bank.problems if not p.text.strip()]
    if bank.shared_items():
        out["shared"] = True
        if _items(bank, SHARED) and texts:
            out["elaborate"] = [SHARED]
            out["own_wording"] = texts
        return out
    if not bank.criteria:
        out["from_material"] = [SHARED]
        out["from_text"] = [SHARED] if texts else []
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
                if example is not None and p.id != example.id and _lacks_specifications(example_items, own):
                    out["carry_over"].append(p.id)
        elif not bank.own_items(p.id):
            out["from_material"].append(p.id)
            if has_text and example is not None:
                out["from_example"].append(p.id)
    return out


def _print(*parts) -> str:
    blob = json.dumps(parts, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _spec(items: list[Criterion]) -> list:
    return [(c.key, c.text.strip(), c.specifications, c.points) for c in items]


def inputs_print(bank: Assessment, mode: str, problem_id: str, example_id: str = "") -> str:
    """Fingerprint of what a draft is made from, as the bank is now. A stored draft
    with another fingerprint was made from something that has changed since. An
    item's own statement is not part of it: each drafted item remembers the statement
    it was made for (``ProposedItem.base``)."""
    if mode == "material":      # a version's text may come from the draft itself
        return _print(mode, bank.material)
    if problem_id == SHARED:
        return _print(mode, [(p.id, p.text.strip(), p.instructions.strip()) for p in bank.problems])
    target = rubric.find_version(bank, problem_id)
    if target is None:
        return ""
    parts: list = [mode, target.text.strip(), target.instructions.strip()]
    if mode == "example" and example_id == SHARED:
        parts.append(_spec(_items(bank, SHARED)))
    elif mode == "example":
        example = rubric.find_version(bank, example_id)
        parts += [] if example is None else [
            example.text.strip(), example.instructions.strip(), _spec(_items(bank, example.id))]
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


def _waiting(bank: Assessment, problem_id: str, mode: str) -> str:
    """The reason not to draft for a rubric that has a draft of another kind waiting:
    the new draft would take its place, and what the owner has not decided yet would
    be gone without a word. Drafting the same kind again is how a draft is replaced."""
    kinds = {"text": "material"}    # from the problem text alone is the weakest form of "material"
    other = next((p for p in bank.proposals if p.problem_id == problem_id
                  and kinds.get(p.mode, p.mode) != kinds.get(mode, mode)), None)
    if other is None:
        return ""
    where = rubric._where(problem_id)
    return (f"{where[0].upper()}{where[1:]} has an AI draft waiting. Accept or discard it before "
            "you ask for a different kind of draft.")


def _nothing(skipped: list[dict], otherwise: str) -> rubric.Refusal:
    """The refusal for a request that comes to no model call at all."""
    boxes = [s["box"] for s in skipped if s.get("box")]
    return rubric.Refusal(skipped[0]["message"] if skipped else otherwise, boxes=boxes,
                          kind="incomplete" if boxes else "invalid")


def _plan_own_wording(bank: Assessment, problem_ids: list[str] | None) -> tuple[list[Call], list[dict]]:
    """A shared rubric: draft chosen versions' own wording of the shared items."""
    shared = _items(bank, SHARED)
    if not shared:
        raise rubric.Refusal("The rubric has no items to draft from yet.", kind="invalid")
    if not problem_ids:
        raise rubric.Refusal("Choose the version that needs its own wording.", kind="invalid")
    calls: list[Call] = []
    skipped: list[dict] = []
    for pid in dict.fromkeys(problem_ids):
        target = _target(bank, pid)
        own_keys = {c.key for c in bank.own_items(target.id)}
        if _waiting(bank, target.id, "example"):
            skipped.append(_skip(target, _waiting(bank, target.id, "example")))
        elif not target.text.strip():
            skipped.append(_skip(target, _needs_text(target)["message"], _needs_text(target)))
        elif all(c.key in own_keys for c in shared):
            skipped.append(_skip(target, f"Version {target.id} already has its own wording of every item."))
        else:
            inputs = inputs_print(bank, "example", target.id, SHARED)
            calls.append(Call(
                target.id, example_messages(None, shared, target, []), ExampleDraft,
                lambda out, target=target, inputs=inputs: from_example(
                    None, shared, target, bank.own_items(target.id), out, inputs),
            ))
    if not calls:
        raise _nothing(skipped, "There is nothing for the AI to draft.")
    return calls, skipped


def _plan_example(bank: Assessment, problem_ids: list[str] | None,
                  example_id: str) -> tuple[list[Call], list[dict]]:
    if bank.shared_items():
        return _plan_own_wording(bank, problem_ids)
    example = _target(bank, example_id) if example_id else example_of(bank)
    example_items = _items(bank, example.id) if example else []
    if not example_items:
        raise rubric.Refusal(
            f"Version {example.id} has no rubric items to draft from." if example else
            "No version has rubric items yet, so there is no example to draft from. Write the "
            "items first.", kind="invalid")
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
        if _waiting(bank, target.id, "example"):
            skipped.append(_skip(target, _waiting(bank, target.id, "example")))
        elif not target.text.strip():
            skipped.append(_skip(target, _needs_text(target)["message"], _needs_text(target)))
        elif bank.own_items(target.id) and not _lacks_specifications(example_items, own):
            skipped.append(_skip(
                target, f"Version {target.id} already has rubric items, and they lack none of "
                        f"version {example.id}'s specifications."))
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
    """A rubric from pasted material, or from the problem text alone: the shared one
    while the bank has no rubric, otherwise (versions with rubrics of their own) for
    a version that has none."""
    if bank.shared_items():
        raise rubric.Refusal("The versions already share a rubric. Drafting from material is for "
                             "a bank without one.", kind="invalid")
    if not bank.criteria and (not problem_ids or problem_ids[0] == SHARED):
        target_id = SHARED
        versions = list(bank.problems)
        only = versions[0] if len(versions) == 1 else None
        text_for = only if only is not None and not only.text.strip() else None
    else:
        empty = options(bank)["from_material"]
        if problem_ids:
            target = _target(bank, problem_ids[0])
        elif empty:
            target = _target(bank, empty[0])
        else:
            raise rubric.Refusal("Every version already has rubric items. This kind of draft is "
                                 "for a version without a rubric.", kind="invalid")
        if bank.own_items(target.id):
            raise rubric.Refusal(f"Version {target.id} already has rubric items. This kind of "
                                 "draft is for a version without a rubric.", kind="invalid")
        target_id, versions = target.id, [target]
        text_for = target if not target.text.strip() else None
    if _waiting(bank, target_id, "material"):
        raise rubric.Refusal(_waiting(bank, target_id, "material"), kind="invalid")
    if not alone:
        material = bank.material
        if not material.strip():
            raise rubric.Refusal("There is no pasted material to draft from.", kind="invalid")
        inputs = inputs_print(bank, "material", target_id)
        return [Call(target_id, material_messages(versions, text_for, material), MaterialDraft,
                     lambda out: from_material(target_id, text_for, material, out, inputs=inputs))], []
    if not bank.ai_fill:
        raise rubric.Refusal(
            'A rubric drafted from the problem text alone is the AI\'s own guess. It is drafted '
            'only while "Let the AI fill gaps" is on.', kind="invalid")
    shown = [v for v in versions if v.text.strip()]
    if not shown:
        raise rubric.Refusal(_needs_text(versions[0])["message"], boxes=[_needs_text(versions[0])],
                             kind="incomplete")
    inputs = inputs_print(bank, "text", target_id)
    return [Call(target_id, text_messages(shown), MaterialDraft,
                 lambda out: from_material(target_id, None, "", out, alone=True, inputs=inputs))], []


def _plan_elaborate(bank: Assessment, problem_ids: list[str] | None) -> tuple[list[Call], list[dict]]:
    if bank.shared_items():
        if problem_ids not in (None, [], [SHARED]):
            raise rubric.Refusal("The versions share a rubric: it is elaborated as a whole.",
                                 kind="invalid")
        shared = _items(bank, SHARED)
        versions = [p for p in bank.problems if p.text.strip()]
        if _waiting(bank, SHARED, "elaborate"):
            raise rubric.Refusal(_waiting(bank, SHARED, "elaborate"), kind="invalid")
        if not shared:
            raise rubric.Refusal("The rubric has no items to elaborate yet.", kind="invalid")
        if not versions:
            first = bank.problems[0]
            raise rubric.Refusal(_needs_text(first)["message"], boxes=[_needs_text(first)],
                                 kind="incomplete")
        inputs = inputs_print(bank, "elaborate", SHARED)
        return [Call(SHARED, elaborate_messages(versions, shared), ElaborateDraft,
                     lambda out: from_elaboration(SHARED, shared, out, inputs))], []
    first = example_of(bank)
    wanted = ([first.id] if first else []) if problem_ids is None else problem_ids
    calls: list[Call] = []
    skipped: list[dict] = []
    for pid in dict.fromkeys(wanted):
        target = _target(bank, pid)
        own = _items(bank, target.id)
        if _waiting(bank, target.id, "elaborate"):
            skipped.append(_skip(target, _waiting(bank, target.id, "elaborate")))
        elif not own:
            skipped.append(_skip(target, f"Version {target.id} has no rubric items to elaborate."))
        elif not target.text.strip():
            skipped.append(_skip(target, _needs_text(target)["message"], _needs_text(target)))
        else:
            inputs = inputs_print(bank, "elaborate", target.id)
            calls.append(Call(
                target.id, elaborate_messages([target], own), ElaborateDraft,
                lambda out, target=target, own=own, inputs=inputs: from_elaboration(
                    target.id, own, out, inputs),
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
    can. Versions with rubrics of their own: those without one, from the example. A
    bank without a rubric: the shared rubric, from the problem texts alone. Never an
    elaboration, never a version's own wording, and never over a waiting draft."""
    waiting = {p.problem_id for p in bank.proposals}
    possible = options(bank)
    try:
        targets = [pid for pid in possible["from_example"] if pid not in waiting]
        if targets:
            return plan(bank, "example", targets)
        if possible["from_text"] and SHARED not in waiting:
            return plan(bank, "text", [SHARED])
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
    of its problem text and of the rubric."""
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
    the same rubric. Returns "" when it is there, or a plain sentence saying why
    there is nothing to show."""
    try:
        bank = store.get_assessment(bank_id)
    except Exception:
        return ""       # the bank was deleted meanwhile
    where = rubric._where(proposal.problem_id)
    if bank.kind != "bank" or bank.rubric_status != "draft":
        return f"The rubric was approved while the AI was drafting, so its draft for {where} was not kept."
    for version in {proposal.problem_id, proposal.text_for} - {SHARED}:
        if rubric.find_version(bank, version) is None:
            return (f"Version {version} was renamed or removed while the AI was drafting, so the "
                    "draft was not kept.")
    if not proposal.items and not proposal.version_text:
        nothing = "had nothing to suggest" if proposal.mode == "elaborate" else "found nothing to draft"
        return " ".join([f"The AI {nothing} for {where}.", *proposal.notes, *proposal.remarks])
    bank = bank.model_copy(deep=True)
    bank.proposals = [p for p in bank.proposals if p.problem_id != proposal.problem_id] + [proposal]
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
            where = rubric._where(call.problem_id)
            if parsed is None:
                job.failed.append({
                    "problem_id": call.problem_id, "detail": error,
                    "message": f"The AI could not draft for {where} just now. Nothing was changed. "
                               "Try again.",
                })
            else:
                try:
                    proposal = call.finish(parsed)
                    proposal.model = job.model
                    problem, detail = _deliver(job.bank_id, proposal), ""
                except Exception as exc:    # one target's trouble must not lose the others' drafts
                    problem = (f"The draft for {where} could not be used. Nothing was changed. "
                               "Try again.")
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
    """Write a draft in the background. Each draft is put on the bank as the bank is
    at that moment, so what the owner typed meanwhile is kept."""
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
