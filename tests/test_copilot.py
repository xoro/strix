"""Tests for strix.llm.copilot: GitHub Copilot device-flow auth helpers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from litellm.llms.github_copilot.common_utils import GetAccessTokenError

from strix.llm import copilot


if TYPE_CHECKING:
    from pathlib import Path


_COPILOT_ENV_KEYS = [
    "GITHUB_COPILOT_HOSTNAME",
    "GITHUB_COPILOT_DEVICE_CODE_URL",
    "GITHUB_COPILOT_ACCESS_TOKEN_URL",
    "GITHUB_COPILOT_API_KEY_URL",
    "GITHUB_COPILOT_API_BASE",
    "GITHUB_COPILOT_USER_API_URL",
    "GITHUB_COPILOT_TOKEN_DIR",
    "GITHUB_COPILOT_ACCESS_TOKEN_FILE",
    "GITHUB_COPILOT_API_KEY_FILE",
    "GITHUB_COPILOT_CLIENT_ID",
]


@pytest.fixture(autouse=True)
def _clean_copilot_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _COPILOT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


class TestIsGithubCopilotModel:
    @pytest.mark.parametrize(
        ("model_name", "expected"),
        [
            ("github_copilot/gpt-4o", True),
            ("GITHUB_COPILOT/gpt-4o", True),
            ("  github_copilot/gpt-4o  ", True),
            ("openai/gpt-4o", False),
            ("anthropic/claude-opus-4-7", False),
            ("", False),
            (None, False),
        ],
    )
    def test_detection(self, model_name: str | None, expected: bool) -> None:
        """Model detection is case-insensitive and prefix-based."""
        assert copilot.is_github_copilot_model(model_name) is expected


class TestGithubCopilotExtraHeaders:
    def test_empty_for_non_copilot_model(self) -> None:
        """Non-Copilot models get no extra headers — this is a Copilot-only workaround."""
        assert copilot.github_copilot_extra_headers("openai/gpt-4o") == {}

    def test_empty_for_none_model(self) -> None:
        """No configured model means no headers."""
        assert copilot.github_copilot_extra_headers(None) == {}

    def test_includes_editor_version_for_copilot_model(self) -> None:
        """The editor-version header is present — this is what avoids the litellm bug
        where any non-empty extra_headers without it makes litellm skip its own
        Copilot header injection entirely.
        """
        headers = copilot.github_copilot_extra_headers("github_copilot/claude-sonnet-5")
        assert "editor-version" in headers
        assert headers["editor-version"]

    def test_does_not_include_stale_authorization(self) -> None:
        """The placeholder Authorization header litellm's helper builds is stripped —
        litellm's own auth flow sets the real one later in the request pipeline.
        """
        headers = copilot.github_copilot_extra_headers("github_copilot/claude-sonnet-5")
        assert "Authorization" not in headers

    def test_case_insensitive_model_detection(self) -> None:
        """Header injection uses the same case-insensitive detection as is_github_copilot_model."""
        headers = copilot.github_copilot_extra_headers("GITHUB_COPILOT/gpt-4o")
        assert "editor-version" in headers


class TestApplyGithubCopilotHostnameEnv:
    def test_noop_without_hostname(self) -> None:
        """No hostname configured means litellm's github.com defaults apply untouched."""
        copilot.apply_github_copilot_hostname_env()
        for key in _COPILOT_ENV_KEYS:
            assert key not in os.environ

    def test_derives_urls_from_explicit_hostname(self) -> None:
        """A single hostname arg derives all four litellm URL env vars."""
        copilot.apply_github_copilot_hostname_env("octodemo.ghe.com")

        assert os.environ["GITHUB_COPILOT_DEVICE_CODE_URL"] == (
            "https://octodemo.ghe.com/login/device/code"
        )
        assert os.environ["GITHUB_COPILOT_ACCESS_TOKEN_URL"] == (
            "https://octodemo.ghe.com/login/oauth/access_token"
        )
        assert os.environ["GITHUB_COPILOT_API_KEY_URL"] == (
            "https://api.octodemo.ghe.com/copilot_internal/v2/token"
        )
        assert os.environ["GITHUB_COPILOT_API_BASE"] == "https://api.octodemo.ghe.com"
        assert os.environ["GITHUB_COPILOT_USER_API_URL"] == "https://api.octodemo.ghe.com/user"

    def test_derives_urls_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GITHUB_COPILOT_HOSTNAME env var is used when no explicit hostname is passed."""
        monkeypatch.setenv("GITHUB_COPILOT_HOSTNAME", "octodemo.ghe.com")
        copilot.apply_github_copilot_hostname_env()

        assert os.environ["GITHUB_COPILOT_API_BASE"] == "https://api.octodemo.ghe.com"

    def test_strips_scheme_and_trailing_slash(self) -> None:
        """Hostnames pasted with a scheme/trailing slash are normalized."""
        copilot.apply_github_copilot_hostname_env("https://octodemo.ghe.com/")

        assert os.environ["GITHUB_COPILOT_API_BASE"] == "https://api.octodemo.ghe.com"

    def test_does_not_override_explicit_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicitly-set litellm env vars always win over the derived hostname URLs."""
        monkeypatch.setenv("GITHUB_COPILOT_API_BASE", "https://api.custom.example.com")
        copilot.apply_github_copilot_hostname_env("octodemo.ghe.com")

        assert os.environ["GITHUB_COPILOT_API_BASE"] == "https://api.custom.example.com"
        # Unset vars are still derived from the hostname.
        assert os.environ["GITHUB_COPILOT_DEVICE_CODE_URL"] == (
            "https://octodemo.ghe.com/login/device/code"
        )


class TestGithubCopilotTokenPaths:
    def test_default_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default token paths mirror litellm's own Authenticator defaults."""
        monkeypatch.delenv("GITHUB_COPILOT_TOKEN_DIR", raising=False)
        access_token_path, api_key_path = copilot.github_copilot_token_paths()
        assert access_token_path.name == "access-token"
        assert api_key_path.name == "api-key.json"
        assert access_token_path.parent == api_key_path.parent
        assert str(access_token_path.parent).endswith(".config/litellm/github_copilot")

    def test_custom_token_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """GITHUB_COPILOT_TOKEN_DIR relocates both cached token files."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        access_token_path, api_key_path = copilot.github_copilot_token_paths()
        assert access_token_path.parent == tmp_path
        assert api_key_path.parent == tmp_path


class TestHasCachedGithubCopilotToken:
    def test_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No cached file at all means no token."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        assert copilot.has_cached_github_copilot_token() is False

    def test_empty_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty (or whitespace-only) cached file counts as no token."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        (tmp_path / "access-token").write_text("   \n")
        assert copilot.has_cached_github_copilot_token() is False

    def test_populated_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-empty cached file is treated as a present token."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        (tmp_path / "access-token").write_text("ghu_faketoken123")
        assert copilot.has_cached_github_copilot_token() is True


class TestClearGithubCopilotTokens:
    def test_removes_both_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both the access-token and api-key.json caches are removed."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        (tmp_path / "access-token").write_text("ghu_faketoken123")
        (tmp_path / "api-key.json").write_text("{}")

        copilot.clear_github_copilot_tokens()

        assert not (tmp_path / "access-token").exists()
        assert not (tmp_path / "api-key.json").exists()

    def test_missing_files_do_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Clearing when nothing is cached yet is a safe no-op."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        copilot.clear_github_copilot_tokens()


class TestValidateGithubCopilotAccessToken:
    def test_no_cached_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No cached token is trivially invalid — no network call is made."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        assert copilot.validate_github_copilot_access_token() is False

    def test_valid_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 200 response from the user endpoint means the token is still valid."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        (tmp_path / "access-token").write_text("ghu_faketoken123")

        mock_response = MagicMock(status_code=200)
        with patch("httpx.get", return_value=mock_response) as mock_get:
            assert copilot.validate_github_copilot_access_token() is True
        mock_get.assert_called_once()

    def test_invalid_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-200 response means the token is no longer accepted."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        (tmp_path / "access-token").write_text("ghu_faketoken123")

        mock_response = MagicMock(status_code=401)
        with patch("httpx.get", return_value=mock_response):
            assert copilot.validate_github_copilot_access_token() is False

    def test_network_error_treated_as_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Any network failure is treated as an invalid token, not a crash."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        (tmp_path / "access-token").write_text("ghu_faketoken123")

        with patch("httpx.get", side_effect=OSError("boom")):
            assert copilot.validate_github_copilot_access_token() is False

    def test_uses_ghes_user_api_url(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """GITHUB_COPILOT_USER_API_URL is honored for GHE Cloud instances."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_COPILOT_USER_API_URL", "https://api.octodemo.ghe.com/user")
        (tmp_path / "access-token").write_text("ghu_faketoken123")

        mock_response = MagicMock(status_code=200)
        with patch("httpx.get", return_value=mock_response) as mock_get:
            copilot.validate_github_copilot_access_token()
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.octodemo.ghe.com/user"


class TestAuthenticateGithubCopilot:
    def test_reuses_valid_cached_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A valid cached token is not cleared before the authenticator runs."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        (tmp_path / "access-token").write_text("ghu_faketoken123")

        mock_authenticator = MagicMock()
        mock_authenticator.get_api_key.return_value = "fake-api-key"

        with (
            patch.object(copilot, "validate_github_copilot_access_token", return_value=True),
            patch.object(copilot, "_build_authenticator", return_value=mock_authenticator),
            patch.object(copilot, "clear_github_copilot_tokens") as mock_clear,
        ):
            result = copilot.authenticate_github_copilot()

        mock_clear.assert_not_called()
        mock_authenticator.get_access_token.assert_called_once()
        assert result == "fake-api-key"

    def test_clears_invalid_cached_token_before_login(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An invalid cached token is cleared so a fresh device-code login runs."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        (tmp_path / "access-token").write_text("ghu_faketoken123")

        mock_authenticator = MagicMock()
        mock_authenticator.get_api_key.return_value = "fake-api-key"

        with (
            patch.object(copilot, "validate_github_copilot_access_token", return_value=False),
            patch.object(copilot, "_build_authenticator", return_value=mock_authenticator),
            patch.object(copilot, "clear_github_copilot_tokens") as mock_clear,
        ):
            copilot.authenticate_github_copilot()

        mock_clear.assert_called_once()

    def test_force_always_clears(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """force=True clears cached tokens even if they would otherwise validate."""
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
        (tmp_path / "access-token").write_text("ghu_faketoken123")

        mock_authenticator = MagicMock()
        mock_authenticator.get_api_key.return_value = "fake-api-key"

        with (
            patch.object(copilot, "validate_github_copilot_access_token", return_value=True),
            patch.object(copilot, "_build_authenticator", return_value=mock_authenticator),
            patch.object(copilot, "clear_github_copilot_tokens") as mock_clear,
        ):
            copilot.authenticate_github_copilot(force=True)

        mock_clear.assert_called_once()

    def test_applies_hostname_before_authenticating(self) -> None:
        """The GHE Cloud hostname is applied before the authenticator is built."""
        mock_authenticator = MagicMock()
        mock_authenticator.get_api_key.return_value = "fake-api-key"

        with (
            patch.object(copilot, "apply_github_copilot_hostname_env") as mock_apply,
            patch.object(copilot, "_build_authenticator", return_value=mock_authenticator),
        ):
            copilot.authenticate_github_copilot(hostname="octodemo.ghe.com")

        mock_apply.assert_called_once_with("octodemo.ghe.com")


class TestBuildAuthenticatorPatchedPoll:
    def test_honors_device_code_interval_and_expiry(self) -> None:
        """The patched poll loop uses the device-code response's own interval/expiry."""
        authenticator = copilot._build_authenticator()

        with (
            patch("time.sleep") as mock_sleep,
            patch("httpx.Client") as mock_client_cls,
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"access_token": "ghu_realtoken"}
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__enter__.return_value = mock_client

            token = authenticator._patched_poll("device-code-123", interval=1, expires_in=3)

        assert token == "ghu_realtoken"
        # First attempt already returns the token, so no sleep should occur.
        mock_sleep.assert_not_called()

    def test_raises_after_expiry_window(self) -> None:
        """Polling stops (and raises) once the device-code's expiry window elapses."""

        authenticator = copilot._build_authenticator()

        with (
            patch("time.sleep"),
            patch("httpx.Client") as mock_client_cls,
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"error": "authorization_pending"}
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__enter__.return_value = mock_client

            with pytest.raises(GetAccessTokenError):
                authenticator._patched_poll("device-code-123", interval=1, expires_in=2)
