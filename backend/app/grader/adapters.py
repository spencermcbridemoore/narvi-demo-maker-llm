"""Adapters: outside formats -> the internal model.

  * ``load_chen``: the Chen & Wan handoff kit (``data/instances.csv``), whole or one
    problem at a time, with the two reference graders kept as evaluation-only labels.
  * ``load_problem_info``: a Problem Info CSV -> a draft problem bank plus findings.
  * ``parse_responses``: a responses CSV -> responses plus findings.

Intake is soft. An upload is never rejected because something is missing or odd: it
loads what it can, fills what plain code can fill (and says so in a finding), and
leaves the rest as empty boxes for the owner. The one error is ``NothingToLoad``.
Names and ids should already be stripped by the browser before upload.

A new format costs one function here; nothing downstream changes.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import REPO_ROOT
from . import rubric
from .model import Assessment, Criterion, Finding, Problem, Response

# Rubric wordings available in the kit's instances.csv. ``simple`` is the wording
# the human graders used; the detailed ones are Chen's model-facing rewrites, which
# he tuned on these same responses (so scores with them are optimistic).
CHEN_VERSIONS = {
    "simple": "rubric_item_simple",
    "detailed": "rubric_item_detailed",
    "detailed_latest": "rubric_item_detailed_latest",
}
CHEN_SOURCE = "example:chen"

# One very long cell (a pasted essay, a whole rubric) is content, not an error.
csv.field_size_limit(16 * 1024 * 1024)


class NothingToLoad(ValueError):
    """The one upload error: the file is empty, or cannot be read as a table."""


# --- the Chen & Wan kit ------------------------------------------------------

def kit_dir() -> Path | None:
    """The unpacked handoff kit, or None if it isn't on this machine."""
    default = REPO_ROOT / "grader_kit" / "rubric_grader_handoff"
    path = Path(os.getenv("GRADER_KIT_DIR", str(default)))
    return path if (path / "data" / "instances.csv").is_file() else None


def _chen_rows(kit: Path | None) -> list[dict[str, str]]:
    kit = kit or kit_dir()
    if kit is None:
        raise FileNotFoundError(
            "Chen kit not found. Unzip rubric_grader_handoff_*.zip into grader_kit/ "
            "(or set GRADER_KIT_DIR)."
        )
    with open(kit / "data" / "instances.csv", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def chen_problems(kit: Path | None = None) -> list[dict]:
    """The kit's problems, with how many responses and rubric items each has."""
    out: dict[str, dict] = {}
    for r in _chen_rows(kit):
        p = out.setdefault(r["problem_id"], {"id": r["problem_id"], "responses": set(), "items": set()})
        p["responses"].add(r["response_id"])
        p["items"].add(r["item_no"])
    return [
        {"id": p["id"], "responses": len(p["responses"]), "items": len(p["items"])}
        for p in sorted(out.values(), key=lambda p: p["id"])
    ]


def load_chen(rubric_version: str = "simple", kit: Path | None = None,
              problem: str | None = None, with_responses: bool = True) -> Assessment:
    """The kit as a problem bank with a draft rubric.

    ``problem`` picks one problem (Q1, Q2 or Q3), which is how the app loads the
    example. Without it the whole kit comes back, for the benchmark CLI.
    ``with_responses=False`` leaves the responses and human labels out, because in
    the app they are attached only after the rubric is approved.
    """
    rows = _chen_rows(kit)
    if rubric_version not in CHEN_VERSIONS:
        raise ValueError(f"rubric_version must be one of {sorted(CHEN_VERSIONS)}")
    column = CHEN_VERSIONS[rubric_version]
    if problem is not None:
        rows = [r for r in rows if r["problem_id"] == problem]
        if not rows:
            raise ValueError(f"The example has no problem called {problem!r}.")

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
        if with_responses:
            responses.setdefault(r["response_id"], Response(
                id=r["response_id"], problem_id=pid, text=r["response_text"],
                external_id=r["response_id"], version_label=pid,
            ))
            references[r["instance_id"]] = {
                "ref_a": int(r["label_ref_a"]), "ref_b": int(r["label_ref_b"]),
            }

    name = (f"Chen & Wan example, {problem} ({rubric_version} wording)" if problem
            else f"Chen & Wan physics (PHY 2048), {rubric_version} rubric")
    return Assessment(
        # A one-problem bank remembers which kit problem it is, whatever its version
        # is renamed to later.
        name=name, source=f"{CHEN_SOURCE}:{problem}" if problem else CHEN_SOURCE, kind="bank",
        rubric_version=rubric_version,
        problems=sorted(problems.values(), key=lambda p: p.id),
        criteria=sorted(criteria.values(), key=lambda c: (c.problem_id, _num_key(c.key))),
        responses=list(responses.values()),
        references=references,
    )


def chen_problem_of(bank: Assessment) -> str:
    """The kit problem a one-problem example bank was made from ("" for the whole kit)."""
    return bank.source.partition(f"{CHEN_SOURCE}:")[2]


def chen_responses(bank: Assessment, kit: Path | None = None) -> tuple[list[Response], dict]:
    """The kit's responses for an example bank, with both human graders' labels. A
    bank made from one problem gets that problem's responses under its own version,
    even if the version has been renamed since."""
    whole = load_chen("simple", kit)
    problem = chen_problem_of(bank)
    if problem:
        target = rubric.find_version(bank, problem) or bank.problems[0]
        keep = [r.model_copy(update={"problem_id": target.id})
                for r in whole.responses if r.problem_id == problem]
    else:
        versions = {p.id for p in bank.problems}
        keep = [r for r in whole.responses if r.problem_id in versions]
    ids = {r.id for r in keep}
    references = {
        iid: labels for iid, labels in whole.references.items() if iid.rsplit("#", 1)[0] in ids
    }
    return keep, references


def _num_key(s: str) -> tuple[int, str]:
    return (int(s), "") if s.isdigit() else (10**9, s)


# --- reading a CSV softly ----------------------------------------------------

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_ONE_COLUMN = chr(31)   # a separator no file contains: the file is a single column


def _norm(header: str) -> str:
    """Column names are matched loosely: case, spaces, dashes and camelCase don't matter."""
    return _NON_WORD.sub("_", _CAMEL.sub("_", header.strip()).lower()).strip("_")


def _ends_inside_quotes(text: str, delimiter: str) -> bool:
    """Does the text end inside a quoted cell? A quote opens a cell only at its start."""
    quoted, at_start, i = False, True, 0
    while i < len(text):
        ch = text[i]
        if quoted:
            if ch == '"':
                if text[i + 1:i + 2] == '"':
                    i += 1                  # "" inside quotes is one quote character
                else:
                    quoted = False
        elif ch == '"' and at_start:
            quoted, at_start = True, False
        else:
            at_start = ch in (delimiter, "\n")
        i += 1
    return quoted


@dataclass
class _Table:
    rows: list[tuple[int, list[str]]]   # every non-empty row with its spreadsheet row number
    delimiter: str
    notes: list[tuple[str, str, str]]   # (code, message, filled) about how the file was read


def _table(text: str, what: str) -> _Table:
    """Read a CSV as loosely as it can be read. Text pasted from a spreadsheet is
    tab-separated, so tabs work, and so do semicolons. A first line without any
    separator means one column: nothing is split at commas inside it."""
    text = (text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise NothingToLoad(f"{what} is empty, so there is nothing to load.")
    first = text.lstrip().split("\n", 1)[0]
    delimiter = next((d for d in ("\t", ",", ";") if d in first), _ONE_COLUMN)
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as exc:
        raise NothingToLoad(
            f"{what} could not be read as a table. Save it as CSV and try again."
        ) from exc
    numbered = [(i, row) for i, row in enumerate(rows, start=1) if any(c.strip() for c in row)]
    if not numbered:
        raise NothingToLoad(f"{what} is empty, so there is nothing to load.")

    notes: list[tuple[str, str, str]] = []
    separator = {"\t": "tab-separated", ";": "semicolon-separated"}.get(delimiter)
    if separator:
        notes.append(("separator", f"We read this file as {separator}.", "note"))
    if _ends_inside_quotes(text, delimiter):
        notes.append((
            "quote_not_closed",
            f"{what} ends inside a quoted cell: an opening quote mark has no closing one, so "
            "everything after it was read as a single cell. Check the last rows.",
            "code",
        ))
    return _Table(numbered, delimiter, notes)


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _is_prose(text: str) -> bool:
    return len(text.strip()) > 40 or len(text.split()) > 5


def _looks_like_data(cells: list[str]) -> bool:
    """A first row that holds a number or a sentence is data, not column names."""
    return any(_is_prose(c) or _is_number(c.strip()) for c in cells)


def _assumed_order(header: list[str], first_is_data: bool, order: str, unit: str) -> str:
    """The sentence for a file whose column names were not recognized. It quotes the
    row that was taken as column names, so a first row lost that way can be seen."""
    if first_is_data:
        return (f"We did not recognise any column names, so we read the columns in this order: "
                f"{order}. The first row looked like {unit}, so we loaded it too.")
    names = ", ".join(f'"{h.strip()[:30]}"' for h in header if h.strip())
    return (f"We did not recognise the column names ({names}), so we read the columns in this "
            f"order: {order}. If that first row is {unit} and not column names, add a row of "
            "column names above it and upload again.")


class _Columns:
    """Maps our field names to column positions, first matching alias wins."""

    def __init__(self, header: list[str]):
        self.header = header
        self.norm = [_norm(h) for h in header]
        self.used: set[int] = set()
        self.named = True       # False when the first row turned out to be data

    def take(self, aliases: list[str]) -> int | None:
        for alias in aliases:
            for i, name in enumerate(self.norm):
                if name == alias and i not in self.used:
                    self.used.add(i)
                    return i
        return None

    def names(self, positions: list[int]) -> str:
        return ", ".join(
            f'"{self.header[i].strip()}"'
            if self.named and i < len(self.header) and self.header[i].strip() else f"column {i + 1}"
            for i in positions
        )

    def spare(self, rows: list[tuple[int, list[str]]], named_only: bool = True) -> list[int]:
        """Columns that were not used: those with a name nobody asked for, those without
        a name that hold something, and cells beyond the last column name."""
        width = max([len(self.header)] + [len(cells) for _, cells in rows])
        return [
            i for i in range(width) if i not in self.used and (
                (named_only and self.named and i < len(self.header) and self.header[i].strip())
                or any(i < len(cells) and cells[i].strip() for _, cells in rows)
            )
        ]

    def leftovers(self, rows: list[tuple[int, list[str]]]) -> str:
        """A sentence naming everything in the file that was not used, or ""."""
        spare = self.spare(rows)
        return f"These columns were not used: {self.names(spare)}." if spare else ""


# --- Problem Info CSV --------------------------------------------------------

# Accepted column names, first match wins. The MVP's rubric CSV (problem_id,
# problem_text, item, criterion_text, points) is the long shape under other names.
_PID = ["problem_id", "version_id", "version", "problem_version", "problem", "question_id",
        "question", "q", "id"]
_PTEXT = ["problem_text", "problem_body", "problem_statement", "question_text", "prompt",
          "statement", "problem", "question"]
_INSTRUCTIONS = ["instructions", "student_instructions"]
_KEY = ["item_no", "item_number", "item_id", "item_key", "criterion_id", "key", "no", "number",
        "item"]
_TEXT = ["rubric_item", "rubric_items", "criterion_text", "item_text", "criterion", "criteria",
         "rubric_text", "description", "rubric", "text", "item", "items"]
_WEIGHT = ["weight", "rubric_score", "score", "points", "max_points", "rubric_weight",
           "item_weight"]
_AMBIGUOUS_PID = {"problem", "question", "q"}   # may hold the problem text instead of an id

# Wide shape: rubric_item_1, weight_1, rubric_item_2, weight_2, ...
_WIDE_TEXT = re.compile(
    r"^(?:rubric_items?|items?|item_text|criterion|criteria|criterion_text|rubric)_?(\d+)(?:_text)?$")
_WIDE_WEIGHT = re.compile(
    r"^(?:weight|rubric_score|score|points?|max_points|rubric_weight|item_weight)_?(\d+)$"
    r"|^(?:rubric_items?|items?|criterion)_?(\d+)_(?:weight|rubric_score|score|points?)$")

# Chen's whole-rubric cells mark each item with a "# Item n:" line.
_ITEM_MARK = re.compile(r"^[ \t]*#+[ \t]*item[ \t]*(\d+)[ \t]*[:.)\-]?[ \t]*", re.I | re.M)


def _tidy(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _split_items(cell: str) -> tuple[list[tuple[str, str]], str] | None:
    """(item number, text) for each "# Item n:" block, plus any text before the first."""
    marks = list(_ITEM_MARK.finditer(cell))
    if not marks:
        return None
    parts = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(cell)
        parts.append((m.group(1), _tidy(cell[m.end():end])))
    return parts, cell[:marks[0].start()].strip()


def _weight(raw: str) -> tuple[float, str]:
    """(points, state). state: ok | missing (code fills 1) | invalid (the owner fixes it)."""
    if not raw:
        return 1.0, "missing"
    try:
        value = float(raw)
    except ValueError:
        return 0.0, "invalid"
    if not math.isfinite(value):
        return 0.0, "invalid"
    return value, "ok" if value > 0 else "invalid"


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


@dataclass
class _ItemRow:
    """One rubric item as the file gives it, before it has its final number."""

    version: Problem
    key: str            # "" when the file gives no item number
    text: str
    raw_weight: str
    row: int
    joined: bool        # the row had no problem id and was put with the version above
    shared_weight: int  # > 0: this many items share one weight cell


@dataclass
class _Bank:
    """A bank under construction: versions, their items, and what was noticed."""

    versions: list[Problem] = field(default_factory=list)
    items: dict[str, list[Criterion]] = field(default_factory=dict)
    pending: list[_ItemRow] = field(default_factory=list)
    taken: set[str] = field(default_factory=set)        # normalized version ids
    findings: list[Finding] = field(default_factory=list)
    has_key_column: bool = False
    numbered_in_order: int = 0

    def note(self, code: str, message: str, filled: str = "note", box: str = "file", **where) -> None:
        self.findings.append(Finding(code=code, message=message, filled=filled, box=box, **where))

    def version(self, wanted: str, row: int) -> Problem:
        """A new version called ``wanted``; code names it when the file does not."""
        if not wanted:
            vid = rubric.next_version_id(self.taken, len(self.versions) + 1)
            self.note("version_id_filled",
                      f"One version had no problem id in the file, so we named it {vid}.",
                      "code", "version_id", problem_id=vid, value=vid, row=row)
        else:
            vid = rubric.unique_id(wanted, self.taken)
            if vid != wanted:
                self.note("version_id_duplicate",
                          f'Two versions are called "{wanted}" in the file. We kept both and '
                          f"renamed the second one {vid}.",
                          "code", "version_id", problem_id=vid, value=vid, row=row)
        version = Problem(id=vid)
        self.versions.append(version)
        self.items[vid] = []
        self.taken.add(rubric.norm_id(vid))
        return version

    def find(self, pid: str) -> Problem | None:
        wanted = rubric.norm_id(pid)
        return next((p for p in self.versions if rubric.norm_id(p.id) == wanted), None)

    def describe(self, version: Problem, text: str, instructions: str, row: int) -> None:
        """Problem text and instructions: the first non-empty value wins, and a
        different later value is reported, not dropped in silence."""
        if text and not version.text:
            version.text = text
        elif text and not _same_text(version.text, text):
            self.note("version_text_conflict",
                      f"Version {version.id} has two different problem texts in the file. "
                      "We kept the first one.",
                      "code", "version_text", problem_id=version.id, value=version.text, row=row)
        if instructions and not version.instructions:
            version.instructions = instructions
        elif instructions and not _same_text(version.instructions, instructions):
            self.note("version_instructions_conflict",
                      f"Version {version.id} has two different instructions to students in the "
                      "file. We kept the first one.",
                      "code", "version_instructions", problem_id=version.id,
                      value=version.instructions, row=row)

    def rubric_cell(self, version: Problem, key: str, text: str, raw_weight: str, row: int,
                    joined: bool = False) -> None:
        """One row's rubric cell: a single item, or several behind "# Item n:" marks.
        Items wait in ``pending`` until every row is read (see ``number_items``)."""
        split = _split_items(text)
        if split is None:
            self.pending.append(_ItemRow(version, key, text, raw_weight, row, joined, 0))
            return
        parts, lead = split
        if lead:
            self.note("rubric_cell_lead",
                      f"Version {version.id}: the text before the first \"# Item\" mark in the "
                      f'rubric cell was not used ("{lead[:80]}").',
                      problem_id=version.id, row=row)
        weights = [w for w in re.split(r"[;,|/\s]+", raw_weight) if w]
        if not weights:
            weights = [""] * len(parts)
        shared = len(parts) if len(weights) != len(parts) else 0
        for i, (number, part) in enumerate(parts):
            self.pending.append(_ItemRow(
                version, number, part, raw_weight if shared else weights[i], row, joined, shared))

    def number_items(self) -> None:
        """Turn the pending rows into items. Numbers the file gives are kept; an item
        without one gets the lowest number no item of its version uses, so a number
        written further down the file is never taken away from its own item."""
        given: dict[str, set[str]] = {}
        for it in self.pending:
            if it.key:
                given.setdefault(it.version.id, set()).add(rubric.norm_id(it.key))
        for it in self.pending:
            self._item(it, given.get(it.version.id, set()))
        self.pending = []

    def _item(self, it: _ItemRow, given: set[str]) -> None:
        version, key = it.version, it.key
        items = self.items[version.id]
        keys = {rubric.norm_id(c.key) for c in items}
        where = {"problem_id": version.id, "row": it.row}
        if not key:
            key = rubric.next_item_key(keys | given)
            if self.has_key_column:
                self.note("item_number_filled",
                          f"One item of version {version.id} had no item number in the file, so "
                          f"we numbered it {key}.",
                          "code", "item_key", item_key=key, value=key, **where)
            else:
                self.numbered_in_order += 1
        elif rubric.norm_id(key) in keys:
            wanted, key = key, rubric.unique_id(key, keys | given)
            self.note("item_number_duplicate",
                      f"Version {version.id} has two items numbered {wanted} in the file. We kept "
                      f"both and renamed the second one {key}.",
                      "code", "item_key", item_key=key, value=key, **where)
        where["item_key"] = key

        if it.joined:
            self.note("row_joined_version",
                      f"A row had no problem id, so we put its item with version {version.id}, "
                      "the version on the row above.",
                      "code", "item", **where)
        if it.shared_weight:
            points = 1.0
            self.note("weight_filled",
                      f'The file gives one weight ("{it.raw_weight}") for {it.shared_weight} items '
                      f"in one cell, so we set item {key} of version {version.id} to 1.",
                      "code", "weight", value="1", **where)
        else:
            points, state = _weight(it.raw_weight)
            if state == "missing":
                self.note("weight_filled",
                          f"Item {key} of version {version.id} had no weight in the file, so we "
                          "set it to 1.",
                          "code", "weight", value="1", **where)
            elif state == "invalid":
                self.note("weight_invalid",
                          f'The weight of item {key} of version {version.id} is "{it.raw_weight}" '
                          "in the file. A weight has to be a number above zero.",
                          "owner", "weight", value=it.raw_weight, **where)
        if not it.text:
            self.note("item_text_missing",
                      f"Item {key} of version {version.id} has no text in the file.",
                      "owner", "item_text", **where)
        items.append(Criterion(
            id=f"{version.id}#{key}", problem_id=version.id, key=key, label=f"Item {key}",
            text=it.text, source_text=it.text, points=points,
        ))


def load_problem_info(csv_text: str, name: str = "") -> tuple[Assessment, list[Finding]]:
    """A Problem Info CSV -> a draft bank plus findings.

    Long shape (one row per rubric item)::

        problem_id,problem_text,rubric_item,weight

    Wide shape (one row per version)::

        problem_id,problem_text,rubric_item_1,weight_1,rubric_item_2,weight_2

    Raises ``NothingToLoad`` only when there is nothing in the file to load.
    """
    table = _table(csv_text, "The problem info file")
    bank = _Bank()
    for code, message, filled in table.notes:
        bank.note(code, message, filled)

    header = table.rows[0][1]
    rows = table.rows[1:]
    cols = _Columns(header)
    pid_col = cols.take(_PID)
    ptext_col = cols.take(_PTEXT)
    instr_col = cols.take(_INSTRUCTIONS)
    weight_col = cols.take(_WEIGHT)
    key_col = cols.take(_KEY)
    text_col = cols.take(_TEXT)

    wide: dict[int, list[int | None]] = {}      # item number -> [text column, weight column]
    for i, norm in enumerate(cols.norm):
        if i in cols.used:
            continue
        m_text, m_weight = _WIDE_TEXT.match(norm), _WIDE_WEIGHT.match(norm)
        if not (m_text or m_weight):
            continue
        number = int(m_text.group(1) if m_text else m_weight.group(1) or m_weight.group(2))
        pair = wide.setdefault(number, [None, None])
        slot = 0 if m_text else 1
        if pair[slot] is None:      # a second column for the same item stays "not used"
            pair[slot] = i
            cols.used.add(i)
    is_wide = any(t is not None for t, _ in wide.values())
    if wide and not is_wide:                    # numbered weights without numbered items
        cols.used -= {w for _, w in wide.values() if w is not None}
        wide = {}
    if is_wide:
        if text_col is not None:
            if 1 in wide:
                # "rubric_item, rubric_item.1, ..." (as pandas and R write repeated
                # names): the unnumbered column comes first, the others move up.
                wide = {n + 1: pair for n, pair in wide.items()}
                bank.note("columns_renumbered",
                          f"The file has the column {cols.names([text_col])} next to numbered "
                          "item columns, so we read it as item 1 and moved the others up by one.")
            wide[1] = [text_col, weight_col]
            text_col = weight_col = None
        # One row per version: other unnumbered item columns have no meaning here.
        cols.used -= {c for c in (key_col, text_col, weight_col) if c is not None}
        key_col = text_col = weight_col = None

    if not cols.used:
        # No column name recognized: assume the documented order, and say so.
        first_is_data = _looks_like_data(header)
        if first_is_data:
            rows, cols.named = table.rows, False
        pid_col, ptext_col, text_col, weight_col = (
            (i if i < len(header) else None) for i in range(4))
        cols.used.update(range(min(4, len(header))))
        bank.note("columns_assumed", _assumed_order(
            header, first_is_data, "problem id, problem text, rubric item, weight",
            "part of the bank"), "code")
    else:
        if text_col is None and not is_wide and key_col is not None and cols.norm[key_col] in ("item", "items"):
            text_col, key_col = key_col, None   # a lone "item" column holds the item text
        if (ptext_col is None and pid_col is not None and cols.norm[pid_col] in _AMBIGUOUS_PID
                and any(_is_prose(row[pid_col]) for _, row in rows if pid_col < len(row))):
            bank.note("problem_column_is_text",
                      f"We read the column {cols.names([pid_col])} as the problem text, because it "
                      "holds sentences rather than short names.",
                      "code")
            ptext_col, pid_col = pid_col, cols.take(_PID)   # another column may hold the ids

    bank.has_key_column = key_col is not None
    current: Problem | None = None

    for row_no, cells in rows:
        def cell(col: int | None) -> str:
            return cells[col].strip() if col is not None and col < len(cells) else ""

        pid, ptext, instr = cell(pid_col), cell(ptext_col), cell(instr_col)
        if is_wide:
            numbered_items = [
                (n, cell(t), cell(w)) for n, (t, w) in sorted(wide.items())
                if cell(t) or cell(w)
            ]
            if not (pid or ptext or instr or numbered_items):
                continue
            version = bank.version(pid, row_no)     # one row per version: a repeat is a duplicate
            bank.describe(version, ptext, instr, row_no)
            for n, text, raw_weight in numbered_items:
                bank.rubric_cell(version, str(n), text, raw_weight, row_no)
            continue

        key, text, raw_weight = cell(key_col), cell(text_col), cell(weight_col)
        if not (pid or ptext or instr or key or text or raw_weight):
            continue
        joined = False
        if pid:
            version = bank.find(pid) or bank.version(pid, row_no)
        elif ptext:
            version = next((p for p in bank.versions if p.text and _same_text(p.text, ptext)), None)
            version = version or bank.version("", row_no)
        elif current is not None:
            version, joined = current, pid_col is not None
        else:
            version = bank.version("", row_no)
        current = version
        bank.describe(version, ptext, instr, row_no)
        if key or text or raw_weight:
            bank.rubric_cell(version, key, text, raw_weight, row_no, joined)

    if not bank.versions:
        raise NothingToLoad(
            "The problem info file has column names but no rows, so there is nothing to load.")
    bank.number_items()

    for p in bank.versions:
        if not p.text:
            bank.note("version_text_missing", f"Version {p.id} has no problem text in the file.",
                      "owner", "version_text", problem_id=p.id)
        if not bank.items[p.id]:
            bank.note("rubric_missing", f"Version {p.id} has no rubric items in the file.",
                      "owner", "items", problem_id=p.id)
    if bank.numbered_in_order:
        bank.note("items_numbered",
                  "The file has no item number column, so the items of each version are numbered "
                  "1, 2, 3 in the order they appear.")
    if cols.leftovers(rows):
        bank.note("columns_ignored", cols.leftovers(rows))

    assessment = Assessment(
        name=(name or "").strip() or "Problem bank", source="csv", kind="bank",
        rubric_version="uploaded", problems=bank.versions,
        criteria=[c for p in bank.versions for c in bank.items[p.id]],
        findings=bank.findings,
    )
    return assessment, bank.findings


def problem_info_csv(bank: Assessment) -> str:
    """The bank's current rubric as a Problem Info CSV in the long shape. The
    ``instructions`` and ``item`` columns appear only when they carry something."""
    with_instructions = any(p.instructions.strip() for p in bank.problems)
    plain_keys = all(
        [c.key for c in bank.criteria_for(p.id)] == [str(i + 1) for i in range(len(bank.criteria_for(p.id)))]
        for p in bank.problems
    )
    header = ["problem_id", "problem_text"]
    header += ["instructions"] if with_instructions else []
    header += [] if plain_keys else ["item"]
    header += ["rubric_item", "weight"]

    buf = io.StringIO()
    out = csv.writer(buf, lineterminator="\n")
    out.writerow(header)
    for p in bank.problems:
        for i, c in enumerate(bank.criteria_for(p.id) or [None]):
            row = [p.id, p.text if i == 0 else ""]
            if with_instructions:
                row.append(p.instructions if i == 0 else "")
            if not plain_keys:
                row.append(c.key if c else "")
            # A weight that is not set yet stays empty; any other number is written in full.
            row += [c.text, "" if c.points == 0 else f"{c.points:.15g}"] if c else ["", ""]
            out.writerow(row)
    return buf.getvalue()


# --- responses CSV -----------------------------------------------------------

_SID = ["response_id", "id", "student_id", "student", "submission_id", "pseudonym"]
_VERSION = ["problem_id", "version_id", "version", "problem_version", "problem", "question_id",
            "question", "q"]
_RESPONSE = ["response_text", "response", "answer", "explanation", "text"]


def _unique(wanted: str, taken: set[str]) -> str:
    if wanted not in taken:
        return wanted
    n = 2
    while f"{wanted}-{n}" in taken:
        n += 1
    return f"{wanted}-{n}"


def parse_responses(csv_text: str, bank: Assessment) -> tuple[list[Response], list[Finding]]:
    """A responses CSV (``student_id, problem_id, response_text``, matched loosely)
    -> responses plus findings. A response whose version cannot be matched exactly
    (ignoring case and spaces) is kept with ``problem_id=""``: it is held until the
    owner gives it a version or leaves it out.

    Raises ``NothingToLoad`` only when the file holds no responses at all.
    """
    table = _table(csv_text, "The responses file")
    found: list[Finding] = []

    def note(code: str, message: str, filled: str = "note", box: str = "file", **where) -> None:
        found.append(Finding(code=code, message=message, filled=filled, box=box,
                             scope="responses", **where))

    for code, message, filled in table.notes:
        note(code, message, filled)
    header = table.rows[0][1]
    rows = table.rows[1:]
    cols = _Columns(header)
    sid_col = cols.take(_SID)
    version_col = cols.take(_VERSION)
    text_col = cols.take(_RESPONSE)

    if not cols.used:
        first_is_data = _looks_like_data(header)
        if first_is_data:
            rows, cols.named = table.rows, False
        n = len(header)
        sid_col, version_col, text_col = (0, 1, 2) if n >= 3 else (0, None, 1) if n == 2 else (None, None, 0)
        cols.used.update(range(min(3, n)))
        order = {3: "student id, version, response", 2: "student id, response"}.get(min(n, 3), "response")
        note("columns_assumed", _assumed_order(header, first_is_data, order, "a response"), "code")
    elif text_col is None:
        # No column is named like the answers. Take the spare column holding the most
        # text, and say so: the owner sees which one, and can rename it if it is wrong.
        candidates = cols.spare(rows, named_only=False)
        if not candidates:
            raise NothingToLoad(
                "We could not find the students' answers in the responses file. It needs a column "
                "holding what each student wrote, for example response_text.")
        text_col = max(candidates, key=lambda i: sum(len(c[i].strip()) for _, c in rows if i < len(c)))
        cols.used.add(text_col)
        note("response_column_assumed",
             f"We read the column {cols.names([text_col])} as the students' answers"
             + (", because it holds the most text." if len(candidates) > 1 else "."), "code")

    versions = bank.problems
    only = versions[0].id if len(versions) == 1 else ""
    responses: list[Response] = []
    taken: set[str] = set()
    unknown: dict[str, int] = {}        # uploaded label -> how many could not be matched
    other_labels: dict[str, int] = {}   # single-version bank: labels that named something else
    empty = 0

    for n, (row_no, cells) in enumerate(rows, start=1):
        def cell(col: int | None) -> str:
            return cells[col] if col is not None and col < len(cells) else ""

        sid, label, text = cell(sid_col).strip(), cell(version_col).strip(), cell(text_col)
        if not (sid or label or text.strip()):
            continue
        rid = sid or f"R{n:03d}"
        if not sid and sid_col is not None:
            note("student_id_filled",
                 f"One response had no student id in the file, so we called it {rid}.",
                 "code", "student_id", response_id=rid, value=rid, row=row_no)
        if rid in taken:
            wanted, rid = rid, _unique(rid, taken)
            note("student_id_duplicate",
                 f'Two responses have the id "{wanted}" in the file. We kept both and renamed the '
                 f"second one {rid}.",
                 "code", "student_id", response_id=rid, value=rid, row=row_no)
        taken.add(rid)

        if only:
            problem_id = only
            if label and rubric.norm_id(label) != rubric.norm_id(only):
                other_labels[label] = other_labels.get(label, 0) + 1
        else:
            # Only an exact match, ignoring case and spaces. Anything else is the owner's call.
            wanted = rubric.norm_id(label)
            problem_id = next((p.id for p in versions if rubric.norm_id(p.id) == wanted), "")
            if not problem_id:
                unknown[label] = unknown.get(label, 0) + 1
        if not text.strip():
            empty += 1
        responses.append(Response(
            id=rid, problem_id=problem_id, text=text, external_id=sid or rid, version_label=label,
        ))

    if not responses:
        raise NothingToLoad(
            "The responses file has column names but no responses, so there is nothing to load.")

    total = len(responses)
    if only:
        message = f"This bank has one version ({only}), so all {total} responses were matched to it."
        if other_labels:
            named = ", ".join(f'"{label}"' for label in other_labels)
            message += (f" {sum(other_labels.values())} of them name a different version in the "
                        f"file ({named}).")
        note("single_version", message, "code" if other_labels else "note", "response_version",
             problem_id=only)
    for label, count in unknown.items():
        say = "response says" if count == 1 else "responses say"
        dont = "response does not say" if count == 1 else "responses do not say"
        note("response_version_unknown",
             (f'{count} {say} version "{label}", which is not one of this bank\'s versions.'
              if label else f"{count} {dont} which version of the problem was answered."),
             "owner", "response_version", value=label)
    if sid_col is None:
        note("student_ids_numbered",
             "The file has no student id column, so the responses are numbered R001, R002, ... "
             "in file order.", "code", "student_id")
    if empty:
        note("responses_empty",
             f"{empty} {'response is' if empty == 1 else 'responses are'} empty. "
             "Empty responses are still graded, as empty answers.")
    if cols.leftovers(rows):
        note("columns_ignored", cols.leftovers(rows))
    return responses, found
