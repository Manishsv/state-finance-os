"""Tests for financeos.os.llm.client — message helpers and Anthropic translators.

Provider SDKs are not invoked. We test the format translation logic
directly, since that's what makes provider-swapping safe.
"""
from __future__ import annotations

import json

from financeos.os.llm import (
    ToolCall,
    assistant_msg,
    make_parameters,
    make_tool,
    system_msg,
    tool_result_msg,
    user_msg,
)
from financeos.os.llm.client import (
    _oai_messages_to_anthropic,
    _oai_tool_choice_to_anthropic,
    _oai_tools_to_anthropic,
)


# -- message helpers --

def test_user_msg_shape():
    assert user_msg("hi") == {"role": "user", "content": "hi"}


def test_system_msg_shape():
    assert system_msg("be terse") == {"role": "system", "content": "be terse"}


def test_assistant_msg_with_tool_calls_serializes_arguments():
    tc = ToolCall(id="call_1", name="lookup", arguments={"q": "karnataka"})
    msg = assistant_msg(content="ok", tool_calls=[tc])
    assert msg["role"] == "assistant"
    assert msg["content"] == "ok"
    assert msg["tool_calls"][0]["id"] == "call_1"
    assert msg["tool_calls"][0]["function"]["name"] == "lookup"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"q": "karnataka"}


def test_tool_result_msg_jsonifies_dict():
    msg = tool_result_msg("call_1", {"answer": 42})
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "call_1"
    assert json.loads(msg["content"]) == {"answer": 42}


def test_tool_result_msg_passes_through_string():
    msg = tool_result_msg("call_1", "raw text")
    assert msg["content"] == "raw text"


# -- tool helpers --

def test_make_tool_produces_openai_format():
    t = make_tool("lookup", "Look up a state",
                  make_parameters({"state": {"type": "string"}}, required=["state"]))
    assert t["type"] == "function"
    assert t["function"]["name"] == "lookup"
    assert t["function"]["parameters"]["required"] == ["state"]


# -- Anthropic translators --

def test_oai_tools_to_anthropic_unwraps_function():
    tools = [make_tool("foo", "desc", make_parameters({"x": {"type": "integer"}}))]
    out = _oai_tools_to_anthropic(tools)
    assert out == [{
        "name": "foo",
        "description": "desc",
        "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": []},
    }]


def test_oai_tool_choice_translations():
    assert _oai_tool_choice_to_anthropic("auto") == {"type": "auto"}
    assert _oai_tool_choice_to_anthropic("required") == {"type": "any"}
    assert _oai_tool_choice_to_anthropic("any") == {"type": "any"}
    assert _oai_tool_choice_to_anthropic(None) == {"type": "auto"}
    assert _oai_tool_choice_to_anthropic(
        {"type": "function", "function": {"name": "foo"}}
    ) == {"type": "tool", "name": "foo"}


def test_oai_messages_to_anthropic_extracts_system():
    msgs = [system_msg("you are X"), user_msg("hi")]
    sys_text, ant_msgs = _oai_messages_to_anthropic(msgs)
    assert sys_text == "you are X"
    assert ant_msgs == [{"role": "user", "content": "hi"}]


def test_oai_messages_to_anthropic_concatenates_multiple_systems():
    msgs = [system_msg("a"), system_msg("b"), user_msg("hi")]
    sys_text, _ = _oai_messages_to_anthropic(msgs)
    assert "a" in sys_text and "b" in sys_text


def test_oai_messages_to_anthropic_merges_consecutive_tool_results():
    """Two tool results in a row should be merged into ONE user message
    with multiple tool_result blocks (Anthropic API requirement)."""
    msgs = [
        user_msg("hi"),
        assistant_msg(tool_calls=[ToolCall(id="c1", name="t1", arguments={})]),
        tool_result_msg("c1", "result1"),
        tool_result_msg("c2", "result2"),
        user_msg("thanks"),
    ]
    _, ant = _oai_messages_to_anthropic(msgs)
    # Find the merged tool-result user message
    tool_result_msg_blocks = [m for m in ant
                              if m["role"] == "user"
                              and isinstance(m["content"], list)
                              and any(b.get("type") == "tool_result" for b in m["content"])]
    assert len(tool_result_msg_blocks) == 1
    blocks = tool_result_msg_blocks[0]["content"]
    assert len(blocks) == 2
    assert {b["tool_use_id"] for b in blocks} == {"c1", "c2"}


def test_oai_messages_to_anthropic_converts_tool_calls_to_tool_use_blocks():
    msgs = [
        user_msg("hi"),
        assistant_msg(content="thinking",
                      tool_calls=[ToolCall(id="c1", name="search", arguments={"q": "x"})]),
    ]
    _, ant = _oai_messages_to_anthropic(msgs)
    asst = ant[1]
    assert asst["role"] == "assistant"
    blocks = asst["content"]
    assert any(b["type"] == "text" and b["text"] == "thinking" for b in blocks)
    assert any(b["type"] == "tool_use" and b["name"] == "search"
               and b["input"] == {"q": "x"} for b in blocks)
