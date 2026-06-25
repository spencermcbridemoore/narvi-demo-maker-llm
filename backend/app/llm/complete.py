"""Thin LLM-call helper used by the graph nodes.

`llm_text` routes a (system, user) prompt through the config-driven model factory
and returns the text — or ``None`` if no provider is reachable (e.g. Ollama not
running, Azure not configured). Nodes treat ``None`` as "use the deterministic
fallback", so the whole 7-stage loop runs end-to-end with or without a model.
Real-provider verification is Stage 3.
"""

from __future__ import annotations

import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from .factory import Role, get_model

logger = logging.getLogger("demobuilder.llm")

# A configuration/programming mistake (bad models.yaml, unknown provider, missing
# file) should be loud, not silently masked as "provider unavailable".
_CONFIG_ERRORS = (KeyError, FileNotFoundError, ValueError)

_FENCE_RE = re.compile(r"```[a-zA-Z0-9]*\s*\n(.*?)```", re.DOTALL)


def llm_text(role: Role, system: str, user: str) -> str | None:
    """Return the model's text for this role, or None if unavailable."""
    try:
        model = get_model(role)
        resp = model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        content = resp.content
        text = content if isinstance(content, str) else str(content)
        return text.strip() or None
    except _CONFIG_ERRORS as exc:
        # Surfaced at ERROR so a broken provider config isn't hidden behind a warning.
        logger.error("LLM role=%s misconfigured (%s); using fallback", role.value, exc)
        return None
    except Exception as exc:  # provider unreachable / transient
        logger.warning("LLM role=%s unavailable (%s); using fallback", role.value, exc)
        return None


def extract_html(text: str) -> str:
    """Pull a clean HTML document out of a model response.

    Handles the common shapes: a fenced ```html block (with optional prose
    before/after), or raw HTML preceded by a sentence. Returns "" if nothing
    HTML-looking is found — callers fall back to a template in that case.
    """
    t = text.strip()

    fence = _FENCE_RE.search(t)
    if fence:
        t = fence.group(1).strip()
    else:
        # No paired fence; drop any stray ``` markers (e.g. an unclosed fence).
        t = t.replace("```", "").strip()

    lowered = t.lower()
    for marker in ("<!doctype", "<html"):
        idx = lowered.find(marker)
        if idx != -1:
            return t[idx:].strip()
    return t


def usable_html(html: str | None) -> bool:
    """True if `html` looks like a real artifact (non-empty, contains a tag)."""
    return bool(html and "<" in html)
