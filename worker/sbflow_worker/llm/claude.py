"""Claude provider (Anthropic Python SDK) — the recommended default (ADR-0007).

BYO-key: the SDK resolves credentials from the environment (`ANTHROPIC_API_KEY`
or an `ant auth login` profile); no key is hardcoded. Thinking is disabled here
so the provider-neutral loop can pass its own message history verbatim without
having to round-trip Claude-specific thinking blocks — adequate for a bounded,
targeted-edit repair task.
"""

from __future__ import annotations

from typing import Any

from .base import AssistantTurn, LlmProvider, ToolCall, ToolSpec


class ClaudeProvider(LlmProvider):
    def __init__(self, model: str = "claude-opus-4-8", max_tokens: int = 8000):
        import anthropic  # imported lazily so `replay` needs no SDK installed

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AssistantTurn:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            thinking={"type": "disabled"},
            tools=[
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ],
            messages=messages,
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input))
            for b in resp.content
            if b.type == "tool_use"
        ]
        return AssistantTurn(
            text=text, tool_calls=tool_calls, stop_reason=resp.stop_reason or "end_turn"
        )
