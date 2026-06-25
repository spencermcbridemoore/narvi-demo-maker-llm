"""Role → model factory.

Profiles are named by PROVIDER (azure / ollama / openai), not by environment.
``LLM_PROFILE`` selects which provider is active — provider and environment are
orthogonal axes, and where inference runs only affects cost/latency/quality/
secrets, never the workflow's behavior or its output. Each workflow role
(routing/scope = cheap, generation/fix = strong, review/diagnose = mid) maps to a
tier, and each profile maps tiers to concrete model ids. Every provider returns a
``BaseChatModel`` so graph nodes stay provider-agnostic.

Config values of the form ``env:NAME`` are resolved from environment variable
NAME at runtime, so secrets-adjacent values (e.g. Azure deployment names) live in
``.env`` rather than in ``models.yaml``. Importing this module is side-effect
free — models are only constructed when ``get_model`` is called.
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache

import yaml
from langchain_core.language_models.chat_models import BaseChatModel

from ..config import LLM_PROFILE, MODELS_CONFIG


class Role(str, Enum):
    ROUTING = "routing"        # cheap, high-volume
    SCOPE = "scope"            # cheap
    GENERATION = "generation"  # strong — code quality matters most
    FIX = "fix"                # strong
    REVIEW = "review"          # mid
    DIAGNOSE = "diagnose"      # mid


@lru_cache(maxsize=1)
def _load_config() -> dict:
    with open(MODELS_CONFIG, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _resolve_env(value):
    """Expand an ``env:NAME`` reference to the value of environment variable NAME."""
    if isinstance(value, str) and value.startswith("env:"):
        name = value[4:]
        resolved = os.getenv(name)
        if not resolved:
            raise ValueError(f"environment variable {name!r} is required but not set")
        return resolved
    return value


def _resolve_model_id(cfg: dict, profile: str, role: Role) -> tuple[str, dict]:
    block = cfg["profiles"][profile]
    tier = cfg["roles"][role.value]
    return _resolve_env(block["tiers"][tier]), block


@lru_cache(maxsize=None)
def get_model(role: Role, profile: str | None = None) -> BaseChatModel:
    """Return the chat model for ``role`` under the active (or given) profile.

    Cached per ``(role, profile)`` so each model is constructed once. Provider
    SDKs are imported lazily so a missing provider dependency never breaks import.
    """
    profile = (profile or LLM_PROFILE).lower()
    cfg = _load_config()
    model_id, block = _resolve_model_id(cfg, profile, role)
    provider = block["provider"]
    # temperature and reasoning_effort are opt-in per profile, sent ONLY when set:
    #  - GPT-5 / reasoning deployments reject a non-default temperature (400).
    #  - reasoning_effort (minimal|low|medium|high) tunes gpt-5/o-series latency vs
    #    depth; it's invalid on non-reasoning models, so set it only on profiles
    #    whose deployments are reasoning models.
    common: dict = {"timeout": block.get("timeout", 120)}
    if "temperature" in block:
        common["temperature"] = block["temperature"]
    if "reasoning_effort" in block:
        common["reasoning_effort"] = block["reasoning_effort"]

    if provider == "azure":
        from langchain_openai import AzureChatOpenAI

        # azure_endpoint / api_key / api_version fall back to env vars
        # (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, OPENAI_API_VERSION).
        return AzureChatOpenAI(azure_deployment=model_id, **common)

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_id,
            base_url=block.get("base_url", "http://localhost:11434"),
            **common,
        )

    if provider == "openai_compatible":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_id,
            base_url=block["base_url"],
            api_key=_resolve_env(block.get("api_key", "not-needed")),
            **common,
        )

    raise ValueError(f"Unknown LLM provider {provider!r} for profile {profile!r}")
