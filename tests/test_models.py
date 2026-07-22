"""Tests for LLM model recommendation helpers."""

from __future__ import annotations

import pytest
from agents.model_settings import ModelSettings
from agents.models.chatcmpl_helpers import HEADERS_OVERRIDE

from strix.config.models import (
    RECOMMENDED_MODEL_NAMES,
    _configure_github_copilot_headers,
    is_recommended_or_frontier_model,
    request_timeout_extra_args,
)


@pytest.mark.parametrize("model_name", RECOMMENDED_MODEL_NAMES)
def test_recommended_models_are_accepted(model_name: str) -> None:
    assert is_recommended_or_frontier_model(model_name)


def test_request_timeout_extra_args_positive() -> None:
    assert request_timeout_extra_args(300) == {"timeout": 300}
    assert request_timeout_extra_args(10) == {"timeout": 10}


def test_request_timeout_extra_args_survives_model_settings_json_dump() -> None:
    """The Chat Completions and LiteLLM paths pydantic-serialize ModelSettings for
    their tracing span; a non-JSON-serializable timeout fails every turn there."""
    settings = ModelSettings(extra_args=request_timeout_extra_args(300))
    assert settings.to_json_dict()["extra_args"] == {"timeout": 300}


@pytest.mark.parametrize("value", [None, 0, -1])
def test_request_timeout_extra_args_disabled(value: float | None) -> None:
    assert request_timeout_extra_args(value) is None


def test_recommended_models_are_matched_case_insensitively() -> None:
    assert is_recommended_or_frontier_model("Vertex_AI/Gemini-3-Pro-Preview")


@pytest.mark.parametrize(
    "model_name",
    [
        "gpt-5.5",
        "litellm/openai/gpt-5.4-pro",
        "azure_ai/gpt-5.5-pro",
        "bedrock_mantle/openai.gpt-5.5",
        "anthropic/claude-opus-4-8",
        "anthropic.claude-opus-4-8",
        "anthropic/claude-opus-4-7",
        "anthropic/claude-fable-5",
        "anthropic/claude-sonnet-5",
        "vertex_ai/claude-sonnet-5@default",
        "vertex_ai/claude-sonnet-4-6@default",
        "any-llm/anthropic/claude-sonnet-4-6",
        "vertex_ai/gemini-3.1-pro-preview",
        "openrouter/google/gemini-3.1-pro-preview",
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-r1-0528",
        "deepseek/deepseek-reasoner",
        "dashscope/qwen3-max-2026-01-23",
        "qwen3.7-max",
        "moonshot/kimi-k2.6",
        "kimi-k2.7-code",
    ],
)
def test_frontier_model_families_are_accepted(model_name: str) -> None:
    assert is_recommended_or_frontier_model(model_name)


@pytest.mark.parametrize(
    "model_name",
    [
        "",
        "openai/gpt-4.1",
        "anthropic/claude-3-5-sonnet-latest",
        "ollama/llama3.1",
        "deepseek/deepseek-chat",
        "custom-ollama/gpt-5-mini-local",
        "custom-provider/claude-opus-4-local",
        "xai/grok-4.5",
        "openrouter/x-ai/grok-4",
        "mistral/mistral-medium-3-5",
        "mistral/magistral-medium-latest",
    ],
)
def test_non_frontier_models_are_rejected(model_name: str) -> None:
    assert not is_recommended_or_frontier_model(model_name)


class TestConfigureGithubCopilotHeaders:
    """Fork-specific: strix.config.models._configure_github_copilot_headers().

    Works around a litellm bug where any non-empty ``extra_headers`` makes
    litellm's ``github_copilot`` provider skip its own Copilot request-header
    injection. See strix/llm/copilot.py for the full explanation.
    """

    def test_sets_headers_override_for_copilot_model(self) -> None:
        """A configured github_copilot/* model populates the SDK's HEADERS_OVERRIDE."""
        token = HEADERS_OVERRIDE.set(None)
        try:
            _configure_github_copilot_headers("github_copilot/claude-sonnet-5")
            override = HEADERS_OVERRIDE.get()
            assert override is not None
            assert "editor-version" in override
        finally:
            HEADERS_OVERRIDE.reset(token)

    def test_noop_for_non_copilot_model(self) -> None:
        """Non-Copilot models leave HEADERS_OVERRIDE untouched."""
        token = HEADERS_OVERRIDE.set(None)
        try:
            _configure_github_copilot_headers("openai/gpt-4o")
            assert HEADERS_OVERRIDE.get() is None
        finally:
            HEADERS_OVERRIDE.reset(token)

    def test_noop_for_none_model(self) -> None:
        """No configured model is a safe no-op."""
        token = HEADERS_OVERRIDE.set(None)
        try:
            _configure_github_copilot_headers(None)
            assert HEADERS_OVERRIDE.get() is None
        finally:
            HEADERS_OVERRIDE.reset(token)
