from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

from .config import KimiSettings


class AgentProtocolError(RuntimeError):
    """Raised when the model or tool loop violates the expected protocol."""


ToolHandler = Callable[[dict[str, Any]], Any]
ProgressHandler = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler = field(repr=False)

    def api_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(slots=True)
class AgentResult:
    content: str
    turns: int
    tool_calls: int
    messages: list[Any]


class KimiK3ToolAgent:
    """Native Kimi K3 tool loop.

    The returned assistant object is appended directly to the next request. This is
    intentional: Kimi K3 requires the complete assistant message, including its
    reasoning and tool-call fields, to be returned unchanged on later turns.
    """

    def __init__(
        self,
        client: Any,
        settings: KimiSettings,
        *,
        progress_handler: ProgressHandler | None = None,
    ):
        settings.validate(require_api_key=False)
        self._client = client
        self._settings = settings
        self._progress_handler = progress_handler

    def run(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: Sequence[ToolSpec],
        response_format: Mapping[str, Any] | None = None,
    ) -> AgentResult:
        tool_by_name = {tool.name: tool for tool in tools}
        if len(tool_by_name) != len(tools):
            raise ValueError("Tool names must be unique")

        messages: list[Any] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        total_tool_calls = 0

        for turn in range(1, self._settings.max_agent_turns + 1):
            self._emit(
                "model_request_started",
                phase="evidence_collection",
                turn=turn,
            )
            request: dict[str, Any] = {
                "model": self._settings.model,
                "reasoning_effort": self._settings.reasoning_effort,
                "messages": messages,
                "max_completion_tokens": self._settings.max_completion_tokens,
            }
            if tools:
                request["tools"] = [tool.api_definition() for tool in tools]
                request["tool_choice"] = "required" if turn == 1 else "auto"

            response = self._client.chat.completions.create(**request)
            if not getattr(response, "choices", None):
                raise AgentProtocolError("Kimi K3 returned no choices")

            choice = response.choices[0]
            assistant_message = choice.message
            messages.append(assistant_message)
            calls = list(getattr(assistant_message, "tool_calls", None) or [])
            finish_reason = getattr(choice, "finish_reason", "unknown")
            self._emit(
                "model_response_received",
                phase="evidence_collection",
                turn=turn,
                finish_reason=finish_reason,
                tool_call_count=len(calls),
            )

            if not calls:
                if turn == 1 and tools:
                    raise AgentProtocolError(
                        "Kimi K3 did not honor required tool use on the evidence turn"
                    )
                if finish_reason == "length":
                    raise AgentProtocolError(
                        "Kimi K3 response was truncated before completion"
                    )
                if finish_reason != "stop":
                    raise AgentProtocolError(
                        f"Unexpected finish_reason without tool calls: {finish_reason}"
                    )
                if response_format is not None:
                    return self._finalize(
                        messages=messages,
                        tools=tools,
                        response_format=response_format,
                        turn=turn,
                        total_tool_calls=total_tool_calls,
                    )
                content = getattr(assistant_message, "content", None)
                if not content:
                    raise AgentProtocolError(
                        "Kimi K3 returned neither tool calls nor final content "
                        f"(finish_reason={finish_reason})"
                    )
                return AgentResult(
                    content=content,
                    turns=turn,
                    tool_calls=total_tool_calls,
                    messages=messages,
                )

            if finish_reason not in {"tool_calls", "stop"}:
                raise AgentProtocolError(
                    f"Unexpected finish_reason with tool calls: {finish_reason}"
                )
            total_tool_calls += len(calls)
            for call in calls:
                tool_name = call.function.name
                self._emit(
                    "tool_started",
                    turn=turn,
                    tool=tool_name,
                    arguments=_safe_argument_summary(call.function.arguments),
                )
                tool_result = self._execute_tool(
                    tool_by_name=tool_by_name,
                    tool_name=tool_name,
                    raw_arguments=call.function.arguments,
                )
                parsed_result = json.loads(tool_result)
                self._emit(
                    "tool_completed",
                    turn=turn,
                    tool=tool_name,
                    ok=bool(parsed_result.get("ok")),
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": tool_result,
                    }
                )

        raise AgentProtocolError(
            f"Kimi K3 exceeded {self._settings.max_agent_turns} agent turns"
        )

    def _finalize(
        self,
        *,
        messages: list[Any],
        tools: Sequence[ToolSpec],
        response_format: Mapping[str, Any],
        turn: int,
        total_tool_calls: int,
    ) -> AgentResult:
        self._emit(
            "model_request_started",
            phase="structured_finalization",
            turn=turn + 1,
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    "Now convert only the evidence collected in this conversation into "
                    "the required strict JSON object. Do not call more tools, add new "
                    "facts, or expose reasoning."
                ),
            }
        )
        request: dict[str, Any] = {
            "model": self._settings.model,
            "reasoning_effort": self._settings.reasoning_effort,
            "messages": messages,
            "max_completion_tokens": self._settings.max_completion_tokens,
            "response_format": dict(response_format),
        }
        if tools:
            request["tools"] = [tool.api_definition() for tool in tools]
            request["tool_choice"] = "none"
        response = self._client.chat.completions.create(**request)
        if not getattr(response, "choices", None):
            raise AgentProtocolError("Kimi K3 finalizer returned no choices")
        choice = response.choices[0]
        assistant_message = choice.message
        messages.append(assistant_message)
        finish_reason = getattr(choice, "finish_reason", "unknown")
        if finish_reason == "length":
            raise AgentProtocolError("Kimi K3 structured output was truncated")
        if finish_reason != "stop":
            raise AgentProtocolError(
                f"Unexpected structured-output finish_reason: {finish_reason}"
            )
        if getattr(assistant_message, "tool_calls", None):
            raise AgentProtocolError("Kimi K3 called a tool during finalization")
        content = getattr(assistant_message, "content", None)
        if not content:
            raise AgentProtocolError("Kimi K3 finalizer returned empty content")
        self._emit(
            "model_response_received",
            phase="structured_finalization",
            turn=turn + 1,
            finish_reason=finish_reason,
            tool_call_count=0,
        )
        return AgentResult(
            content=content,
            turns=turn + 1,
            tool_calls=total_tool_calls,
            messages=messages,
        )

    def _execute_tool(
        self,
        *,
        tool_by_name: Mapping[str, ToolSpec],
        tool_name: str,
        raw_arguments: str,
    ) -> str:
        if tool_name not in tool_by_name:
            return json.dumps(
                {"ok": False, "error": f"Unknown tool: {tool_name}"},
                ensure_ascii=False,
            )
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be a JSON object")
            tool = tool_by_name[tool_name]
            Draft202012Validator(tool.parameters).validate(arguments)
            payload = tool.handler(arguments)
            result = json.dumps(
                {"ok": True, "result": payload},
                ensure_ascii=False,
                default=str,
            )
        except Exception as exc:  # tool errors are observations, not loop crashes
            result = json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )

        if len(result) <= self._settings.max_tool_result_chars:
            return result
        return json.dumps(
            {
                "ok": False,
                "error": "Tool result exceeded the configured size limit",
                "original_chars": len(result),
            },
            ensure_ascii=False,
        )

    def _emit(self, event: str, **details: Any) -> None:
        if self._progress_handler is None:
            return
        # Progress is deliberately metadata-only: never emit prompts, model
        # content/reasoning, tool results, or credentials.
        self._progress_handler({"event": event, **details})


def _safe_argument_summary(raw_arguments: str) -> dict[str, Any]:
    try:
        arguments = json.loads(raw_arguments or "{}")
    except (TypeError, json.JSONDecodeError):
        return {"valid_json": False}
    if not isinstance(arguments, dict):
        return {"valid_json": False}

    summary: dict[str, Any] = {"valid_json": True}
    for key in ("file_role", "pages", "page", "max_results"):
        if key in arguments:
            summary[key] = arguments[key]
    for key in ("query", "focus"):
        if key in arguments and isinstance(arguments[key], str):
            summary[f"{key}_chars"] = len(arguments[key])
    return summary
