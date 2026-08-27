from __future__ import annotations

import json
from unittest import TestCase

import httpx
from openai import OpenAI
from openai.types.chat.chat_completion_message import ChatCompletionMessage


class OpenAISdkContractTests(TestCase):
    def test_sdk_serializes_kimi_reasoning_history_unchanged(self) -> None:
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "kimi-k3",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "ok"},
                        }
                    ],
                },
            )

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as http_client:
            client = OpenAI(
                api_key="test-key",
                base_url="https://moonshot.invalid/v1",
                http_client=http_client,
            )
            assistant = ChatCompletionMessage.model_validate(
                {
                    "role": "assistant",
                    "content": "collected",
                    "reasoning_content": "private-kimi-state",
                }
            )
            client.chat.completions.create(
                model="kimi-k3",
                reasoning_effort="high",
                messages=[assistant],
            )

        message = captured[0]["messages"][0]
        self.assertEqual("private-kimi-state", message["reasoning_content"])
        self.assertEqual("high", captured[0]["reasoning_effort"])
        self.assertNotIn("temperature", captured[0])
        self.assertNotIn("top_p", captured[0])
