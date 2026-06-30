"""Tests for strix.llm.copilot header helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from strix.llm.copilot import (
    _is_github_copilot_model,
    configure_copilot_litellm,
    get_copilot_extra_headers,
    maybe_copilot_headers,
)


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
        assert _is_github_copilot_model(model_name) is False

    def test_none_falls_back_to_config(self) -> None:
        mock_settings = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        mock_settings.llm.model = "github_copilot/gpt-4o"
        with patch("strix.llm.copilot.load_settings", return_value=mock_settings):
            assert _is_github_copilot_model(None) is True

    def test_none_with_non_copilot_config(self) -> None:
        mock_settings = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        mock_settings.llm.model = "openai/gpt-4o"
        with patch("strix.llm.copilot.load_settings", return_value=mock_settings):
            assert _is_github_copilot_model(None) is False


# ---------------------------------------------------------------------------
# configure_copilot_litellm
# ---------------------------------------------------------------------------


class TestConfigureCopilotLitellm:
    def _mock_settings(self, model: str | None):
        from unittest.mock import MagicMock

        s = MagicMock()
        s.llm.model = model
        return s

    def test_sets_flag_for_copilot_model(self) -> None:
        import litellm

        original = litellm.disable_copilot_system_to_assistant
        try:
            litellm.disable_copilot_system_to_assistant = False
            with patch(
                "strix.llm.copilot.load_settings",
                return_value=self._mock_settings("github_copilot/gpt-4o"),
            ):
                configure_copilot_litellm()
            assert litellm.disable_copilot_system_to_assistant is True
        finally:
            litellm.disable_copilot_system_to_assistant = original

    def test_noop_for_non_copilot_model(self) -> None:
        import litellm

        original = litellm.disable_copilot_system_to_assistant
        try:
            litellm.disable_copilot_system_to_assistant = False
            with patch(
                "strix.llm.copilot.load_settings",
                return_value=self._mock_settings("openai/gpt-4o"),
            ):
                configure_copilot_litellm()
            assert litellm.disable_copilot_system_to_assistant is False
        finally:
            litellm.disable_copilot_system_to_assistant = original

    def test_noop_when_no_model_configured(self) -> None:
        import litellm

        original = litellm.disable_copilot_system_to_assistant
        try:
            litellm.disable_copilot_system_to_assistant = False
            with patch(
                "strix.llm.copilot.load_settings",
                return_value=self._mock_settings(None),
            ):
                configure_copilot_litellm()
            assert litellm.disable_copilot_system_to_assistant is False
        finally:
            litellm.disable_copilot_system_to_assistant = original

    def test_called_from_config_models(self) -> None:
        """Verify that configure_sdk_model_defaults triggers configure_copilot_litellm."""
        from unittest.mock import MagicMock

        from strix.config.models import configure_sdk_model_defaults

        settings = MagicMock()
        settings.llm.model = "github_copilot/gpt-4o"
        settings.llm.api_key = None
        settings.llm.api_base = None

        with patch("strix.llm.copilot.configure_copilot_litellm") as mock_configure:
            configure_sdk_model_defaults(settings)
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
        from unittest.mock import MagicMock

        mock_settings = MagicMock()
        mock_settings.llm.model = "github_copilot/gpt-4o"
        with patch("strix.llm.copilot.load_settings", return_value=mock_settings):
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
# Integration: make_model_settings injects Copilot headers
# ---------------------------------------------------------------------------


class TestMakeModelSettingsIncludesHeaders:
    """Verify that make_model_settings populates extra_headers for Copilot models."""

    def test_copilot_model_gets_extra_headers(self) -> None:
        from strix.core.inputs import make_model_settings

        settings = make_model_settings(None, model_name="github_copilot/gpt-4o")
        assert settings.extra_headers is not None
        assert "editor-version" in settings.extra_headers

    def test_non_copilot_model_no_extra_headers(self) -> None:
        from strix.core.inputs import make_model_settings

        settings = make_model_settings(None, model_name="openai/gpt-4o")
        assert settings.extra_headers is None


# ---------------------------------------------------------------------------
# Integration: dedupe.check_duplicate passes headers (new SDK path)
# ---------------------------------------------------------------------------


class TestDedupePassesCopilotHeaders:
    @pytest.mark.asyncio
    async def test_copilot_model_sends_headers(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from strix.report.dedupe import check_duplicate

        mock_response = MagicMock()
        mock_response.usage = None
        mock_response.output = [MagicMock()]
        mock_response.output[0].content = [MagicMock()]
        mock_response.output[0].content[0].text = (
            '{"is_duplicate": false, "duplicate_id": "", "confidence": 0.9, "reason": "Different"}'
        )

        mock_model = MagicMock()
        mock_model.get_response = AsyncMock(return_value=mock_response)
        captured_settings: list = []

        original_get_response = mock_model.get_response

        async def capture(*args, **kwargs):
            captured_settings.append(kwargs.get("model_settings"))
            return mock_response

        mock_model.get_response = capture

        settings_mock = MagicMock()
        settings_mock.llm.model = "github_copilot/gpt-4o"

        with (
            patch("strix.report.dedupe.load_settings", return_value=settings_mock),
            patch("strix.report.dedupe.configure_sdk_model_defaults"),
            patch("strix.report.dedupe.StrixProvider") as mock_provider,
            patch("strix.report.dedupe.get_global_report_state", return_value=None),
        ):
            mock_provider.return_value.get_model.return_value = mock_model
            await check_duplicate(
                {"title": "XSS in /search"},
                [{"id": "vuln-001", "title": "SQLi in /login"}],
            )

        assert len(captured_settings) == 1
        ms = captured_settings[0]
        assert ms is not None
        assert ms.extra_headers is not None
        assert "editor-version" in ms.extra_headers


# ---------------------------------------------------------------------------
# (TestMemoryCompressorPassesCopilotHeaders removed: memory_compressor is now
# handled by the openai-agents SDK which uses ModelSettings.extra_headers set
# in make_model_settings / check_duplicate — see TestMakeModelSettingsIncludesHeaders
# and TestDedupePassesCopilotHeaders above.)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# (TestBuildCompletionArgsIncludesHeaders, TestIsCopilot, TestPrepareMessagesCopilotFix
# removed: the old strix.llm.llm.LLM class and strix.llm.config.LLMConfig were
# replaced by the openai-agents SDK in v1.0. Equivalent coverage is provided by
# TestMakeModelSettingsIncludesHeaders above.)
# ---------------------------------------------------------------------------
