"""Record/replay provider — the deterministic, keyless test seam (PRD).

Replays a scripted list of assistant turns regardless of the conversation, so
the loop's tool execution (real `read_file`/`get_schema`/`edit_file`) and the
diff it produces are exercised deterministically without an LLM key. The tool
*results* are still produced by really running the tools; only the model's
decisions are canned.

Session file shape::

    {"turns": [
        {"text": "...", "tool_calls": [{"name": "read_file", "input": {...}}]},
        {"text": "final explanation"}
    ]}

A scripted turn may also carry a "usage" dict (KAN-651) — e.g.
``{"model": "test-model", "input_tokens": 10, "output_tokens": 4}`` — which
passes straight through to `AssistantTurn.usage`, purely so tests can exercise
the usage-recording path deterministically without a live provider. No real
recorded session needs this key; the deterministic demo/test scripts genuinely
have no token cost, so a session without it (the common case) reports `None`,
same as before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import AssistantTurn, LlmProvider, ToolCall, ToolSpec


class ReplayProvider(LlmProvider):
    def __init__(self, turns: list[dict[str, Any]]):
        self._turns = turns
        self._i = 0

    @classmethod
    def from_file(cls, path: str) -> "ReplayProvider":
        data = json.loads(Path(path).read_text())
        return cls(data.get("turns", []))

    def complete(self, system, messages, tools: list[ToolSpec]) -> AssistantTurn:
        if self._i >= len(self._turns):
            # Script exhausted → a final, tool-less turn ends the loop.
            return AssistantTurn(text="", tool_calls=[], stop_reason="end_turn")
        turn = self._turns[self._i]
        self._i += 1
        calls = [
            ToolCall(id=f"call_{self._i}_{j}", name=c["name"], input=c.get("input", {}))
            for j, c in enumerate(turn.get("tool_calls", []))
        ]
        return AssistantTurn(
            text=turn.get("text", ""),
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            usage=turn.get("usage"),
        )
