"""Shared test setup.

Puts the backend package on sys.path and forces the LLM-backed nodes to use the
deterministic fallbacks, so the whole test suite runs offline (no provider calls).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

import pytest


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    import app.graph.nodes as nodes

    monkeypatch.setattr(nodes, "llm_text", lambda *args, **kwargs: None)
