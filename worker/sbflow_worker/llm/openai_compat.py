"""OpenAI-compatible / local provider (ADR-0007: BYO local model is first-class).

Talks to any OpenAI-compatible chat-completions endpoint (LM Studio, Ollama's
OpenAI shim, vLLM, OpenAI itself) via the `openai` SDK. Translates the neutral
Anthropic-shaped history to/from OpenAI's `tool_calls` / `role:"tool"` format.

Best-effort: this path is not exercised by the V2 tests or the keyless demo
(those use the replay provider), but it is wired behind the same interface so a
local model is a config switch, not a rewrite.
"""

from __future__ import annotations

import json
from typing import Any

from .base import AssistantTurn, LlmProvider, ToolCall, ToolSpec


class OpenAICompatProvider(LlmProvider):
    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        max_tokens: int = 8000,
    ):
        import openai  # lazy import

        # api_key resolved from env (OPENAI_API_KEY); local servers ignore it.
        self.client = openai.OpenAI(base_url=base_url)
        self.model = model
        self.max_tokens = max_tokens

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AssistantTurn:
        oai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            oai_messages.extend(_to_openai(m))

        oai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # The SDK wants precisely-typed message/tool TypedDicts; we build the
            # provider-neutral dicts by hand (ADR-0007). Overload friction only,
            # not a runtime mismatch.
            messages=oai_messages,  # type: ignore[arg-type]
            tools=oai_tools,  # type: ignore[arg-type]
        )
        msg = resp.choices[0].message
        tool_calls = [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                input=json.loads(tc.function.arguments or "{}"),
            )
            for tc in (msg.tool_calls or [])
            if tc.type == "function"
        ]
        return AssistantTurn(
            text=msg.content or "",
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
        )


def _to_openai(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate one neutral message into one or more OpenAI messages."""
    role = message["role"]
    content = message["content"]
    if isinstance(content, str):
        return [{"role": role, "content": content}]

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block["text"])
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block.get("input", {})),
                    },
                }
            )
        elif btype == "tool_result":
            tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": block.get("content", ""),
                }
            )

    out: list[dict[str, Any]] = []
    if role == "assistant":
        assistant: dict[str, Any] = {"role": "assistant"}
        assistant["content"] = "\n".join(text_parts) or None
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        out.append(assistant)
    else:  # user turn: plain text and/or tool results
        if text_parts:
            out.append({"role": "user", "content": "\n".join(text_parts)})
        out.extend(tool_results)
    return out
