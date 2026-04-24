#!/usr/bin/env python3
"""
Strix Agent Interface
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
from datetime import UTC
from pathlib import Path
from typing import Any

import litellm
from docker.errors import DockerException
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from strix.config import Config, apply_saved_config, save_current_config
from strix.config.config import resolve_llm_config
from strix.llm.utils import resolve_strix_model


apply_saved_config()

from strix.interface.cli import run_cli  # noqa: E402
from strix.interface.tui import run_tui  # noqa: E402
from strix.interface.utils import (  # noqa: E402
    assign_workspace_subdirs,
    build_final_stats_text,
    check_docker_connection,
    clone_repository,
    collect_local_sources,
    generate_run_name,
    image_exists,
    infer_target_type,
    process_pull_line,
    resolve_diff_scope_context,
    rewrite_localhost_targets,
    validate_config_file,
    validate_llm_response,
)
from strix.runtime.docker_runtime import HOST_GATEWAY_HOSTNAME  # noqa: E402
from strix.telemetry import posthog  # noqa: E402
from strix.telemetry.tracer import get_global_tracer  # noqa: E402
from strix.utils.container_platform import linux_container_platform  # noqa: E402


logging.getLogger().setLevel(logging.ERROR)


def _is_github_copilot_model(model_name: str | None = None) -> bool:
    name = model_name or Config.get("strix_llm") or ""
    return name.lower().startswith("github_copilot/")


def _get_github_copilot_token_path() -> Path:
    token_dir = os.getenv(
        "GITHUB_COPILOT_TOKEN_DIR",
        str(Path.home() / ".config/litellm/github_copilot"),
    )
    return Path(token_dir) / os.getenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")


def _has_github_copilot_token() -> bool:
    token_path = _get_github_copilot_token_path()
    if not token_path.exists():
        return False
    try:
        return bool(token_path.read_text().strip())
    except OSError:
        return False


def _validate_github_copilot_token() -> bool:
    """Check whether the stored GitHub Copilot access token is still valid.

    Returns ``True`` when the token is accepted by GitHub, ``False`` otherwise.
    """
    token_path = _get_github_copilot_token_path()
    try:
        token = token_path.read_text().strip()
        if not token:
            return False
    except OSError:
        return False

    try:
        import httpx

        user_api_url = os.getenv("GITHUB_COPILOT_USER_API_URL", "https://api.github.com/user")
        resp = httpx.get(
            user_api_url,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/json",
            },
            timeout=10,
        )
    except Exception:  # noqa: BLE001
        return False
    else:
        return resp.status_code == 200


def _clear_github_copilot_tokens() -> None:
    """Remove cached GitHub Copilot token files so a fresh login is triggered."""
    import contextlib

    token_path = _get_github_copilot_token_path()
    api_key_path = token_path.parent / os.getenv("GITHUB_COPILOT_API_KEY_FILE", "api-key.json")
    for path in (token_path, api_key_path):
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def authenticate_github_copilot() -> None:  # noqa: PLR0915
    console = Console()

    if _has_github_copilot_token():
        console.print()
        console.print("[dim]Existing GitHub Copilot token found.[/]")
        console.print("[dim]Validating token...[/]")
        if _validate_github_copilot_token():
            console.print("[dim]Token is still valid.[/]")
        else:
            console.print("[dim yellow]Token is expired or invalid. Clearing cached tokens...[/]")
            _clear_github_copilot_tokens()
            console.print("[dim]Starting fresh authentication...[/]")
        console.print()

    try:
        import time as _time

        from litellm.llms.github_copilot.authenticator import Authenticator
        from litellm.llms.github_copilot.common_utils import GetAccessTokenError

        class _GHESAuthenticator(Authenticator):
            """Authenticator subclass that respects expires_in/interval from the
            device code response instead of the upstream 60-second hard cap."""

            def _poll_for_access_token(
                self,
                device_code: str,
                interval: int = 5,
                expires_in: int = 900,
            ) -> str:
                import httpx
                from litellm.llms.custom_httpx.http_handler import _get_httpx_client

                sync_client = _get_httpx_client()
                max_attempts = max(1, expires_in // max(1, interval))
                access_token_url = os.getenv(
                    "GITHUB_COPILOT_ACCESS_TOKEN_URL",
                    "https://github.com/login/oauth/access_token",
                )
                client_id = os.getenv(
                    "GITHUB_COPILOT_CLIENT_ID", "Iv1.b507a08c87ecfe98"
                )
                for attempt in range(max_attempts):
                    try:
                        resp = sync_client.post(
                            access_token_url,
                            headers=self._get_github_headers(),
                            json={
                                "client_id": client_id,
                                "device_code": device_code,
                                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                            },
                        )
                        resp.raise_for_status()
                        resp_json = resp.json()
                        if "access_token" in resp_json:
                            return resp_json["access_token"]
                        elif resp_json.get("error") != "authorization_pending":
                            pass  # unexpected response, keep polling
                    except httpx.HTTPStatusError as exc:
                        raise GetAccessTokenError(
                            message=f"Failed to get access token: {exc}",
                            status_code=400,
                        ) from exc
                    _time.sleep(interval)
                raise GetAccessTokenError(
                    message="Timed out waiting for user to authorize the device",
                    status_code=400,
                )

            def _login(self) -> str:
                device_code_info = self._get_device_code()
                device_code = device_code_info["device_code"]
                user_code = device_code_info["user_code"]
                verification_uri = device_code_info["verification_uri"]
                interval = int(device_code_info.get("interval", 5))
                expires_in = int(device_code_info.get("expires_in", 900))
                print(  # noqa: T201
                    f"Please visit {verification_uri} and enter code {user_code} to authenticate.",
                    flush=True,
                )
                return self._poll_for_access_token(
                    device_code, interval=interval, expires_in=expires_in
                )

        auth = _GHESAuthenticator()
        auth.get_access_token()
    except Exception as e:  # noqa: BLE001
        error_text = Text()
        error_text.append("GITHUB COPILOT AUTHENTICATION FAILED", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append(f"Error: {e}", style="dim white")

        panel = Panel(
            error_text,
            title="[bold white]STRIX",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )
        console.print("\n")
        console.print(panel)
        console.print()
        sys.exit(1)

    console.print()

    success_text = Text()
    success_text.append("GitHub Copilot authentication successful", style="bold #22c55e")
    success_text.append("\n\n", style="white")
    success_text.append("Token stored at: ", style="white")
    success_text.append(str(_get_github_copilot_token_path()), style="#60a5fa")
    success_text.append("\n\n", style="white")
    success_text.append("You can now use GitHub Copilot as your LLM provider:\n", style="white")
    success_text.append(
        "  export STRIX_LLM='github_copilot/gpt-4o'\n",
        style="dim white",
    )
    success_text.append(
        "  strix --target https://example.com",
        style="dim white",
    )

    panel = Panel(
        success_text,
        title="[bold white]STRIX",
        title_align="left",
        border_style="#22c55e",
        padding=(1, 2),
    )
    console.print(panel)
    console.print()

    try:
        api_key = auth.get_api_key()
        if api_key:
            token_path = _get_github_copilot_token_path()
            api_key_path = token_path.parent / os.getenv(
                "GITHUB_COPILOT_API_KEY_FILE", "api-key.json"
            )
            if api_key_path.exists():
                with api_key_path.open() as f:
                    api_key_info = json.load(f)
                    expires_at = api_key_info.get("expires_at", 0)
                    if expires_at:
                        from datetime import datetime

                        exp_time = datetime.fromtimestamp(expires_at, tz=UTC)
                        exp_str = exp_time.strftime("%Y-%m-%d %H:%M:%S UTC")
                        console.print(f"[dim]API key valid until: {exp_str}[/]")
                        console.print()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).debug("Failed to display API key info", exc_info=True)


def validate_environment() -> None:  # noqa: PLR0912, PLR0915
    console = Console()
    missing_required_vars = []
    missing_optional_vars = []

    strix_llm = Config.get("strix_llm")
    uses_strix_models = strix_llm and strix_llm.startswith("strix/")
    is_copilot = _is_github_copilot_model()

    if not strix_llm:
        missing_required_vars.append("STRIX_LLM")

    has_base_url = uses_strix_models or any(
        [
            Config.get("llm_api_base"),
            Config.get("openai_api_base"),
            Config.get("litellm_base_url"),
            Config.get("ollama_api_base"),
        ]
    )

    if not Config.get("llm_api_key") and not is_copilot:
        missing_optional_vars.append("LLM_API_KEY")

    if not has_base_url:
        missing_optional_vars.append("LLM_API_BASE")

    if not Config.get("perplexity_api_key"):
        missing_optional_vars.append("PERPLEXITY_API_KEY")

    if not Config.get("strix_reasoning_effort"):
        missing_optional_vars.append("STRIX_REASONING_EFFORT")

    if missing_required_vars:
        error_text = Text()
        error_text.append("MISSING REQUIRED ENVIRONMENT VARIABLES", style="bold red")
        error_text.append("\n\n", style="white")

        for var in missing_required_vars:
            error_text.append(f"• {var}", style="bold yellow")
            error_text.append(" is not set\n", style="white")

        if missing_optional_vars:
            error_text.append("\nOptional environment variables:\n", style="dim white")
            for var in missing_optional_vars:
                error_text.append(f"• {var}", style="dim yellow")
                error_text.append(" is not set\n", style="dim white")

        error_text.append("\nRequired environment variables:\n", style="white")
        for var in missing_required_vars:
            if var == "STRIX_LLM":
                error_text.append("• ", style="white")
                error_text.append("STRIX_LLM", style="bold cyan")
                error_text.append(
                    " - Model name to use with litellm (e.g., 'openai/gpt-5.4')\n",
                    style="white",
                )

        if missing_optional_vars:
            error_text.append("\nOptional environment variables:\n", style="white")
            for var in missing_optional_vars:
                if var == "LLM_API_KEY":
                    error_text.append("• ", style="white")
                    error_text.append("LLM_API_KEY", style="bold cyan")
                    error_text.append(
                        " - API key for the LLM provider "
                        "(not needed for local models, Vertex AI, AWS, etc.)\n",
                        style="white",
                    )
                elif var == "LLM_API_BASE":
                    error_text.append("• ", style="white")
                    error_text.append("LLM_API_BASE", style="bold cyan")
                    error_text.append(
                        " - Custom API base URL if using local models (e.g., Ollama, LMStudio)\n",
                        style="white",
                    )
                elif var == "PERPLEXITY_API_KEY":
                    error_text.append("• ", style="white")
                    error_text.append("PERPLEXITY_API_KEY", style="bold cyan")
                    error_text.append(
                        " - API key for Perplexity AI web search (enables real-time research)\n",
                        style="white",
                    )
                elif var == "STRIX_REASONING_EFFORT":
                    error_text.append("• ", style="white")
                    error_text.append("STRIX_REASONING_EFFORT", style="bold cyan")
                    error_text.append(
                        " - Reasoning effort level: none, minimal, low, medium, high, xhigh "
                        "(default: high)\n",
                        style="white",
                    )

        error_text.append("\nExample setup:\n", style="white")
        error_text.append("export STRIX_LLM='openai/gpt-5.4'\n", style="dim white")

        if missing_optional_vars:
            for var in missing_optional_vars:
                if var == "LLM_API_KEY":
                    error_text.append(
                        "export LLM_API_KEY='your-api-key-here'  "
                        "# not needed for local models, Vertex AI, AWS, etc.\n",
                        style="dim white",
                    )
                elif var == "LLM_API_BASE":
                    error_text.append(
                        "export LLM_API_BASE='http://localhost:11434'  "
                        "# needed for local models only\n",
                        style="dim white",
                    )
                elif var == "PERPLEXITY_API_KEY":
                    error_text.append(
                        "export PERPLEXITY_API_KEY='your-perplexity-key-here'\n", style="dim white"
                    )
                elif var == "STRIX_REASONING_EFFORT":
                    error_text.append(
                        "export STRIX_REASONING_EFFORT='high'\n",
                        style="dim white",
                    )

        panel = Panel(
            error_text,
            title="[bold white]STRIX",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )

        console.print("\n")
        console.print(panel)
        console.print()
        sys.exit(1)


def check_docker_installed() -> None:
    if shutil.which("docker") is not None:
        return
    if sys.platform.startswith("freebsd") and shutil.which("podman") is not None:
        return
    console = Console()
    error_text = Text()
    error_text.append("CONTAINER CLI NOT FOUND", style="bold red")
    error_text.append("\n\n", style="white")
    error_text.append(
        "Neither 'docker' nor (on FreeBSD) 'podman' was found in your PATH.\n", style="white"
    )
    error_text.append(
        "Install Docker or Podman and ensure the command is available, then try again.\n\n",
        style="white",
    )

    panel = Panel(
        error_text,
        title="[bold white]STRIX",
        title_align="left",
        border_style="red",
        padding=(1, 2),
    )
    console.print("\n", panel, "\n")
    sys.exit(1)


async def warm_up_llm() -> None:
    console = Console()

    if _is_github_copilot_model():
        if not _has_github_copilot_token():
            error_text = Text()
            error_text.append("GITHUB COPILOT NOT AUTHENTICATED", style="bold red")
            error_text.append("\n\n", style="white")
            error_text.append("No cached GitHub Copilot token found.\n", style="white")
            error_text.append("Run the following command to authenticate:\n\n", style="white")
            error_text.append("  strix --auth-github-copilot", style="bold cyan")

            panel = Panel(
                error_text,
                title="[bold white]STRIX",
                title_align="left",
                border_style="red",
                padding=(1, 2),
            )

            console.print("\n")
            console.print(panel)
            console.print()
            sys.exit(1)

        if not _validate_github_copilot_token():
            error_text = Text()
            error_text.append("GITHUB COPILOT TOKEN EXPIRED", style="bold red")
            error_text.append("\n\n", style="white")
            error_text.append(
                "Your cached GitHub Copilot token is expired or invalid.\n",
                style="white",
            )
            error_text.append("Run the following command to re-authenticate:\n\n", style="white")
            error_text.append("  strix --auth-github-copilot", style="bold cyan")

            panel = Panel(
                error_text,
                title="[bold white]STRIX",
                title_align="left",
                border_style="red",
                padding=(1, 2),
            )

            console.print("\n")
            console.print(panel)
            console.print()
            sys.exit(1)

    try:
        model_name, api_key, api_base = resolve_llm_config()
        litellm_model, _ = resolve_strix_model(model_name)
        litellm_model = litellm_model or model_name

        test_messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Reply with just 'OK'."},
        ]

        llm_timeout = int(Config.get("llm_timeout") or "300")

        completion_kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": test_messages,
            "timeout": llm_timeout,
        }
        if api_key:
            completion_kwargs["api_key"] = api_key
        if api_base:
            completion_kwargs["api_base"] = api_base

        from strix.llm.copilot import maybe_copilot_headers

        completion_kwargs.update(maybe_copilot_headers(model_name))

        response = litellm.completion(**completion_kwargs)

        validate_llm_response(response)

    except Exception as e:  # noqa: BLE001
        error_text = Text()
        error_text.append("LLM CONNECTION FAILED", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append("Could not establish connection to the language model.\n", style="white")
        error_text.append("Please check your configuration and try again.\n", style="white")
        error_text.append(f"\nError: {e}", style="dim white")

        if _is_github_copilot_model():
            error_text.append("\n\n", style="white")
            error_text.append(
                "Tip: Try re-authenticating with: strix --auth-github-copilot",
                style="dim yellow",
            )

        panel = Panel(
            error_text,
            title="[bold white]STRIX",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )

        console.print("\n")
        console.print(panel)
        console.print()
        sys.exit(1)


def get_version() -> str:
    try:
        from importlib.metadata import version

        return version("strix-agent")
    except Exception:  # noqa: BLE001
        return "unknown"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strix Multi-Agent Cybersecurity Penetration Testing Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Web application penetration test
  strix --target https://example.com

  # GitHub repository analysis
  strix --target https://github.com/user/repo
  strix --target git@github.com:user/repo.git

  # Local code analysis
  strix --target ./my-project

  # Domain penetration test
  strix --target example.com

  # IP address penetration test
  strix --target 192.168.1.42

  # Multiple targets (e.g., white-box testing with source and deployed app)
  strix --target https://github.com/user/repo --target https://example.com
  strix --target ./my-project --target https://staging.example.com --target https://prod.example.com

  # Custom instructions (inline)
  strix --target example.com --instruction "Focus on authentication vulnerabilities"

  # Custom instructions (from file)
  strix --target example.com --instruction-file ./instructions.txt
  strix --target https://app.com --instruction-file /path/to/detailed_instructions.md

  # Authenticate with GitHub Copilot (one-time setup)
  strix --auth-github-copilot
  export STRIX_LLM='github_copilot/gpt-4o'
  strix --target https://example.com
        """,
    )

    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"strix {get_version()}",
    )

    parser.add_argument(
        "--auth-github-copilot",
        action="store_true",
        help="Authenticate with GitHub Copilot via OAuth device flow. "
        "Run this once before using 'github_copilot/' models with STRIX_LLM.",
    )

    parser.add_argument(
        "-t",
        "--target",
        type=str,
        action="append",
        help="Target to test (URL, repository, local directory path, domain name, or IP address). "
        "Can be specified multiple times for multi-target scans.",
    )
    parser.add_argument(
        "--instruction",
        type=str,
        help="Custom instructions for the penetration test. This can be "
        "specific vulnerability types to focus on (e.g., 'Focus on IDOR and XSS'), "
        "testing approaches (e.g., 'Perform thorough authentication testing'), "
        "test credentials (e.g., 'Use the following credentials to access the app: "
        "admin:password123'), "
        "or areas of interest (e.g., 'Check login API endpoint for security issues').",
    )

    parser.add_argument(
        "--instruction-file",
        type=str,
        help="Path to a file containing detailed custom instructions for the penetration test. "
        "Use this option when you have lengthy or complex instructions saved in a file "
        "(e.g., '--instruction-file ./detailed_instructions.txt').",
    )

    parser.add_argument(
        "-n",
        "--non-interactive",
        action="store_true",
        help=(
            "Run in non-interactive mode (no TUI, exits on completion). "
            "Default is interactive mode with TUI."
        ),
    )

    parser.add_argument(
        "-m",
        "--scan-mode",
        type=str,
        choices=["quick", "standard", "deep"],
        default="deep",
        help=(
            "Scan mode: "
            "'quick' for fast CI/CD checks, "
            "'standard' for routine testing, "
            "'deep' for thorough security reviews (default). "
            "Default: deep."
        ),
    )

    parser.add_argument(
        "--scope-mode",
        type=str,
        choices=["auto", "diff", "full"],
        default="auto",
        help=(
            "Scope mode for code targets: "
            "'auto' enables PR diff-scope in CI/headless runs, "
            "'diff' forces changed-files scope, "
            "'full' disables diff-scope."
        ),
    )

    parser.add_argument(
        "--diff-base",
        type=str,
        help=(
            "Target branch or commit to compare against (e.g., origin/main). "
            "Defaults to the repository's default branch."
        ),
    )

    parser.add_argument(
        "--config",
        type=str,
        help="Path to a custom config file (JSON) to use instead of ~/.strix/cli-config.json",
    )

    args = parser.parse_args()

    if args.auth_github_copilot:
        return args

    if not args.target:
        parser.error("the following arguments are required: -t/--target")

    if args.instruction and args.instruction_file:
        parser.error(
            "Cannot specify both --instruction and --instruction-file. Use one or the other."
        )

    if args.instruction_file:
        instruction_path = Path(args.instruction_file)
        try:
            with instruction_path.open(encoding="utf-8") as f:
                args.instruction = f.read().strip()
                if not args.instruction:
                    parser.error(f"Instruction file '{instruction_path}' is empty")
        except Exception as e:  # noqa: BLE001
            parser.error(f"Failed to read instruction file '{instruction_path}': {e}")

    args.targets_info = []
    for target in args.target:
        try:
            target_type, target_dict = infer_target_type(target)

            if target_type == "local_code":
                display_target = target_dict.get("target_path", target)
            else:
                display_target = target

            args.targets_info.append(
                {"type": target_type, "details": target_dict, "original": display_target}
            )
        except ValueError:
            parser.error(f"Invalid target '{target}'")

    assign_workspace_subdirs(args.targets_info)
    rewrite_localhost_targets(args.targets_info, HOST_GATEWAY_HOSTNAME)

    return args


def display_completion_message(args: argparse.Namespace, results_path: Path) -> None:
    console = Console()
    tracer = get_global_tracer()

    scan_completed = False
    if tracer and tracer.scan_results:
        scan_completed = tracer.scan_results.get("scan_completed", False)

    completion_text = Text()
    if scan_completed:
        completion_text.append("Penetration test completed", style="bold #22c55e")
    else:
        completion_text.append("SESSION ENDED", style="bold #eab308")

    target_text = Text()
    target_text.append("Target", style="dim")
    target_text.append("  ")
    if len(args.targets_info) == 1:
        target_text.append(args.targets_info[0]["original"], style="bold white")
    else:
        target_text.append(f"{len(args.targets_info)} targets", style="bold white")
        for target_info in args.targets_info:
            target_text.append("\n        ")
            target_text.append(target_info["original"], style="white")

    stats_text = build_final_stats_text(tracer)

    panel_parts = [completion_text, "\n\n", target_text]

    if stats_text.plain:
        panel_parts.extend(["\n", stats_text])

    results_text = Text()
    results_text.append("\n")
    results_text.append("Output", style="dim")
    results_text.append("  ")
    results_text.append(str(results_path), style="#60a5fa")
    panel_parts.extend(["\n", results_text])

    panel_content = Text.assemble(*panel_parts)

    border_style = "#22c55e" if scan_completed else "#eab308"

    panel = Panel(
        panel_content,
        title="[bold white]STRIX",
        title_align="left",
        border_style=border_style,
        padding=(1, 2),
    )

    console.print("\n")
    console.print(panel)
    console.print()
    console.print("[#60a5fa]strix.ai[/]  [dim]·[/]  [#60a5fa]discord.gg/strix-ai[/]")
    console.print()


def pull_docker_image() -> None:
    console = Console()
    client = check_docker_connection()

    if image_exists(client, Config.get("strix_image")):  # type: ignore[arg-type]
        return

    console.print()
    console.print(f"[dim]Pulling image[/] {Config.get('strix_image')}")
    console.print("[dim yellow]This only happens on first run and may take a few minutes...[/]")
    console.print()

    with console.status("[bold cyan]Downloading image layers...", spinner="dots") as status:
        try:
            layers_info: dict[str, str] = {}
            last_update = ""

            for line in client.api.pull(
                Config.get("strix_image"),
                stream=True,
                decode=True,
                platform=linux_container_platform(),
            ):
                last_update = process_pull_line(line, layers_info, status, last_update)

        except DockerException as e:
            console.print()
            error_text = Text()
            error_text.append("FAILED TO PULL IMAGE", style="bold red")
            error_text.append("\n\n", style="white")
            error_text.append(f"Could not download: {Config.get('strix_image')}\n", style="white")
            error_text.append(str(e), style="dim red")

            panel = Panel(
                error_text,
                title="[bold white]STRIX",
                title_align="left",
                border_style="red",
                padding=(1, 2),
            )
            console.print(panel, "\n")
            sys.exit(1)

    success_text = Text()
    success_text.append("Docker image ready", style="#22c55e")
    console.print(success_text)
    console.print()


def apply_config_override(config_path: str) -> None:
    Config._config_file_override = validate_config_file(config_path)
    apply_saved_config(force=True)


def persist_config() -> None:
    if Config._config_file_override is None:
        save_current_config()


def main() -> None:  # noqa: PLR0912, PLR0915
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    args = parse_arguments()

    if args.auth_github_copilot:
        authenticate_github_copilot()
        return

    if args.config:
        apply_config_override(args.config)

    check_docker_installed()
    pull_docker_image()

    validate_environment()
    asyncio.run(warm_up_llm())

    persist_config()

    args.run_name = generate_run_name(args.targets_info)

    for target_info in args.targets_info:
        if target_info["type"] == "repository":
            repo_url = target_info["details"]["target_repo"]
            dest_name = target_info["details"].get("workspace_subdir")
            cloned_path = clone_repository(repo_url, args.run_name, dest_name)
            target_info["details"]["cloned_repo_path"] = cloned_path

    args.local_sources = collect_local_sources(args.targets_info)
    try:
        diff_scope = resolve_diff_scope_context(
            local_sources=args.local_sources,
            scope_mode=args.scope_mode,
            diff_base=args.diff_base,
            non_interactive=args.non_interactive,
        )
    except ValueError as e:
        console = Console()
        error_text = Text()
        error_text.append("DIFF SCOPE RESOLUTION FAILED", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append(str(e), style="white")

        panel = Panel(
            error_text,
            title="[bold white]STRIX",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )
        console.print("\n")
        console.print(panel)
        console.print()
        sys.exit(1)

    args.diff_scope = diff_scope.metadata
    if diff_scope.instruction_block:
        if args.instruction:
            args.instruction = f"{diff_scope.instruction_block}\n\n{args.instruction}"
        else:
            args.instruction = diff_scope.instruction_block

    is_whitebox = bool(args.local_sources)

    posthog.start(
        model=Config.get("strix_llm"),
        scan_mode=args.scan_mode,
        is_whitebox=is_whitebox,
        interactive=not args.non_interactive,
        has_instructions=bool(args.instruction),
    )

    exit_reason = "user_exit"
    try:
        if args.non_interactive:
            asyncio.run(run_cli(args))
        else:
            asyncio.run(run_tui(args))
    except KeyboardInterrupt:
        exit_reason = "interrupted"
    except Exception as e:
        exit_reason = "error"
        posthog.error("unhandled_exception", str(e))
        raise
    finally:
        tracer = get_global_tracer()
        if tracer:
            posthog.end(tracer, exit_reason=exit_reason)

    results_path = Path("strix_runs") / args.run_name
    display_completion_message(args, results_path)

    if args.non_interactive:
        tracer = get_global_tracer()
        if tracer and tracer.vulnerability_reports:
            sys.exit(2)


if __name__ == "__main__":
    main()
