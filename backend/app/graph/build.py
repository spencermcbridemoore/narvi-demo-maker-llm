"""Assemble the full DemoBuilder ``StateGraph``.

Topology (this is the real graph the flowchart is drawn from):

    START → elicit → scope → plan_review ─approve─► generate → review
                        ▲          │                              │
                        └──revise──┘                              │
    review ─looks_good─► extend ─add more─► scope (loop)          │
                              └─done─► finalize → END             │
    review ─needs_changes─► diagnose → fix → review (loop) ◄──────┘

Conditional edges route on ``state["stage"]`` (each node sets it to its chosen
successor). Passing explicit path maps also lets ``draw_mermaid()`` render the
branches. A checkpointer is REQUIRED (the "ask" nodes use ``interrupt()``).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import DemoState


def _route(state: DemoState) -> str:
    return state.get("stage", "")


def build_graph() -> StateGraph:
    builder = StateGraph(DemoState)

    builder.add_node("elicit", nodes.elicit)
    builder.add_node("scope", nodes.scope)
    builder.add_node("plan_review", nodes.plan_review)
    builder.add_node("generate", nodes.generate)
    builder.add_node("review", nodes.review)
    builder.add_node("diagnose", nodes.diagnose)
    builder.add_node("fix", nodes.fix)
    builder.add_node("extend", nodes.extend)
    builder.add_node("finalize", nodes.finalize)

    builder.add_edge(START, "elicit")
    builder.add_edge("elicit", "scope")
    builder.add_edge("scope", "plan_review")

    # Scope confirmation: build it, or loop back to re-plan.
    builder.add_conditional_edges(
        "plan_review", _route, {"generate": "generate", "scope": "scope"}
    )

    builder.add_edge("generate", "review")

    # Review: continue, or enter the diagnose → fix loop.
    builder.add_conditional_edges(
        "review", _route, {"extend": "extend", "diagnose": "diagnose"}
    )

    builder.add_edge("diagnose", "fix")
    builder.add_edge("fix", "review")

    # Extend: add another feature (loop to scope) or finish.
    builder.add_conditional_edges(
        "extend", _route, {"scope": "scope", "finalize": "finalize"}
    )

    builder.add_edge("finalize", END)

    return builder
