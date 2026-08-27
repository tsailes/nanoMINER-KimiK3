from __future__ import annotations

import os
from dataclasses import dataclass, replace


class ConfigurationError(ValueError):
    """Raised when the Kimi runtime configuration is unsafe or incomplete."""


_REASONING_EFFORTS = frozenset({"low", "high", "max"})
_CHINA_OPEN_PLATFORM_BASE_URL = "https://api.moonshot.cn/v1"
_GLOBAL_OPEN_PLATFORM_BASE_URL = "https://api.moonshot.ai/v1"
_DEFAULT_BASE_URL = _CHINA_OPEN_PLATFORM_BASE_URL
_DEFAULT_MODEL = "kimi-k3"
_DEFAULT_REASONING_EFFORT = "high"
_DEFAULT_MAX_AGENT_TURNS = 10
_DEFAULT_MAX_COMPLETION_TOKENS = 32768
_DEFAULT_MAX_TOOL_RESULT_CHARS = 180_000
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 300
_DEFAULT_TRANSPORT_RETRIES = 4
_KIMI_CODE_BASE_URL = "https://api.kimi.com/coding/v1"
_KIMI_CODE_MODEL = "k3"
_ALLOWED_BASE_URLS = frozenset(
    {
        _CHINA_OPEN_PLATFORM_BASE_URL,
        _GLOBAL_OPEN_PLATFORM_BASE_URL,
        _KIMI_CODE_BASE_URL,
    }
)


def _positive_int(name: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < 1:
        raise ConfigurationError(f"{name} must be positive")
    return value


def _is_kimi_code_url(base_url: str) -> bool:
    return (
        base_url.rstrip("/").casefold()
        == _KIMI_CODE_BASE_URL.casefold()
    )


def _normalized_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


@dataclass(frozen=True, slots=True)
class KimiSettings:
    """Runtime settings for the Kimi K3 coordinator and vision worker."""

    api_key: str | None
    base_url: str = _DEFAULT_BASE_URL
    model: str = _DEFAULT_MODEL
    reasoning_effort: str = _DEFAULT_REASONING_EFFORT
    max_agent_turns: int = _DEFAULT_MAX_AGENT_TURNS
    max_completion_tokens: int = _DEFAULT_MAX_COMPLETION_TOKENS
    max_tool_result_chars: int = _DEFAULT_MAX_TOOL_RESULT_CHARS
    request_timeout_seconds: int = _DEFAULT_REQUEST_TIMEOUT_SECONDS
    transport_retries: int = _DEFAULT_TRANSPORT_RETRIES

    @classmethod
    def from_env(cls, *, require_api_key: bool = True) -> "KimiSettings":
        api_key = os.getenv("KIMI_API_KEY") or os.getenv("MOONSHOT_API_KEY")
        base_url = os.getenv("KIMI_BASE_URL", _DEFAULT_BASE_URL)
        settings = cls(
            api_key=api_key,
            base_url=base_url,
            model=os.getenv(
                "KIMI_MODEL",
                _KIMI_CODE_MODEL if _is_kimi_code_url(base_url) else _DEFAULT_MODEL,
            ),
            reasoning_effort=os.getenv(
                "KIMI_REASONING_EFFORT", _DEFAULT_REASONING_EFFORT
            ).lower(),
            max_agent_turns=_positive_int(
                "KIMI_MAX_AGENT_TURNS",
                os.getenv("KIMI_MAX_AGENT_TURNS", str(_DEFAULT_MAX_AGENT_TURNS)),
            ),
            max_completion_tokens=_positive_int(
                "KIMI_MAX_COMPLETION_TOKENS",
                os.getenv(
                    "KIMI_MAX_COMPLETION_TOKENS",
                    str(_DEFAULT_MAX_COMPLETION_TOKENS),
                ),
            ),
            max_tool_result_chars=_positive_int(
                "KIMI_MAX_TOOL_RESULT_CHARS",
                os.getenv(
                    "KIMI_MAX_TOOL_RESULT_CHARS",
                    str(_DEFAULT_MAX_TOOL_RESULT_CHARS),
                ),
            ),
            request_timeout_seconds=_positive_int(
                "KIMI_REQUEST_TIMEOUT_SECONDS",
                os.getenv(
                    "KIMI_REQUEST_TIMEOUT_SECONDS",
                    str(_DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ),
            ),
            transport_retries=_positive_int(
                "KIMI_TRANSPORT_RETRIES",
                os.getenv(
                    "KIMI_TRANSPORT_RETRIES",
                    str(_DEFAULT_TRANSPORT_RETRIES),
                ),
            ),
        )
        settings.validate(require_api_key=require_api_key)
        return settings

    def validate(self, *, require_api_key: bool = True) -> None:
        if require_api_key and not self.api_key:
            raise ConfigurationError(
                "KIMI_API_KEY or MOONSHOT_API_KEY is required; pass it through "
                "the environment, not a CLI flag"
            )
        expected_model = (
            _KIMI_CODE_MODEL if self.service == "kimi_code" else _DEFAULT_MODEL
        )
        if self.model != expected_model:
            raise ConfigurationError(
                "The corrected reproduction pins the core agent to Kimi K3: "
                f"use model {expected_model!r} for {self.service}"
            )
        if self.reasoning_effort not in _REASONING_EFFORTS:
            allowed = ", ".join(sorted(_REASONING_EFFORTS))
            raise ConfigurationError(
                f"KIMI_REASONING_EFFORT must be one of: {allowed}"
            )
        if _normalized_base_url(self.base_url) not in _ALLOWED_BASE_URLS:
            allowed = ", ".join(sorted(_ALLOWED_BASE_URLS))
            raise ConfigurationError(
                "KIMI_BASE_URL must be an official Kimi endpoint: " + allowed
            )

    def with_reasoning_effort(self, effort: str | None) -> "KimiSettings":
        if effort is None:
            return self
        updated = replace(self, reasoning_effort=effort.lower())
        updated.validate(require_api_key=bool(updated.api_key))
        return updated

    def public_summary(self) -> dict[str, object]:
        """Return diagnostics that never include credentials."""

        return {
            "api_key_configured": bool(self.api_key),
            "service": self.service,
            "region": self.region,
            "base_url": self.base_url,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "max_agent_turns": self.max_agent_turns,
            "max_completion_tokens": self.max_completion_tokens,
            "max_tool_result_chars": self.max_tool_result_chars,
            "request_timeout_seconds": self.request_timeout_seconds,
            "transport_retries": self.transport_retries,
        }

    @property
    def service(self) -> str:
        return "kimi_code" if _is_kimi_code_url(self.base_url) else "open_platform"

    @property
    def region(self) -> str:
        normalized = _normalized_base_url(self.base_url)
        if normalized == _CHINA_OPEN_PLATFORM_BASE_URL:
            return "china"
        if normalized == _GLOBAL_OPEN_PLATFORM_BASE_URL:
            return "global"
        return "kimi_code"
