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
    def __init__(self, model: str, system_prompt: str, tools: list[ToolSpec]):
        self.model = model
        self.system_prompt = system_prompt
        self.tools = {t.name: t for t in tools}
        self._client = anthropic.Anthropic()

    def run(self, messages: list[dict], max_turns: int = 10) -> str:
        msgs = list(messages)
        tool_schemas = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in self.tools.values()
        ]
        for _ in range(max_turns):
            kwargs = {"model": self.model, "system": self.system_prompt,
                       "messages": msgs, "max_tokens": 4096}
            if tool_schemas:
                kwargs["tools"] = tool_schemas
            resp = self._client.messages.create(**kwargs)

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


def _to_text(payload: dict) -> str:
    import json
    return json.dumps(payload, default=str)
