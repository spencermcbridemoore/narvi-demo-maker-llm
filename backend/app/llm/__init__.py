"""Config-driven LLM access (role → model), by provider: azure / ollama / openai."""

from .factory import Role, get_model

__all__ = ["Role", "get_model"]
