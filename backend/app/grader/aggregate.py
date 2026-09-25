"""k samples -> one binary final, a confidence, and review flags.

Final outcomes are always 0/1. The in-between states stay internal and resolve by
fixed rules:
  * split votes  -> majority (use an odd k); confidence = majority share
  * a tie (only possible when some samples failed) -> TIE_DEFAULT, flagged
  * a teacher override always wins (resolved_by = "teacher")
Flags mark cells for review regardless of the confidence threshold.
"""

from __future__ import annotations

import re

from .model import Cell

TIE_DEFAULT = 0  # strict: an unresolved tie earns no credit until a teacher decides

# Flags that always send a cell to review, whatever the slider says.
HARD_FLAGS = ("error", "tie", "quote_missing", "no_evidence", "addresses_grader")

_QUOTES = "\"'`“”‘’«»"
_DASHES = "–—−"
_SPACE = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\.\.\.|…|\[\.\.\.\]")

# Text in a response that talks to the grader instead of answering the problem.
_GRADER_DIRECTED = re.compile(
    r"(note|message|dear|attention)\s+(to\s+)?(the\s+)?(grader|ta|marker|ai)\b"
    r"|\b(give|award)\s+(me\s+)?(full|all|the)\s+(credit|marks|points)\b"
    r"|\bignore\s+(the\s+|all\s+|any\s+|previous\s+|your\s+)*(rubric|instructions)\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    t = text.lower()
    for ch in _QUOTES:
        t = t.replace(ch, "")
    for ch in _DASHES:
        t = t.replace(ch, "-")
    return _SPACE.sub(" ", t).strip(" .,;:")


def quotes_found(evidence: list[str], response_text: str) -> bool | None:
    """True if every quoted fragment appears in the response (whitespace/quote-insensitive)."""
    if not evidence:
        return None
    haystack = _normalize(response_text)
    for quote in evidence:
        for fragment in _ELLIPSIS.split(quote):
            f = _normalize(fragment)
            if len(f) >= 4 and f not in haystack:
                return False
    return True


def addresses_grader(response_text: str) -> bool:
    return bool(_GRADER_DIRECTED.search(response_text))


def resolve(cell: Cell, grader_directed: bool = False) -> Cell:
    """Recompute final/confidence/flags from the cell's samples (and override)."""
    samples = cell.samples
    votes = [s.verdict for s in samples if s.verdict is not None]
    flags: list[str] = []
    if any(s.verdict is None for s in samples):
        flags.append("error")

    if not votes:
        cell.final, cell.confidence, cell.votes, cell.resolved_by = None, None, "", ""
    else:
        ones, n = sum(votes), len(votes)
        agree = max(ones, n - ones)
        if ones * 2 == n:
            cell.final, cell.resolved_by = TIE_DEFAULT, "tie_default"
            flags.append("tie")
        else:
            cell.final, cell.resolved_by = int(ones * 2 > n), "majority"
        cell.confidence = round(agree / n, 4)
        cell.votes = f"{agree}/{n}"
        if 0 < ones < n:
            flags.append("split")
        winners = [s for s in samples if s.verdict == cell.final]
        if any(s.quotes_found is False for s in winners):
            flags.append("quote_missing")
        if cell.final == 1 and winners and all(not s.evidence for s in winners):
            flags.append("no_evidence")

    if grader_directed:
        flags.append("addresses_grader")
    cell.flags = flags
    if cell.override is not None:
        cell.final, cell.resolved_by = cell.override, "teacher"
    return cell


def needs_review(cell: Cell, threshold: float) -> bool:
    if cell.override is not None:
        return False
    if any(f in HARD_FLAGS for f in cell.flags):
        return True
    return cell.confidence is not None and cell.confidence < threshold
