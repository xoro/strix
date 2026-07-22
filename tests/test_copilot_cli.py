"""Tests for the --auth-github-copilot / --github-copilot-host CLI wiring."""

from __future__ import annotations

import importlib
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


cli_main: Any = importlib.import_module("strix.interface.main")


def test_auth_github_copilot_bypasses_target_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    """--auth-github-copilot does not require --target/--target-list/--mount."""
    monkeypatch.setattr(sys, "argv", ["strix", "--auth-github-copilot"])
    args = cli_main.parse_arguments()
    assert args.auth_github_copilot is True
    assert args.github_copilot_host is None


def test_auth_github_copilot_accepts_host_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """--github-copilot-host is parsed alongside --auth-github-copilot."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["strix", "--auth-github-copilot", "--github-copilot-host", "octodemo.ghe.com"],
    )
    args = cli_main.parse_arguments()
    assert args.auth_github_copilot is True
    assert args.github_copilot_host == "octodemo.ghe.com"


def test_plain_target_run_does_not_set_auth_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal scan invocation leaves auth_github_copilot False."""
    monkeypatch.setattr(
        cli_main,
        "load_settings",
        lambda: __import__("types").SimpleNamespace(
            runtime=__import__("types").SimpleNamespace(max_local_copy_mb=1024)
        ),
    )
    monkeypatch.setattr(sys, "argv", ["strix", "--target", "https://example.com", "-n"])
    args = cli_main.parse_arguments()
    assert args.auth_github_copilot is False


def test_run_github_copilot_auth_command_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command applies the hostname, authenticates, and prints success — no sys.exit."""
    monkeypatch.setattr(cli_main, "has_cached_github_copilot_token", lambda: False)
    monkeypatch.setattr(cli_main, "authenticate_github_copilot", MagicMock())
    monkeypatch.setattr(
        cli_main, "github_copilot_token_paths", lambda: (__import__("pathlib").Path("/x"), None)
    )
    mock_apply = MagicMock()
    monkeypatch.setattr(cli_main, "apply_github_copilot_hostname_env", mock_apply)

    cli_main._run_github_copilot_auth_command("octodemo.ghe.com")

    mock_apply.assert_called_once_with("octodemo.ghe.com")


def test_run_github_copilot_auth_command_exits_nonzero_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed device-code login exits non-zero with a clear error panel."""
    monkeypatch.setattr(cli_main, "has_cached_github_copilot_token", lambda: False)
    monkeypatch.setattr(cli_main, "apply_github_copilot_hostname_env", MagicMock())

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("device flow failed")

    monkeypatch.setattr(cli_main, "authenticate_github_copilot", _raise)

    with pytest.raises(SystemExit) as exc_info:
        cli_main._run_github_copilot_auth_command(None)
    assert exc_info.value.code == 1


def test_run_github_copilot_auth_command_reports_valid_cached_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-valid cached token is reused without forcing a fresh login."""
    monkeypatch.setattr(cli_main, "has_cached_github_copilot_token", lambda: True)
    monkeypatch.setattr(cli_main, "validate_github_copilot_access_token", lambda: True)
    monkeypatch.setattr(cli_main, "apply_github_copilot_hostname_env", MagicMock())
    mock_auth = MagicMock()
    monkeypatch.setattr(cli_main, "authenticate_github_copilot", mock_auth)
    monkeypatch.setattr(
        cli_main, "github_copilot_token_paths", lambda: (__import__("pathlib").Path("/x"), None)
    )

    cli_main._run_github_copilot_auth_command(None)

    mock_auth.assert_called_once_with(hostname=None)


def test_main_dispatches_auth_command_before_docker_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`strix --auth-github-copilot` must not require Docker to be installed."""
    monkeypatch.setattr(sys, "argv", ["strix", "--auth-github-copilot"])
    mock_auth_command = MagicMock()
    monkeypatch.setattr(cli_main, "_run_github_copilot_auth_command", mock_auth_command)

    with patch.object(cli_main, "check_docker_installed") as mock_check_docker:
        cli_main.main()

    mock_check_docker.assert_not_called()
    mock_auth_command.assert_called_once_with(None)
