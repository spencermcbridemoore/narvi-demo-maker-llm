"""Shared workflow state.

The single source of truth that flows through every node and is streamed to the
frontend (AG-UI ``STATE_SNAPSHOT`` / ``STATE_DELTA``). Every field is
JSON-serializable — it is persisted in the checkpoint and sent over SSE.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class DemoState(TypedDict, total=False):
    # What the user wants to build (grows as they revise / extend).
    spec: str

    # Scope's proposed plan (shown to the user for confirmation).
    plan: str

    # The current self-contained HTML artifact (the live preview renders this).
    html: str

    # Running change/issue log — appended (operator.add reducer) across
    # Diagnose → Fix and whenever the user reports something to change.
    issues: Annotated[list[str], operator.add]

    # The node the workflow is at / heading to. Drives the flowchart "you are
    # here" highlight AND the conditional-edge routing (each node sets it to its
    # chosen successor).
    stage: str

    # Set by Finalize once the user is done — the frontend offers the download.
    done: bool

    # True when the latest Generate/Fix produced a DETERMINISTIC PLACEHOLDER
    # because no LLM provider was reachable. Surfaced in the UI so a misconfigured
    # provider isn't mistaken for a real (but bland) result.
    used_fallback: bool
