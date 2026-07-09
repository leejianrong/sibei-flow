"""LlmProvider interface + neutral message/tool types + provider factory.

The neutral message format mirrors Anthropic content blocks (the reference
provider), so the Claude provider passes them through almost verbatim while
other providers translate:

    message = {"role": "user"|"assistant", "content": [block, ...]}
    block   = {"type": "text", "text": str}
            | {"type": "tool_use", "id": str, "name": str, "input": dict}
            | {"type": "tool_result", "tool_use_id": str, "content": str,
               "is_error": bool}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    """A tool the agent may call (name + description + JSON-schema inputs)."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AssistantTurn:
    """One model turn: free text plus any tool calls it wants executed."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


class LlmProvider(ABC):
    """A single non-streaming completion step. The loop owns the history and
    the tool-dispatch, so a provider only maps one request/response."""

    @abstractmethod
    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
    ) -> AssistantTurn:  # pragma: no cover - interface
        ...


def get_provider(cfg) -> LlmProvider:
    """Construct a *fresh* provider from config.

    A fresh instance per repair is important for the replay provider, which is
    stateful (it consumes scripted turns in order).
    """
    kind = cfg.llm_provider
    if kind == "replay":
        from .replay import ReplayProvider

        return ReplayProvider.from_file(cfg.replay_session)
    if kind == "claude":
        from .claude import ClaudeProvider

        return ClaudeProvider(model=cfg.llm_model)
    if kind == "openai":
        from .openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(model=cfg.llm_model, base_url=cfg.llm_base_url)
    raise ValueError(f"unknown LLM_PROVIDER: {kind!r} (expected replay|claude|openai)")
