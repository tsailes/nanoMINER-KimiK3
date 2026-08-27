from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest import TestCase

from nanominer_k3.agent import AgentProtocolError, KimiK3ToolAgent, ToolSpec
from nanominer_k3.config import KimiSettings


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


class FakeMessage:
    def __init__(self, *, content=None, tool_calls=None, reasoning_content=None):
        self.role = "assistant"
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content


def response(message: FakeMessage, finish_reason: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class KimiK3ToolAgentTests(TestCase):
    def test_preserves_full_assistant_object_and_uses_two_stage_json(self) -> None:
        first_message = FakeMessage(
            tool_calls=[
                FakeToolCall("call-1", FakeFunction("lookup", '{"page": 2}'))
            ],
            reasoning_content="private reasoning that must remain in history",
        )
        collector_stop = FakeMessage(content="Evidence collected")
        final_payload = {
            "profile": "test",
            "summary": "done",
            "documents": [],
            "records": [],
            "unresolved": [],
        }
        final_message = FakeMessage(content=json.dumps(final_payload))
        completions = FakeCompletions(
            [
                response(first_message, "tool_calls"),
                response(collector_stop, "stop"),
                response(final_message, "stop"),
            ]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        agent = KimiK3ToolAgent(client, KimiSettings(api_key=None))
        tool = ToolSpec(
            name="lookup",
            description="lookup a page",
            parameters={"type": "object"},
            handler=lambda args: {"page": args["page"], "text": "evidence"},
        )

        result = agent.run(
            system_prompt="system",
            user_prompt="user",
            tools=[tool],
            response_format={"type": "json_schema"},
        )

        self.assertEqual(json.dumps(final_payload), result.content)
        self.assertIs(first_message, completions.calls[1]["messages"][2])
        self.assertEqual("required", completions.calls[0]["tool_choice"])
        self.assertEqual("auto", completions.calls[1]["tool_choice"])
        self.assertEqual("none", completions.calls[2]["tool_choice"])
        self.assertNotIn("response_format", completions.calls[0])
        self.assertIn("response_format", completions.calls[2])
        for call in completions.calls:
            self.assertNotIn("temperature", call)
            self.assertNotIn("top_p", call)
            self.assertEqual("high", call["reasoning_effort"])
        tool_message = completions.calls[1]["messages"][3]
        self.assertEqual("call-1", tool_message["tool_call_id"])
        self.assertTrue(json.loads(tool_message["content"])["ok"])

    def test_tool_error_still_returns_matching_tool_result(self) -> None:
        first = FakeMessage(
            tool_calls=[FakeToolCall("bad-1", FakeFunction("missing", "{}"))]
        )
        stop = FakeMessage(content="stopped")
        completions = FakeCompletions(
            [response(first, "tool_calls"), response(stop, "stop")]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        agent = KimiK3ToolAgent(client, KimiSettings(api_key=None))
        result = agent.run(
            system_prompt="s", user_prompt="u", tools=[], response_format=None
        )
        self.assertEqual("stopped", result.content)
        error_message = completions.calls[1]["messages"][3]
        self.assertEqual("bad-1", error_message["tool_call_id"])
        self.assertFalse(json.loads(error_message["content"])["ok"])

    def test_length_finish_is_rejected(self) -> None:
        completions = FakeCompletions(
            [response(FakeMessage(content="partial"), "length")]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        agent = KimiK3ToolAgent(client, KimiSettings(api_key=None))
        with self.assertRaisesRegex(AgentProtocolError, "truncated"):
            agent.run(system_prompt="s", user_prompt="u", tools=[])

    def test_required_evidence_tool_must_be_honored(self) -> None:
        completions = FakeCompletions(
            [response(FakeMessage(content="I skipped tools"), "stop")]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        agent = KimiK3ToolAgent(client, KimiSettings(api_key=None))
        tool = ToolSpec(
            name="lookup",
            description="lookup",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            handler=lambda _: {},
        )
        with self.assertRaisesRegex(AgentProtocolError, "required tool"):
            agent.run(system_prompt="s", user_prompt="u", tools=[tool])

    def test_progress_events_do_not_expose_prompts_reasoning_or_queries(self) -> None:
        first_message = FakeMessage(
            tool_calls=[
                FakeToolCall(
                    "call-1",
                    FakeFunction(
                        "lookup",
                        '{"page": 2, "query": "sensitive search terms"}',
                    ),
                )
            ],
            reasoning_content="private chain of thought",
        )
        stop = FakeMessage(content="done")
        completions = FakeCompletions(
            [response(first_message, "tool_calls"), response(stop, "stop")]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        events = []
        agent = KimiK3ToolAgent(
            client,
            KimiSettings(api_key=None),
            progress_handler=events.append,
        )
        tool = ToolSpec(
            name="lookup",
            description="lookup",
            parameters={
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "query": {"type": "string"},
                },
                "required": ["page", "query"],
                "additionalProperties": False,
            },
            handler=lambda _: {"secret_result": "not emitted"},
        )

        agent.run(system_prompt="secret system", user_prompt="secret user", tools=[tool])

        rendered = json.dumps(events)
        self.assertIn('"query_chars": 22', rendered)
        self.assertNotIn("sensitive search terms", rendered)
        self.assertNotIn("private chain of thought", rendered)
        self.assertNotIn("secret_result", rendered)
