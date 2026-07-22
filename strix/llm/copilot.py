"""GitHub Copilot integration helpers.

litellm's ``github_copilot`` provider (a first-class litellm provider, not a Strix
addition) already implements the entire OAuth device-code flow and token caching /
refresh (``litellm.llms.github_copilot.authenticator.Authenticator`` /
``GithubCopilotConfig``). ``STRIX_LLM=github_copilot/<model>`` routes through
:class:`strix.config.models.StrixProvider` straight to litellm, which handles auth
completely on its own — Strix does not need to manage tokens itself.

This module adds three things litellm does not provide out of the box:

- **GitHub Enterprise Server / GHE Cloud (``*.ghe.com``) support.** litellm's
  ``Authenticator`` supports pointing at a non-github.com instance, but only via
  four separate env vars (``GITHUB_COPILOT_DEVICE_CODE_URL``,
  ``GITHUB_COPILOT_ACCESS_TOKEN_URL``, ``GITHUB_COPILOT_API_KEY_URL``,
  ``GITHUB_COPILOT_API_BASE``). :func:`apply_github_copilot_hostname_env` derives
  all four from a single ``GITHUB_COPILOT_HOSTNAME`` (e.g. ``octodemo.ghe.com``).
- **A patched device-code poll window.** litellm hardcodes a 60-second
  (12 x 5s) poll timeout that ignores the ``interval``/``expires_in`` fields
  GitHub's device-code endpoint actually returns. Enterprise orgs may configure
  longer windows, so :class:`_PatchedAuthenticator` honors them instead.
- **A workaround for a real litellm request-header bug.** litellm's
  ``GithubCopilotConfig.validate_environment()`` normally injects the required
  Copilot request headers (``editor-version``, ``x-github-api-version``, etc.),
  but only when the caller passes *no* ``extra_headers`` at all. The
  ``openai-agents`` SDK's ``LitellmModel`` always passes a non-empty
  ``extra_headers`` dict (at minimum a ``User-Agent``,
  see ``agents.models.chatcmpl_helpers.HEADERS``), which causes litellm to skip
  ``validate_environment()`` entirely and send the request with only
  ``Authorization`` + the caller's headers — the server then rejects it with
  ``"missing Editor-Version header for IDE auth"``. Confirmed by inspecting the
  actual outgoing request: passing any non-empty ``extra_headers`` without an
  ``editor-version`` key drops every Copilot-specific header litellm would
  otherwise add. :func:`github_copilot_extra_headers` pre-fills those headers so
  they're present regardless of what else ends up in ``extra_headers``.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast


if TYPE_CHECKING:
    from litellm.llms.github_copilot.authenticator import Authenticator


DEFAULT_GITHUB_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"


def is_github_copilot_model(model_name: str | None) -> bool:
    """Return whether *model_name* targets litellm's ``github_copilot`` provider."""
    if not model_name:
        return False
    return model_name.strip().lower().startswith("github_copilot/")


def github_copilot_extra_headers(model_name: str | None) -> dict[str, str]:
    """Return the request headers litellm's ``github_copilot`` provider needs.

    Works around the litellm bug described in this module's docstring: when the
    caller (the SDK, always) passes a non-empty ``extra_headers``, litellm skips
    ``GithubCopilotConfig.validate_environment()`` and never adds these itself.
    Pre-filling them here means they're present no matter what else the SDK adds
    to ``extra_headers``. Returns ``{}`` for non-Copilot models.

    Reuses litellm's own header values (not hardcoded copies) so this stays in
    sync automatically if litellm changes its default editor/user-agent strings.
    """
    if not is_github_copilot_model(model_name):
        return {}

    from litellm.llms.github_copilot.common_utils import (
        get_copilot_default_headers,  # pyright: ignore[reportUnknownVariableType]
    )

    # get_copilot_default_headers() also sets Authorization from the api key we pass
    # it, but litellm's own auth flow already sets the real Authorization header
    # later in the request pipeline — drop it here so we don't ship a stale one.
    headers = cast("dict[str, str]", get_copilot_default_headers(""))
    headers.pop("Authorization", None)
    return headers


def apply_github_copilot_hostname_env(hostname: str | None = None) -> None:
    """Derive litellm's Copilot URL env vars from a single GHE Cloud hostname.

    ``GITHUB_COPILOT_HOSTNAME`` (e.g. ``octodemo.ghe.com``) is a Strix-only
    convenience env var; litellm itself only understands the four URL env vars
    set below. Values the user (or an earlier call) already set explicitly are
    never overridden — this only fills in gaps via ``setdefault``. A no-op when
    no hostname is configured (the default github.com Copilot endpoints apply).
    """
    host = (hostname or os.getenv("GITHUB_COPILOT_HOSTNAME") or "").strip()
    if not host:
        return
    host = host.removeprefix("https://").removeprefix("http://").rstrip("/")

    os.environ.setdefault("GITHUB_COPILOT_DEVICE_CODE_URL", f"https://{host}/login/device/code")
    os.environ.setdefault(
        "GITHUB_COPILOT_ACCESS_TOKEN_URL", f"https://{host}/login/oauth/access_token"
    )
    os.environ.setdefault(
        "GITHUB_COPILOT_API_KEY_URL", f"https://api.{host}/copilot_internal/v2/token"
    )
    os.environ.setdefault("GITHUB_COPILOT_API_BASE", f"https://api.{host}")
    os.environ.setdefault("GITHUB_COPILOT_USER_API_URL", f"https://api.{host}/user")


def github_copilot_token_paths() -> tuple[Path, Path]:
    """Return the ``(access_token_path, api_key_path)`` litellm caches Copilot tokens at."""
    token_dir = Path(
        os.getenv(
            "GITHUB_COPILOT_TOKEN_DIR",
            str(Path.home() / ".config" / "litellm" / "github_copilot"),
        )
    )
    access_token_path = token_dir / os.getenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", "access-token")
    api_key_path = token_dir / os.getenv("GITHUB_COPILOT_API_KEY_FILE", "api-key.json")
    return access_token_path, api_key_path


def has_cached_github_copilot_token() -> bool:
    """Return whether a (not necessarily still-valid) cached access token exists on disk."""
    access_token_path, _ = github_copilot_token_paths()
    try:
        return bool(access_token_path.read_text().strip())
    except OSError:
        return False


def clear_github_copilot_tokens() -> None:
    """Remove cached Copilot tokens so the next call re-runs the device-code flow."""
    for path in github_copilot_token_paths():
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def validate_github_copilot_access_token(timeout: float = 10.0) -> bool:
    """Return whether the cached GitHub OAuth access token is still accepted by GitHub.

    litellm itself never checks this: it trusts the cached access token file until a
    request actually fails. This does one lightweight ``GET /user`` call so Strix can
    give a clear "token expired, re-authenticating" message up front instead of
    surfacing a generic connection failure later.
    """
    access_token_path, _ = github_copilot_token_paths()
    try:
        token = access_token_path.read_text().strip()
    except OSError:
        return False
    if not token:
        return False

    import httpx

    user_api_url = os.getenv("GITHUB_COPILOT_USER_API_URL", "https://api.github.com/user")
    try:
        resp = httpx.get(
            user_api_url,
            headers={"Authorization": f"token {token}", "Accept": "application/json"},
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001
        return False
    return resp.status_code == 200


def _build_authenticator() -> Authenticator:
    """Return a litellm ``Authenticator`` that honors GitHub's own poll timing.

    litellm's stock ``_poll_for_access_token`` hardcodes a 60-second window
    (``max_attempts=12``, 5s sleep) regardless of what the device-code response
    says. GHE Cloud orgs can configure a longer device-code expiry, so this
    subclass reads ``interval``/``expires_in`` from the device-code response
    instead, falling back to litellm's own defaults when absent.
    """
    from litellm.llms.github_copilot.authenticator import (
        DEFAULT_GITHUB_ACCESS_TOKEN_URL,
        Authenticator,
    )
    from litellm.llms.github_copilot.common_utils import GetAccessTokenError

    class _PatchedAuthenticator(Authenticator):
        def _poll_for_access_token(
            self, device_code: str, *_args: object, **_kwargs: object
        ) -> str:
            return self._patched_poll(device_code)

        def _patched_poll(self, device_code: str, interval: int = 5, expires_in: int = 900) -> str:
            import httpx

            access_token_url = os.getenv(
                "GITHUB_COPILOT_ACCESS_TOKEN_URL", DEFAULT_GITHUB_ACCESS_TOKEN_URL
            )
            client_id = os.getenv("GITHUB_COPILOT_CLIENT_ID", DEFAULT_GITHUB_COPILOT_CLIENT_ID)
            max_attempts = max(1, expires_in // max(1, interval))

            with httpx.Client() as sync_client:
                for _attempt in range(max_attempts):
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
                            return str(resp_json["access_token"])
                    except httpx.HTTPStatusError as exc:
                        raise GetAccessTokenError(
                            message=f"Failed to get access token: {exc}",
                            status_code=400,
                        ) from exc
                    time.sleep(interval)

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
            return self._patched_poll(device_code, interval=interval, expires_in=expires_in)

    return _PatchedAuthenticator()


def authenticate_github_copilot(*, force: bool = False, hostname: str | None = None) -> str:
    """Run (or reuse) the GitHub Copilot device-code login and return the Copilot API key.

    Args:
        force: Clear any cached tokens first, forcing a fresh device-code login
            even if a token is already cached.
        hostname: GHE Cloud hostname (e.g. ``octodemo.ghe.com``). Overrides
            ``GITHUB_COPILOT_HOSTNAME`` for this call only; does not mutate the
            environment beyond what :func:`apply_github_copilot_hostname_env` sets.

    Raises:
        Whatever litellm's ``Authenticator`` raises on failure
        (``litellm.llms.github_copilot.common_utils.GithubCopilotError`` subclasses).
    """
    apply_github_copilot_hostname_env(hostname)
    if force or (has_cached_github_copilot_token() and not validate_github_copilot_access_token()):
        clear_github_copilot_tokens()

    authenticator = _build_authenticator()
    authenticator.get_access_token()
    return authenticator.get_api_key()
