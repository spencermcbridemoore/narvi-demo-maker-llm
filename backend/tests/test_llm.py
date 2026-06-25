"""Unit tests for the LLM helpers and deterministic fallbacks."""

from __future__ import annotations

import pytest

from app.graph.nodes import _decision
from app.graph.templates import fallback_fix
from app.llm import factory
from app.llm.complete import extract_html, usable_html


def test_extract_html_strips_fence_with_prose_prefix():
    out = extract_html("Here you go:\n```html\n<!doctype html><html></html>\n```")
    assert "```" not in out
    assert out.lower().startswith("<!doctype")


def test_extract_html_raw_html_after_prose():
    out = extract_html("Sure!\n<!doctype html><body>x</body>")
    assert out.lower().startswith("<!doctype")


def test_extract_html_empty_fence_is_unusable():
    assert usable_html(extract_html("```html\n```")) is False


def test_usable_html():
    assert usable_html("<div>hi</div>") is True
    assert usable_html("") is False
    assert usable_html(None) is False
    assert usable_html("just text") is False


def test_fallback_fix_does_not_stack_banners():
    html = "<!doctype html><html><body>x</body></html>"
    for i in range(3):
        html = fallback_fix(html, f"change {i}")
    assert html.count("data-fixbanner") == 1
    assert "change 2" in html  # the latest change is the one kept


def test_decision_button_vs_freetext():
    assert _decision("approve", {"approve", "revise"}, "revise") == ("approve", "")
    assert _decision("make it blue", {"approve", "revise"}, "revise") == ("revise", "make it blue")
    assert _decision("done", {"done"}, "add_more") == ("done", "")
    assert _decision("add a reset button", {"done"}, "add_more") == ("add_more", "add a reset button")


def test_env_resolution(monkeypatch):
    monkeypatch.setenv("DEMOBUILDER_TEST_DEPLOY", "my-deploy")
    assert factory._resolve_env("env:DEMOBUILDER_TEST_DEPLOY") == "my-deploy"
    assert factory._resolve_env("a-literal-value") == "a-literal-value"


def test_env_resolution_missing_raises(monkeypatch):
    monkeypatch.delenv("DEMOBUILDER_TEST_MISSING", raising=False)
    with pytest.raises(ValueError):
        factory._resolve_env("env:DEMOBUILDER_TEST_MISSING")
