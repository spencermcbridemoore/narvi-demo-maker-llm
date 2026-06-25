"""Graph nodes for the full 7-stage workflow.

Stages (each "ask the user" node pauses with ``interrupt()``):

    elicit  -> scope -> plan_review -> generate -> review -> diagnose -> fix
                                                       │                   │
                                       (looks good) ───┘   (loops back) ───┘
    review ──looks good──> extend ──add more──> scope (loop)
                                  └──done──────> finalize -> END

Routing convention: each node sets ``state["stage"]`` to the node it routes to
next. Static edges follow the obvious successor; conditional edges
(``plan_review``, ``review``, ``extend``) read ``state["stage"]`` back out. The
same field drives the flowchart's "you are here" highlight.

Compute nodes (``scope``, ``generate``, ``fix``) call the LLM via
``app.llm.complete`` and fall back to deterministic templates when no provider is
reachable, so the whole loop runs with or without a model.

``interrupt()`` is always the FIRST statement in an "ask" node: on resume the node
re-runs from the top, so anything before it would execute twice. Expensive work
(LLM calls) therefore lives in separate compute-only nodes, never before an
interrupt.
"""

from __future__ import annotations

from langgraph.types import interrupt

from ..llm.complete import extract_html, llm_text, usable_html
from ..llm.factory import Role
from ..llm import prompts
from . import templates
from .state import DemoState


def _decision(value, known: set[str], text_route: str) -> tuple[str, str]:
    """Interpret an interrupt resume value.

    Returns (route, free_text). A value equal to a known choice is a button click
    (no extra text); anything else is free text routed via ``text_route``.
    """
    text = value.strip() if isinstance(value, str) else str(value)
    if text in known:
        return text, ""
    return text_route, text


# --- 1. Elicit ------------------------------------------------------------
def elicit(state: DemoState) -> DemoState:
    answer = interrupt(
        {
            "stage": "elicit",
            "type": "ask",
            "prompt": "What interactive demo would you like to build? Describe it in a sentence or two.",
            "allowText": True,
        }
    )
    spec = answer.strip() if isinstance(answer, str) else str(answer)
    return {"spec": spec, "stage": "scope"}


# --- 2. Scope (compute the plan) -----------------------------------------
def scope(state: DemoState) -> DemoState:
    spec = state.get("spec", "")
    plan = llm_text(Role.SCOPE, prompts.SCOPE_SYSTEM, prompts.scope_user(spec))
    return {"plan": plan or templates.fallback_plan(spec), "stage": "plan_review"}


# --- 2b. Plan review (confirm or revise) ---------------------------------
def plan_review(state: DemoState) -> DemoState:
    decision = interrupt(
        {
            "stage": "plan_review",
            "type": "choice",
            "prompt": "Here's my plan. Shall I build it?",
            "context": state.get("plan", ""),
            "choices": [
                {"label": "Build it", "value": "approve"},
                {"label": "Revise the plan", "value": "revise"},
            ],
            "allowText": True,
        }
    )
    route, text = _decision(decision, {"approve", "revise"}, text_route="revise")
    if route == "approve":
        return {"stage": "generate"}
    update: DemoState = {"stage": "scope"}
    if text:
        update["spec"] = state.get("spec", "") + f"\n\nPlan feedback: {text}"
    return update


# --- 3. Generate (compute the artifact) ----------------------------------
def generate(state: DemoState) -> DemoState:
    raw = llm_text(
        Role.GENERATION,
        prompts.GENERATE_SYSTEM,
        prompts.generate_user(state.get("spec", ""), state.get("plan", "")),
    )
    html = extract_html(raw) if raw else ""
    fell_back = not usable_html(html)
    if fell_back:
        html = templates.fallback_html(state.get("spec", ""), state.get("plan", ""))
    return {"html": html, "stage": "review", "used_fallback": fell_back}


# --- 4. Review (looks good / needs changes) ------------------------------
def review(state: DemoState) -> DemoState:
    decision = interrupt(
        {
            "stage": "review",
            "type": "choice",
            "prompt": "Take a look in the Preview panel. How does it look?",
            "choices": [
                {"label": "Looks good \U0001F44D", "value": "looks_good"},
                {"label": "Needs changes", "value": "needs_changes"},
            ],
            "allowText": False,
        }
    )
    route, _ = _decision(decision, {"looks_good", "needs_changes"}, text_route="needs_changes")
    return {"stage": "extend" if route == "looks_good" else "diagnose"}


# --- 5. Diagnose (collect what to change) --------------------------------
def diagnose(state: DemoState) -> DemoState:
    report = interrupt(
        {
            "stage": "diagnose",
            "type": "ask",
            "prompt": "What would you like to change or fix? Describe what's off or what to add.",
            "allowText": True,
        }
    )
    text = report.strip() if isinstance(report, str) else str(report)
    return {"issues": [text], "stage": "fix"}


# --- 6. Fix (apply the change, regenerate) -------------------------------
def fix(state: DemoState) -> DemoState:
    issues = state.get("issues") or [""]
    issue = issues[-1]
    raw = llm_text(Role.FIX, prompts.FIX_SYSTEM, prompts.fix_user(state.get("html", ""), issue))
    html = extract_html(raw) if raw else ""
    fell_back = not usable_html(html)
    if fell_back:
        html = templates.fallback_fix(state.get("html", ""), issue)
    return {"html": html, "stage": "review", "used_fallback": fell_back}


# --- 7. Extend (add more, or finish) -------------------------------------
def extend(state: DemoState) -> DemoState:
    decision = interrupt(
        {
            "stage": "extend",
            "type": "choice",
            "prompt": "Anything else to add or change? Describe it below, or finish.",
            "choices": [{"label": "I'm done ✓", "value": "done"}],
            "allowText": True,
        }
    )
    route, text = _decision(decision, {"done"}, text_route="add_more")
    if route == "done":
        return {"stage": "finalize"}
    update: DemoState = {"stage": "scope"}
    if text:
        update["spec"] = state.get("spec", "") + f"\n\nAlso add: {text}"
    return update


# --- 8. Finalize / Export ------------------------------------------------
def finalize(state: DemoState) -> DemoState:
    return {"done": True, "stage": "done"}
