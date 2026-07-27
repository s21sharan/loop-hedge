import sys
from dataclasses import dataclass
from typing import Any, Callable

import anthropic


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    function: Callable[..., dict]


class AgentClient:
    def __init__(self, model: str, system_prompt: str, tools: list[ToolSpec],
                  effort: str | None = None):
        self.model = model
        self.system_prompt = system_prompt
        self.tools = {t.name: t for t in tools}
        self.effort = effort
        self._client = anthropic.Anthropic()

    def run(self, messages: list[dict], max_turns: int = 10) -> str:
        msgs = list(messages)
        tool_schemas = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in self.tools.values()
        ]
        for turn in range(max_turns):
            # Place a cache_control breakpoint on the last content block of the last
            # message. On turn 0 this writes the prefix; turn 1+ read it at ~10% cost.
            cached_msgs = _with_cache_marker(msgs)
            kwargs: dict[str, Any] = {"model": self.model, "system": self.system_prompt,
                       "messages": cached_msgs, "max_tokens": 4096}
            if tool_schemas:
                kwargs["tools"] = tool_schemas
            if self.effort:
                kwargs["output_config"] = {"effort": self.effort}
            resp = self._client.messages.create(**kwargs)  # type: ignore[call-overload]
            _log_usage(self.model, turn, resp)

            assistant_content = []
            tool_results = []
            for block in resp.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({"type": "tool_use", "id": block.id,
                                                "name": block.name, "input": block.input})
                    spec = self.tools.get(block.name)
                    if spec is None:
                        result = {"error": f"unknown tool {block.name}"}
                    else:
                        try:
                            result = spec.function(**block.input)
                        except Exception as e:
                            result = {"error": str(e)}
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                          "content": _to_text(result)})

            msgs.append({"role": "assistant", "content": assistant_content})

            if resp.stop_reason == "end_turn" or not tool_results:
                return "".join(b["text"] for b in assistant_content if b["type"] == "text")

            msgs.append({"role": "user", "content": tool_results})
        raise RuntimeError(f"agent exceeded max_turns={max_turns}")


def _with_cache_marker(messages: list[dict]) -> list[dict]:
    """Return a shallow-copied message list with cache_control on the final block."""
    if not messages:
        return messages
    out = list(messages)
    last = dict(out[-1])
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = [{
            "type": "text", "text": content,
            "cache_control": {"type": "ephemeral"},
        }]
    elif isinstance(content, list) and content:
        blocks = [dict(b) if isinstance(b, dict) else b for b in content]
        if isinstance(blocks[-1], dict):
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        last["content"] = blocks
    out[-1] = last
    return out


def _log_usage(model: str, turn: int, resp: Any) -> None:
    """Print per-turn token accounting so cache effectiveness is measurable."""
    u = getattr(resp, "usage", None)
    if u is None:
        return
    fields = ("input_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens", "output_tokens")
    parts = [f"{name.replace('_tokens', '').replace('_input', '')}={getattr(u, name, 0)}"
             for name in fields]
    print(f"[agent-usage] model={model} turn={turn} " + " ".join(parts),
          file=sys.stderr, flush=True)


def _to_text(payload: dict) -> str:
    import json
    return json.dumps(payload, default=str)
