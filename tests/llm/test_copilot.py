"""Tests for strix.llm.copilot header helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from strix.llm.copilot import (
    _is_github_copilot_model,
    configure_copilot_litellm,
    get_copilot_extra_headers,
    maybe_copilot_headers,
)


if TYPE_CHECKING:
    from strix.llm.llm import LLM


# ---------------------------------------------------------------------------
# _is_github_copilot_model
# ---------------------------------------------------------------------------


class TestIsGithubCopilotModel:
    @pytest.mark.parametrize(
        "model_name",
        [
            "github_copilot/gpt-4o",
            "github_copilot/claude-opus-4-20250514",
            "GITHUB_COPILOT/gpt-4o",
            "GitHub_Copilot/o1-mini",
        ],
    )
    def test_copilot_models_detected(self, model_name: str) -> None:
        assert _is_github_copilot_model(model_name) is True

    @pytest.mark.parametrize(
        "model_name",
        [
            "openai/gpt-4o",
            "anthropic/claude-3-5-sonnet",
            "gpt-4o",
            "",
        ],
    )
    def test_non_copilot_models_rejected(self, model_name: str) -> None:
        with patch("strix.llm.copilot.Config.get", return_value=None):
            assert _is_github_copilot_model(model_name) is False

    def test_none_falls_back_to_config(self) -> None:
        with patch("strix.llm.copilot.Config.get", return_value="github_copilot/gpt-4o"):
            assert _is_github_copilot_model(None) is True

    def test_none_with_non_copilot_config(self) -> None:
        with patch("strix.llm.copilot.Config.get", return_value="openai/gpt-4o"):
            assert _is_github_copilot_model(None) is False


# ---------------------------------------------------------------------------
# configure_copilot_litellm
# ---------------------------------------------------------------------------


class TestConfigureCopilotLitellm:
    def test_sets_flag_for_copilot_model(self) -> None:
        import litellm

        original = litellm.disable_copilot_system_to_assistant
        try:
            litellm.disable_copilot_system_to_assistant = False
            with patch("strix.llm.copilot.Config.get", return_value="github_copilot/gpt-4o"):
                configure_copilot_litellm()
            assert litellm.disable_copilot_system_to_assistant is True
        finally:
            litellm.disable_copilot_system_to_assistant = original

    def test_noop_for_non_copilot_model(self) -> None:
        import litellm

        original = litellm.disable_copilot_system_to_assistant
        try:
            litellm.disable_copilot_system_to_assistant = False
            with patch("strix.llm.copilot.Config.get", return_value="openai/gpt-4o"):
                configure_copilot_litellm()
            assert litellm.disable_copilot_system_to_assistant is False
        finally:
            litellm.disable_copilot_system_to_assistant = original

    def test_noop_when_no_model_configured(self) -> None:
        import litellm

        original = litellm.disable_copilot_system_to_assistant
        try:
            litellm.disable_copilot_system_to_assistant = False
            with patch("strix.llm.copilot.Config.get", return_value=None):
                configure_copilot_litellm()
            assert litellm.disable_copilot_system_to_assistant is False
        finally:
            litellm.disable_copilot_system_to_assistant = original

    def test_called_from_llm_init(self) -> None:
        """Verify that importing strix.llm triggers configure_copilot_litellm."""
        with patch("strix.llm.copilot.configure_copilot_litellm") as mock_configure:
            import importlib

            import strix.llm

            importlib.reload(strix.llm)
            mock_configure.assert_called_once()


# ---------------------------------------------------------------------------
# get_copilot_extra_headers
# ---------------------------------------------------------------------------


class TestGetCopilotExtraHeaders:
    def test_returns_required_headers(self) -> None:
        headers = get_copilot_extra_headers()
        required_keys = {
            "copilot-integration-id",
            "editor-version",
            "editor-plugin-version",
            "user-agent",
            "openai-intent",
            "x-github-api-version",
            "x-request-id",
            "x-vscode-user-agent-library-version",
        }
        assert required_keys == set(headers.keys())

    def test_editor_version_present(self) -> None:
        headers = get_copilot_extra_headers()
        assert headers["editor-version"].startswith("vscode/")

    def test_x_request_id_is_unique(self) -> None:
        h1 = get_copilot_extra_headers()
        h2 = get_copilot_extra_headers()
        assert h1["x-request-id"] != h2["x-request-id"]

    def test_no_authorization_header(self) -> None:
        headers = get_copilot_extra_headers()
        assert "Authorization" not in headers
        assert "authorization" not in headers


# ---------------------------------------------------------------------------
# maybe_copilot_headers
# ---------------------------------------------------------------------------


class TestMaybeCopilotHeaders:
    def test_copilot_model_returns_extra_headers(self) -> None:
        result = maybe_copilot_headers("github_copilot/gpt-4o")
        assert "extra_headers" in result
        assert "editor-version" in result["extra_headers"]

    def test_non_copilot_model_returns_empty(self) -> None:
        result = maybe_copilot_headers("openai/gpt-4o")
        assert result == {}

    def test_none_delegates_to_config(self) -> None:
        with patch("strix.llm.copilot.Config.get", return_value="github_copilot/gpt-4o"):
            result = maybe_copilot_headers(None)
            assert "extra_headers" in result

    def test_can_unpack_into_kwargs(self) -> None:
        base = {"model": "github_copilot/gpt-4o", "messages": []}
        base.update(maybe_copilot_headers("github_copilot/gpt-4o"))
        assert "extra_headers" in base
        assert base["model"] == "github_copilot/gpt-4o"

    def test_empty_unpack_is_noop(self) -> None:
        base = {"model": "openai/gpt-4o", "messages": []}
        base.update(maybe_copilot_headers("openai/gpt-4o"))
        assert "extra_headers" not in base


# ---------------------------------------------------------------------------
# Integration: _build_completion_args includes headers
# ---------------------------------------------------------------------------


class TestBuildCompletionArgsIncludesHeaders:
    """Verify that LLM._build_completion_args merges Copilot headers."""

    def test_copilot_model_gets_extra_headers(self) -> None:
        from strix.llm.config import LLMConfig
        from strix.llm.llm import LLM

        config = LLMConfig(model_name="github_copilot/gpt-4o")
        llm = LLM(config)
        args = llm._build_completion_args([{"role": "user", "content": "hi"}])
        assert "extra_headers" in args
        assert "editor-version" in args["extra_headers"]

    def test_non_copilot_model_no_extra_headers(self) -> None:
        from strix.llm.config import LLMConfig
        from strix.llm.llm import LLM

        config = LLMConfig(model_name="openai/gpt-4o")
        llm = LLM(config)
        args = llm._build_completion_args([{"role": "user", "content": "hi"}])
        assert "extra_headers" not in args


# ---------------------------------------------------------------------------
# Integration: dedupe.check_duplicate passes headers
# ---------------------------------------------------------------------------


class TestDedupePassesCopilotHeaders:
    @patch("strix.llm.dedupe.litellm.completion")
    @patch("strix.llm.dedupe.resolve_llm_config", return_value=("github_copilot/gpt-4o", None, None))
    def test_copilot_model_sends_headers(self, mock_resolve, mock_completion) -> None:
        mock_response = type(
            "Resp",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {
                            "message": type(
                                "Msg",
                                (),
                                {
                                    "content": (
                                        "<dedupe_result>"
                                        "<is_duplicate>false</is_duplicate>"
                                        "<duplicate_id></duplicate_id>"
                                        "<confidence>0.9</confidence>"
                                        "<reason>Different</reason>"
                                        "</dedupe_result>"
                                    )
                                },
                            )()
                        },
                    )()
                ]
            },
        )()
        mock_completion.return_value = mock_response

        from strix.llm.dedupe import check_duplicate

        candidate = {"title": "XSS in /search", "endpoint": "/search"}
        existing = [{"id": "vuln-001", "title": "SQLi in /login", "endpoint": "/login"}]
        check_duplicate(candidate, existing)

        _, kwargs = mock_completion.call_args
        assert "extra_headers" in kwargs
        assert "editor-version" in kwargs["extra_headers"]


# ---------------------------------------------------------------------------
# Integration: memory_compressor._summarize_messages passes headers
# ---------------------------------------------------------------------------


class TestMemoryCompressorPassesCopilotHeaders:
    @patch("strix.llm.memory_compressor.litellm.completion")
    @patch("strix.llm.memory_compressor.resolve_llm_config", return_value=(None, None, None))
    def test_copilot_model_sends_headers(self, mock_resolve, mock_completion) -> None:
        mock_response = type(
            "Resp",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {"message": type("Msg", (), {"content": "Summary of messages"})()},
                    )()
                ]
            },
        )()
        mock_completion.return_value = mock_response

        from strix.llm.memory_compressor import _summarize_messages

        messages = [{"role": "user", "content": "test message"}]
        _summarize_messages(messages, "github_copilot/gpt-4o")

        _, kwargs = mock_completion.call_args
        assert "extra_headers" in kwargs
        assert "editor-version" in kwargs["extra_headers"]


# ---------------------------------------------------------------------------
# LLM._is_copilot
# ---------------------------------------------------------------------------


class TestIsCopilot:
    def test_copilot_model(self) -> None:
        from strix.llm.config import LLMConfig
        from strix.llm.llm import LLM

        llm = LLM(LLMConfig(model_name="github_copilot/claude-opus-4.6"))
        assert llm._is_copilot() is True

    def test_non_copilot_model(self) -> None:
        from strix.llm.config import LLMConfig
        from strix.llm.llm import LLM

        llm = LLM(LLMConfig(model_name="openai/gpt-4o"))
        assert llm._is_copilot() is False

    def test_none_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from strix.llm.config import LLMConfig
        from strix.llm.llm import LLM

        monkeypatch.delenv("STRIX_LLM", raising=False)

        def _config_get(key: str) -> str | None:
            if key == "strix_llm":
                return "openai/gpt-4o"
            return None

        with patch("strix.llm.config.Config.get", side_effect=_config_get):
            llm = LLM(LLMConfig(model_name=None))
        assert llm._is_copilot() is False

    def test_case_insensitive(self) -> None:
        from strix.llm.config import LLMConfig
        from strix.llm.llm import LLM

        llm = LLM(LLMConfig(model_name="GITHUB_COPILOT/gpt-4o"))
        assert llm._is_copilot() is True


# ---------------------------------------------------------------------------
# _prepare_messages: Copilot assistant-trailing fix
# ---------------------------------------------------------------------------


class TestPrepareMessagesCopilotFix:
    """Verify _prepare_messages appends a user 'Continue.' for Copilot
    when the conversation ends with an assistant message."""

    def _make_llm(self, model_name: str) -> LLM:
        from strix.llm.config import LLMConfig
        from strix.llm.llm import LLM

        return LLM(LLMConfig(model_name=model_name))

    def test_copilot_appends_continue_when_last_is_assistant(self) -> None:
        llm = self._make_llm("github_copilot/claude-opus-4.6")
        history = [
            {"role": "user", "content": "Scan the target"},
            {"role": "assistant", "content": "I found some issues."},
        ]
        messages = llm._prepare_messages(history)
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "Continue."

    def test_copilot_no_append_when_last_is_user(self) -> None:
        llm = self._make_llm("github_copilot/claude-opus-4.6")
        history = [
            {"role": "user", "content": "Scan the target"},
        ]
        messages = llm._prepare_messages(history)
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] != "Continue."

    def test_non_copilot_no_append_even_with_assistant_last(self) -> None:
        llm = self._make_llm("openai/gpt-4o")
        history = [
            {"role": "user", "content": "Scan the target"},
            {"role": "assistant", "content": "I found some issues."},
        ]
        messages = llm._prepare_messages(history)
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["content"] == "I found some issues."

    def test_copilot_appends_continue_when_last_is_system(self) -> None:
        """Edge case: if somehow conversation has only system message."""
        llm = self._make_llm("github_copilot/claude-opus-4.6")
        llm.agent_name = None
        history: list = []
        messages = llm._prepare_messages(history)
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "Continue."
