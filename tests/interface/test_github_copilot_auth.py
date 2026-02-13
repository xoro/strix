"""Tests for GitHub Copilot authentication support in strix.interface.main."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strix.interface.main import (
    _get_github_copilot_token_path,
    _has_github_copilot_token,
    _is_github_copilot_model,
    authenticate_github_copilot,
)


# ---------------------------------------------------------------------------
# _is_github_copilot_model
# ---------------------------------------------------------------------------


class TestIsGithubCopilotModel:
    """Tests for the _is_github_copilot_model helper."""

    @pytest.mark.parametrize(
        "model_name",
        [
            "github_copilot/gpt-4",
            "github_copilot/gpt-4o",
            "github_copilot/o1-mini",
            "GITHUB_COPILOT/gpt-4",
            "GitHub_Copilot/gpt-4o",
        ],
    )
    def test_copilot_models_return_true(self, model_name: str) -> None:
        """Copilot-prefixed model names should be detected regardless of case."""
        assert _is_github_copilot_model(model_name) is True

    @pytest.mark.parametrize(
        "model_name",
        [
            "openai/gpt-4",
            "anthropic/claude-3-opus",
            "gpt-4o",
            "ollama/llama3",
            "github/copilot",
            "xgithub_copilot/gpt-4",
        ],
    )
    def test_non_copilot_models_return_false(self, model_name: str) -> None:
        """Non-Copilot model names should return False."""
        assert _is_github_copilot_model(model_name) is False

    def test_none_returns_false(self) -> None:
        """None (no model configured) should return False."""
        assert _is_github_copilot_model(None) is False

    def test_empty_string_returns_false(self) -> None:
        """Empty string should return False."""
        assert _is_github_copilot_model("") is False

    def test_reads_config_when_no_argument(self) -> None:
        """When called without an argument, should read from Config."""
        with patch("strix.interface.main.Config") as mock_config:
            mock_config.get.return_value = "github_copilot/gpt-4o"
            assert _is_github_copilot_model() is True
            mock_config.get.assert_called_once_with("strix_llm")

    def test_reads_config_none_fallback(self) -> None:
        """When Config returns None, should return False."""
        with patch("strix.interface.main.Config") as mock_config:
            mock_config.get.return_value = None
            assert _is_github_copilot_model() is False


# ---------------------------------------------------------------------------
# _get_github_copilot_token_path
# ---------------------------------------------------------------------------


class TestGetGithubCopilotTokenPath:
    """Tests for the _get_github_copilot_token_path helper."""

    def test_default_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default path should be ~/.config/litellm/github_copilot/access-token."""
        monkeypatch.delenv("GITHUB_COPILOT_TOKEN_DIR", raising=False)
        monkeypatch.delenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", raising=False)
        result = _get_github_copilot_token_path()
        expected = Path.home() / ".config" / "litellm" / "github_copilot" / "access-token"
        assert result == expected

    def test_custom_token_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """GITHUB_COPILOT_TOKEN_DIR should override the directory."""
        custom_dir = str(tmp_path / "custom_tokens")
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", custom_dir)
        monkeypatch.delenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", raising=False)
        result = _get_github_copilot_token_path()
        assert result == Path(custom_dir) / "access-token"

    def test_custom_token_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GITHUB_COPILOT_ACCESS_TOKEN_FILE should override the filename."""
        monkeypatch.delenv("GITHUB_COPILOT_TOKEN_DIR", raising=False)
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "my-token")
        result = _get_github_copilot_token_path()
        assert result.name == "my-token"

    def test_both_env_vars(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Both env vars should be respected together."""
        custom_dir = str(tmp_path / "dir")
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", custom_dir)
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "tok")
        result = _get_github_copilot_token_path()
        assert result == Path(custom_dir) / "tok"


# ---------------------------------------------------------------------------
# _has_github_copilot_token
# ---------------------------------------------------------------------------


class TestHasGithubCopilotToken:
    """Tests for the _has_github_copilot_token helper."""

    def test_no_file_returns_false(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Missing token file should return False."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")
        assert _has_github_copilot_token() is False

    def test_empty_file_returns_false(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Empty token file should return False."""
        token_file = tmp_path / "access-token"
        token_file.write_text("")
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")
        assert _has_github_copilot_token() is False

    def test_whitespace_only_file_returns_false(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Token file with only whitespace should return False."""
        token_file = tmp_path / "access-token"
        token_file.write_text("   \n\t  \n")
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")
        assert _has_github_copilot_token() is False

    def test_valid_token_returns_true(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Token file with content should return True."""
        token_file = tmp_path / "access-token"
        token_file.write_text("gho_abc123def456")
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")
        assert _has_github_copilot_token() is True

    def test_os_error_returns_false(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """OSError when reading token file should return False."""
        token_dir = tmp_path / "access-token"
        token_dir.mkdir()
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")
        assert _has_github_copilot_token() is False


# ---------------------------------------------------------------------------
# authenticate_github_copilot
# ---------------------------------------------------------------------------


class TestAuthenticateGithubCopilot:
    """Tests for the authenticate_github_copilot function."""

    def test_success_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Successful auth should not call sys.exit."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")

        mock_auth = MagicMock()
        mock_auth.get_access_token.return_value = "gho_token123"
        mock_auth.get_api_key.return_value = None

        mock_auth_cls = MagicMock(return_value=mock_auth)

        with patch.dict(
            "sys.modules",
            {"litellm.llms.github_copilot.authenticator": MagicMock(Authenticator=mock_auth_cls)},
        ):
            authenticate_github_copilot()

        mock_auth.get_access_token.assert_called_once()

    def test_auth_failure_exits(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Auth failure should sys.exit(1)."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")

        mock_auth = MagicMock()
        mock_auth.get_access_token.side_effect = RuntimeError("OAuth failed")

        mock_auth_cls = MagicMock(return_value=mock_auth)

        with (
            patch.dict(
                "sys.modules",
                {
                    "litellm.llms.github_copilot.authenticator": MagicMock(
                        Authenticator=mock_auth_cls
                    ),
                },
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            authenticate_github_copilot()

    def test_existing_token_shows_re_auth_message(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When a token already exists, should mention re-authenticating."""
        token_file = tmp_path / "access-token"
        token_file.write_text("existing_token")
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")

        mock_auth = MagicMock()
        mock_auth.get_access_token.return_value = "gho_new_token"
        mock_auth.get_api_key.return_value = None

        mock_auth_cls = MagicMock(return_value=mock_auth)

        with patch.dict(
            "sys.modules",
            {"litellm.llms.github_copilot.authenticator": MagicMock(Authenticator=mock_auth_cls)},
        ):
            authenticate_github_copilot()

    def test_api_key_expiry_display(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """When api-key.json exists with expires_at, should not crash."""
        token_file = tmp_path / "access-token"
        token_file.write_text("")
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")

        api_key_file = tmp_path / "api-key.json"
        api_key_file.write_text(
            json.dumps(
                {
                    "token": "sk-copilot-key",
                    "expires_at": 1893456000,
                    "endpoints": {},
                }
            )
        )

        mock_auth = MagicMock()
        mock_auth.get_access_token.return_value = "gho_token123"
        mock_auth.get_api_key.return_value = "sk-copilot-key"

        mock_auth_cls = MagicMock(return_value=mock_auth)

        with patch.dict(
            "sys.modules",
            {"litellm.llms.github_copilot.authenticator": MagicMock(Authenticator=mock_auth_cls)},
        ):
            authenticate_github_copilot()

        mock_auth.get_api_key.assert_called_once()

    def test_api_key_error_silently_ignored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Errors during api_key retrieval should be silently ignored."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")

        mock_auth = MagicMock()
        mock_auth.get_access_token.return_value = "gho_token123"
        mock_auth.get_api_key.side_effect = RuntimeError("API key refresh failed")

        mock_auth_cls = MagicMock(return_value=mock_auth)

        with patch.dict(
            "sys.modules",
            {"litellm.llms.github_copilot.authenticator": MagicMock(Authenticator=mock_auth_cls)},
        ):
            authenticate_github_copilot()


# ---------------------------------------------------------------------------
# parse_arguments — Copilot-related behaviour
# ---------------------------------------------------------------------------


class TestParseArgumentsCopilot:
    """Tests for --auth-github-copilot in parse_arguments."""

    def test_auth_flag_without_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--auth-github-copilot should work without --target."""
        monkeypatch.setattr(sys, "argv", ["strix", "--auth-github-copilot"])
        from strix.interface.main import parse_arguments

        args = parse_arguments()
        assert args.auth_github_copilot is True
        assert args.target is None

    def test_target_still_required_without_auth_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without --auth-github-copilot, --target should still be required."""
        monkeypatch.setattr(sys, "argv", ["strix"])
        from strix.interface.main import parse_arguments

        with pytest.raises(SystemExit):
            parse_arguments()

    def test_auth_flag_is_false_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """auth_github_copilot should default to False."""
        monkeypatch.setattr(sys, "argv", ["strix", "--target", "https://example.com"])
        from strix.interface.main import parse_arguments

        args = parse_arguments()
        assert args.auth_github_copilot is False


# ---------------------------------------------------------------------------
# validate_environment — Copilot skips LLM_API_KEY warning
# ---------------------------------------------------------------------------


class TestValidateEnvironmentCopilot:
    """Tests for Copilot-specific behaviour in validate_environment."""

    def test_missing_strix_llm_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing STRIX_LLM (required) should sys.exit(1)."""
        monkeypatch.delenv("STRIX_LLM", raising=False)

        from strix.interface.main import validate_environment

        with pytest.raises(SystemExit, match="1"):
            validate_environment()

    def test_copilot_model_does_not_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Copilot model with STRIX_LLM set should not exit (optional vars are fine)."""
        monkeypatch.setenv("STRIX_LLM", "github_copilot/gpt-4o")
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        from strix.interface.main import validate_environment

        validate_environment()

    def test_copilot_model_skips_api_key_collection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When using a Copilot model, LLM_API_KEY should not appear in optional warnings."""
        monkeypatch.setenv("STRIX_LLM", "github_copilot/gpt-4o")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("STRIX_LLM", raising=False)

        from strix.interface.main import validate_environment

        with (
            patch("strix.interface.main._is_github_copilot_model", return_value=True),
            patch("strix.interface.main.Config") as mock_config,
        ):
            mock_config.get.side_effect = lambda name: {
                "strix_llm": None,
            }.get(name)

            with pytest.raises(SystemExit):
                validate_environment()

    def test_non_copilot_model_collects_api_key_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When using a non-Copilot model, missing LLM_API_KEY should be collected."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("STRIX_LLM", raising=False)

        from strix.interface.main import validate_environment

        with (
            patch("strix.interface.main._is_github_copilot_model", return_value=False),
            patch("strix.interface.main.Config") as mock_config,
        ):
            mock_config.get.side_effect = lambda name: {
                "strix_llm": None,
                "llm_api_key": None,
            }.get(name)

            with pytest.raises(SystemExit):
                validate_environment()


# ---------------------------------------------------------------------------
# warm_up_llm — Copilot token guard
# ---------------------------------------------------------------------------


class TestWarmUpLlmCopilot:
    """Tests for the Copilot token guard in warm_up_llm."""

    async def test_copilot_without_token_exits(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Copilot model without a cached token should sys.exit(1)."""
        monkeypatch.setenv("STRIX_LLM", "github_copilot/gpt-4o")
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")

        from strix.interface.main import warm_up_llm

        with pytest.raises(SystemExit, match="1"):
            await warm_up_llm()

    async def test_copilot_with_token_proceeds_to_llm_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Copilot model with a cached token should proceed past the guard."""
        token_file = tmp_path / "access-token"
        token_file.write_text("gho_validtoken123")
        monkeypatch.setenv("STRIX_LLM", "github_copilot/gpt-4o")
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.delenv("OPENAI_API_BASE", raising=False)
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_BASE", raising=False)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "OK"

        from strix.interface.main import warm_up_llm

        with patch("strix.interface.main.litellm") as mock_litellm:
            mock_litellm.completion.return_value = mock_response
            with patch("strix.interface.main.validate_llm_response"):
                await warm_up_llm()

        mock_litellm.completion.assert_called_once()

    async def test_non_copilot_model_skips_guard(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Non-Copilot models should skip the token guard entirely."""
        monkeypatch.setenv("STRIX_LLM", "openai/gpt-4o")
        monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.delenv("OPENAI_API_BASE", raising=False)
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "OK"

        from strix.interface.main import warm_up_llm

        with patch("strix.interface.main.litellm") as mock_litellm:
            mock_litellm.completion.return_value = mock_response
            with patch("strix.interface.main.validate_llm_response"):
                await warm_up_llm()

        mock_litellm.completion.assert_called_once()

    async def test_copilot_llm_failure_shows_tip(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When a Copilot LLM call fails, the error should include a re-auth tip."""
        token_file = tmp_path / "access-token"
        token_file.write_text("gho_validtoken123")
        monkeypatch.setenv("STRIX_LLM", "github_copilot/gpt-4o")
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.delenv("OPENAI_API_BASE", raising=False)
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_BASE", raising=False)

        from strix.interface.main import warm_up_llm

        with patch("strix.interface.main.litellm") as mock_litellm:
            mock_litellm.completion.side_effect = RuntimeError("Connection refused")
            with pytest.raises(SystemExit, match="1"):
                await warm_up_llm()


# ---------------------------------------------------------------------------
# main() — auth early exit
# ---------------------------------------------------------------------------


class TestMainCopilotEarlyExit:
    """Tests for the early exit path in main() when --auth-github-copilot is set."""

    def test_auth_flag_calls_authenticate_and_returns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() with --auth-github-copilot should call authenticate and return early."""
        monkeypatch.setattr(sys, "argv", ["strix", "--auth-github-copilot"])

        with (
            patch("strix.interface.main.authenticate_github_copilot") as mock_auth,
            patch("strix.interface.main.check_container_runtime_installed") as mock_docker,
            patch("strix.interface.main.validate_environment") as mock_env,
        ):
            from strix.interface.main import main

            main()

            mock_auth.assert_called_once()
            mock_docker.assert_not_called()
            mock_env.assert_not_called()
