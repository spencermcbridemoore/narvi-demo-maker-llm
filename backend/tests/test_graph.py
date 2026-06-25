"""Tests for the workflow graph: interrupt/resume, routing, and the cyclic edges.

The LLM is forced offline (see conftest), so Generate/Fix produce deterministic
placeholder HTML — which is exactly what lets us assert on routing/state.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graph.build import build_graph

INITIAL = {"stage": "elicit", "spec": "", "html": "", "issues": []}


def _graph():
    return build_graph().compile(checkpointer=InMemorySaver())


def _stage(graph, cfg) -> str:
    return graph.get_state(cfg).values.get("stage")


def test_first_invoke_interrupts_at_elicit():
    g = _graph()
    cfg = {"configurable": {"thread_id": "a"}}
    result = g.invoke(INITIAL, cfg)
    assert "__interrupt__" in result
    assert g.get_state(cfg).next == ("elicit",)


def test_full_cyclic_loop():
    g = _graph()
    cfg = {"configurable": {"thread_id": "b"}}
    g.invoke(INITIAL, cfg)

    g.invoke(Command(resume="a bouncing ball demo"), cfg)        # elicit -> scope -> plan_review
    assert _stage(g, cfg) == "plan_review"

    g.invoke(Command(resume="approve"), cfg)                     # -> generate -> review
    assert _stage(g, cfg) == "review"
    assert g.get_state(cfg).values["used_fallback"] is True      # offline -> placeholder

    g.invoke(Command(resume="needs_changes"), cfg)               # review -> diagnose
    assert _stage(g, cfg) == "diagnose"

    g.invoke(Command(resume="make the ball red"), cfg)           # diagnose -> fix -> review (loop)
    assert _stage(g, cfg) == "review"
    assert g.get_state(cfg).values["issues"][-1] == "make the ball red"

    g.invoke(Command(resume="looks_good"), cfg)                  # review -> extend
    assert _stage(g, cfg) == "extend"

    g.invoke(Command(resume="add a reset button"), cfg)          # extend -> scope (loop)
    assert _stage(g, cfg) == "plan_review"
    assert "add a reset button" in g.get_state(cfg).values["spec"]

    g.invoke(Command(resume="approve"), cfg)
    g.invoke(Command(resume="looks_good"), cfg)
    g.invoke(Command(resume="done"), cfg)                        # extend -> finalize -> END

    snap = g.get_state(cfg)
    assert snap.values.get("done") is True
    assert snap.next == ()
    assert snap.values["html"].lstrip().lower().startswith("<!doctype")


def test_revise_loops_back_to_scope():
    g = _graph()
    cfg = {"configurable": {"thread_id": "c"}}
    g.invoke(INITIAL, cfg)
    g.invoke(Command(resume="a demo"), cfg)                      # -> plan_review
    g.invoke(Command(resume="make the plan simpler"), cfg)      # revise -> scope -> plan_review
    assert _stage(g, cfg) == "plan_review"
    assert "make the plan simpler" in g.get_state(cfg).values["spec"]


def test_mermaid_contains_all_nodes():
    mermaid = _graph().get_graph().draw_mermaid()
    for node in (
        "elicit", "scope", "plan_review", "generate",
        "review", "diagnose", "fix", "extend", "finalize",
    ):
        assert node in mermaid
