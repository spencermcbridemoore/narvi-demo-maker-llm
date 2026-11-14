"""How the grader's 0/1 grades compare with a human's, item by item.

Generic: any list of (human, ai) pairs. Used by ``batch`` for exports that carry
human grades (``rubric_1`` .. ``rubric_N``); the same numbers the Newton scripts print
(agreement, Cohen's kappa, which way the disagreements go).
"""

from __future__ import annotations


def stats(pairs: list[tuple[int, int]]) -> dict:
    """``pairs`` = (human, ai) with both 0/1. Kappa is None when it is undefined
    (both graders gave everything the same grade)."""
    n = len(pairs)
    if not n:
        return {"n": 0, "agree": None, "kappa": None, "human_yes": None, "ai_yes": None,
                "too_strict": 0, "too_generous": 0}
    agree = sum(h == a for h, a in pairs) / n
    h1 = sum(h for h, _ in pairs) / n
    a1 = sum(a for _, a in pairs) / n
    chance = h1 * a1 + (1 - h1) * (1 - a1)
    kappa = None if chance >= 1 else (agree - chance) / (1 - chance)
    return {
        "n": n, "agree": agree, "kappa": kappa, "human_yes": h1, "ai_yes": a1,
        "too_strict": sum(h == 1 and a == 0 for h, a in pairs),      # human gave credit, AI did not
        "too_generous": sum(h == 0 and a == 1 for h, a in pairs),    # AI gave credit, human did not
    }


def pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def num(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f}"
