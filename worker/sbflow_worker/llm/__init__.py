"""Provider-agnostic LLM interface (ADR-0007).

The repair loop talks only to :class:`LlmProvider`; concrete providers (Claude,
OpenAI-compatible/local, record-replay) are selected by config. This is the
injected test seam for provider-agnosticism (PRD Testing Decisions).
"""

from .base import AssistantTurn, LlmProvider, ToolCall, ToolSpec, get_provider

__all__ = ["AssistantTurn", "LlmProvider", "ToolCall", "ToolSpec", "get_provider"]
