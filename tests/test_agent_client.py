import json
from unittest.mock import MagicMock, patch

import pytest

from loophedge.agents.client import AgentClient, ToolSpec


def _fake_response(text=None, tool_use=None, stop_reason="end_turn"):
    blocks = []
    if text:
        blocks.append(MagicMock(type="text", text=text))
    if tool_use:
        b = MagicMock(type="tool_use")
        b.id = tool_use["id"]
        b.name = tool_use["name"]
        b.input = tool_use["input"]
        blocks.append(b)
    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = stop_reason
    return resp


def test_text_only_response_returns_text():
    with patch("loophedge.agents.client.anthropic.Anthropic") as M:
        M.return_value.messages.create.return_value = _fake_response(text="hi")
        c = AgentClient(model="claude-sonnet-4-6", system_prompt="sys", tools=[])
        assert c.run([{"role": "user", "content": "hello"}]) == "hi"


def test_tool_use_dispatched_then_loop_continues():
    calls = []
    def tool_fn(symbol: str):
        calls.append(symbol)
        return {"price": 60000}

    spec = ToolSpec(name="get_price",
                     description="get",
                     input_schema={"type": "object", "properties": {"symbol": {"type": "string"}}},
                     function=tool_fn)

    with patch("loophedge.agents.client.anthropic.Anthropic") as M:
        first = _fake_response(tool_use={"id": "tu1", "name": "get_price",
                                          "input": {"symbol": "BTC"}},
                                stop_reason="tool_use")
        second = _fake_response(text="price 60000", stop_reason="end_turn")
        M.return_value.messages.create.side_effect = [first, second]
        c = AgentClient(model="claude-opus-4-7", system_prompt="sys", tools=[spec])
        out = c.run([{"role": "user", "content": "what's BTC?"}])
        assert "60000" in out
        assert calls == ["BTC"]


def test_max_turns_raises():
    with patch("loophedge.agents.client.anthropic.Anthropic") as M:
        # always returns tool_use with no end_turn
        M.return_value.messages.create.return_value = _fake_response(
            tool_use={"id": "x", "name": "n", "input": {}}, stop_reason="tool_use"
        )
        spec = ToolSpec(name="n", description="",
                         input_schema={"type": "object"}, function=lambda: {})
        c = AgentClient(model="claude-sonnet-4-6", system_prompt="sys", tools=[spec])
        with pytest.raises(RuntimeError, match="max_turns"):
            c.run([{"role": "user", "content": "loop"}], max_turns=3)
