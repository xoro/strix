"""GitHub Copilot helpers for litellm chat completions.

litellm's github_copilot chat path inherits from OpenAIConfig and only sets
the Authorization header.  The Copilot API also requires editor-version,
copilot-integration-id and several other headers that are only injected by
litellm for the *Responses* API path.  We provide them as ``extra_headers``
so every litellm.completion / acompletion call works correctly.

Additionally, litellm converts ``system`` messages to ``assistant`` by default
for Copilot, which causes "assistant message prefill" errors on Claude models.
Call :func:`configure_copilot_litellm` once at startup to disable that.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from strix.config import Config


_COPILOT_VERSION = "0.26.7"


def _is_github_copilot_model(model_name: str | None = None) -> bool:
    name = model_name or Config.get("strix_llm") or ""
    return name.lower().startswith("github_copilot/")


def configure_copilot_litellm() -> None:
    """Set litellm globals needed for GitHub Copilot compatibility.

    Must be called once before the first litellm completion call when using a
    ``github_copilot/`` model.  Safe to call for non-Copilot models (no-op).
    """
    if not _is_github_copilot_model():
        return

    import litellm

    litellm.disable_copilot_system_to_assistant = True

    # Patch model cost map with github_copilot/ entries that litellm does not
    # yet ship but are available on the GHES Copilot API endpoint.  Each entry
    # is cloned from the nearest equivalent already present in the map.
    _COPILOT_MODEL_FALLBACKS: dict[str, str] = {
        "github_copilot/claude-opus-4.6": "github_copilot/claude-opus-4.5",
        "github_copilot/claude-opus-4.7": "github_copilot/claude-opus-4.5",
        "github_copilot/claude-sonnet-4.6": "github_copilot/claude-sonnet-4.5",
        "github_copilot/gemini-3-flash-preview": "github_copilot/gemini-2.5-pro",
        "github_copilot/gemini-3.1-pro-preview": "github_copilot/gemini-2.5-pro",
        "github_copilot/gpt-4-0125-preview": "github_copilot/gpt-4o",
        "github_copilot/gpt-5.2-codex": "github_copilot/gpt-5.2",
        "github_copilot/gpt-5.4": "github_copilot/gpt-5.3-codex",
        "github_copilot/gpt-5.4-mini": "github_copilot/gpt-5-mini",
        "github_copilot/gpt-5.5": "github_copilot/gpt-5.3-codex",
        "github_copilot/grok-code-fast-1": "github_copilot/gpt-4o",
    }
    for model, fallback in _COPILOT_MODEL_FALLBACKS.items():
        if model not in litellm.model_cost and fallback in litellm.model_cost:
            litellm.model_cost[model] = litellm.model_cost[fallback].copy()


def get_copilot_extra_headers() -> dict[str, str]:
    """Return the headers the Copilot chat API requires beyond Authorization."""
    return {
        "copilot-integration-id": "vscode-chat",
        "editor-version": "vscode/1.95.0",
        "editor-plugin-version": f"copilot-chat/{_COPILOT_VERSION}",
        "user-agent": f"GitHubCopilotChat/{_COPILOT_VERSION}",
        "openai-intent": "conversation-panel",
        "x-github-api-version": "2025-04-01",
        "x-request-id": str(uuid4()),
        "x-vscode-user-agent-library-version": "electron-fetch",
    }


def maybe_copilot_headers(model_name: str | None = None) -> dict[str, Any]:
    """Return ``{"extra_headers": ...}`` for Copilot models, empty dict otherwise.

    Designed to be unpacked into litellm completion kwargs::

        litellm.completion(model=model, messages=msgs, **maybe_copilot_headers(model))
    """
    if _is_github_copilot_model(model_name):
        return {"extra_headers": get_copilot_extra_headers()}
    return {}
