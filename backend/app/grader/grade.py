"""One LLM call: grade one response against one rubric item.

Prompts are data (``prompts/*.txt``); ``PROMPT_VERSION`` hashes them together with
the output schema, so editing either invalidates the sample cache automatically.
Unlike DemoBuilder's ``llm_text``, failures are never papered over with a
fallback: a failed call becomes a Sample with ``error`` set and no verdict.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..llm.factory import Role, get_model
from .model import Criterion, Problem, Response, Sample

# include_raw=True makes LangChain serialize a dict holding a pydantic object, which
# emits a harmless serializer warning on every call.
warnings.filterwarnings("ignore", message="Pydantic serializer warnings", category=UserWarning)

_PROMPTS = Path(__file__).parent / "prompts"
SYSTEM_TEMPLATE = (_PROMPTS / "grade_system.txt").read_text(encoding="utf-8").strip()
USER_TEMPLATE = (_PROMPTS / "grade_user.txt").read_text(encoding="utf-8").strip()

EFFORTS = ("minimal", "low", "medium", "high")


class ItemVerdict(BaseModel):
    """What the model must return. Field order is deliberate: reason, quote, then decide."""

    comparison: str = Field(
        description="Compare the student response with the rubric item in 1-4 sentences before deciding."
    )
    evidence: list[str] = Field(
        description="Exact quotes copied verbatim from the student response that the decision "
        "relies on. Empty list if nothing in the response is relevant."
    )
    verdict: Literal["credit", "no_credit"] = Field(
        description="credit if the response satisfies the rubric item, otherwise no_credit."
    )


PROMPT_VERSION = hashlib.sha1(
    (SYSTEM_TEMPLATE + USER_TEMPLATE
     + json.dumps(ItemVerdict.model_json_schema(), sort_keys=True)).encode()
).hexdigest()[:10]

_SLOT = re.compile(r"\{\{(\w+)\}\}")


def _fill(template: str, **values: str) -> str:
    # Single pass over the template, so text inside a value is never re-expanded.
    return _SLOT.sub(lambda m: values[m.group(1)], template)


def build_messages(problem: Problem, criterion: Criterion, response: Response) -> list[tuple[str, str]]:
    instructions_block = (
        f"\nWHAT STUDENTS WERE ASKED TO WRITE\n<instructions>\n{problem.instructions.strip()}\n</instructions>\n"
        if problem.instructions.strip() else ""
    )
    user = _fill(
        USER_TEMPLATE,
        problem=problem.text.strip() or "(no problem text provided)",
        instructions_block=instructions_block,
        response=response.text.strip() or "(empty response)",
        label=criterion.label,
        criterion=criterion.text.strip(),
    )
    return [("system", SYSTEM_TEMPLATE), ("human", user)]


@lru_cache(maxsize=8)
def grading_model(effort: str | None = None):
    """The grade-role model, with reasoning effort overridden when the profile uses it."""
    base = get_model(Role.GRADE)
    current = getattr(base, "reasoning_effort", None)
    if effort and current is not None and effort != current:
        return base.model_copy(update={"reasoning_effort": effort})
    return base


def model_name() -> str:
    m = get_model(Role.GRADE)
    return (getattr(m, "deployment_name", None) or getattr(m, "model_name", None)
            or getattr(m, "model", None) or type(m).__name__)


_RETRYABLE = {"RateLimitError", "APITimeoutError", "APIConnectionError", "InternalServerError",
              "ConnectError", "ReadTimeout", "ConnectTimeout"}


def _usage(raw) -> dict[str, int]:
    meta = getattr(raw, "usage_metadata", None) or {}
    out_d = meta.get("output_token_details") or {}
    in_d = meta.get("input_token_details") or {}
    return {
        "input_tokens": int(meta.get("input_tokens") or 0),
        "output_tokens": int(meta.get("output_tokens") or 0),
        "reasoning_tokens": int(out_d.get("reasoning") or 0),
        "cached_tokens": int(in_d.get("cache_read") or 0),
    }


async def grade_once(messages: list[tuple[str, str]], effort: str | None,
                     attempts: int = 8) -> tuple[Sample, dict[str, int]]:
    """One structured call with backoff on rate limits/timeouts. Never raises.

    The backoff (2, 4, 8 ... capped at 60 s) rides out the shared Azure TPM quota:
    a burst of 429s slows the run down instead of losing votes.
    """
    runnable = grading_model(effort).with_structured_output(
        ItemVerdict, method="json_schema", include_raw=True
    )
    last = "unknown error"
    usage: dict[str, int] = {}
    for attempt in range(attempts):
        try:
            out = await runnable.ainvoke(messages)
        except Exception as exc:  # provider errors vary; classify by name
            last = f"{type(exc).__name__}: {exc}"
            if type(exc).__name__ in _RETRYABLE and attempt < attempts - 1:
                await asyncio.sleep(min(60.0, 2 ** (attempt + 1) + random.random()))
                continue
            return Sample(error=last[:300]), usage
        usage = _usage(out.get("raw"))
        parsed = out.get("parsed")
        if parsed is None:
            last = f"unparseable output: {out.get('parsing_error')}"
            continue
        return Sample(
            verdict=1 if parsed.verdict == "credit" else 0,
            comparison=parsed.comparison.strip(),
            evidence=[q.strip() for q in parsed.evidence if q and q.strip()],
        ), usage
    return Sample(error=last[:300]), usage
