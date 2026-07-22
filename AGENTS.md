# AGENTS.md

> Guide for AI agents working in this fork of Strix.

## Project Overview

**Strix** (`strix-agent`, upstream v1.2.0 base) is an open-source AI-powered penetration testing
agent. It uses LLMs to autonomously conduct security assessments by executing tools inside a
Docker sandbox (Kali Linux container with preinstalled security tools). Licensed Apache-2.0.
Python 3.12+.

**This fork adds exactly one thing on top of upstream: GitHub Copilot as an LLM provider**
(device-code OAuth, GitHub Enterprise Cloud support). There is no Podman/FreeBSD support in this
fork — `main` is based directly on upstream's `main` branch tree, plus the Copilot delta described
in [MERGE.md](MERGE.md). If you're looking for the previous (much larger) fork with Podman/FreeBSD
support and the old XML-tool-call architecture, see the `pre-merge-backup` branch — but note it
predates upstream's SDK migration described below and is not maintained.

---

## Architecture (upstream v1.2.0+)

Upstream migrated from a custom XML-tool-call harness + FastAPI sidecar to
**[`openai-agents`](https://github.com/openai/openai-agents-python) (the OpenAI Agents SDK)**, with
LiteLLM as the model backend (`openai-agents[litellm]`). This is a different architecture than
older Strix releases (pre-v1.0): there is no `strix/tools/registry.py`, no XML schema files, no
`strix/llm/llm.py`, and no `BaseAgent`/`AgentMeta` metaclass loop anymore. If you've worked on an
older Strix fork, expect most tool/agent-loop internals to be unfamiliar.

### Agent System

- `strix/core/runner.py` — top-level scan runner (`run_strix_scan`): builds the root agent,
  resolves scope/instructions, wires up hooks, and drives the SDK's `Runner`.
- `strix/core/execution.py` — the actual execution loop (`run_agent_loop`,
  `spawn_child_agent`, `respawn_subagents`) built on `agents.Runner` + `agents.RunConfig`.
- `strix/core/agents.py` — `AgentCoordinator`: SDK-native state for Strix's addressable
  multi-agent graph (root + spawned sub-agents), persisted so scans can resume.
- `strix/core/hooks.py` — `agents.lifecycle.RunHooks` subclasses (budget enforcement, usage
  reporting) that hook into the SDK's run lifecycle.
- `strix/core/sessions.py` — wraps the SDK's `SQLiteSession` for native conversation-history
  persistence/resume (`--resume <run_name>`).
- `strix/core/inputs.py` — pure builders for `ModelSettings`, the root task text, and scope
  context — no side effects, easy to unit test.
- `strix/agents/factory.py` — `build_strix_agent()` constructs an `agents.sandbox.SandboxAgent`
  with the base toolset (`_BASE_TOOLS`), the sandbox `Shell`/`Filesystem` capabilities, and a
  `tool_use_behavior` callback that only stops the loop when a lifecycle tool
  (`finish_scan`/`agent_finish`) reports success.
- `strix/agents/prompt.py` — Jinja2 system-prompt renderer; templates live in
  `strix/agents/prompts/system_prompt.jinja`.
- `register_agent_tools(*tools)` (`strix/agents/factory.py`) is the extension point for adding
  tools to every scan agent (root + children) without editing the base toolset.

### Tool System

- Tools are plain async functions decorated with `agents.function_tool` (from the SDK) — **no
  more XML schema files, no registry, no executor**. The function's docstring becomes the tool
  description sent to the model; type hints become the JSON schema.
- Each tool module lives at `strix/tools/<name>/tools.py` (or `tool.py` for a single-tool module),
  re-exported through `strix/tools/<name>/__init__.py`.
- Shell (`exec_command`, `write_stdin`, ...) and filesystem tools come from the SDK's own
  `agents.sandbox.capabilities.Shell` / `Filesystem` — Strix only wraps their `on_invoke_tool` to
  add error-as-result behavior and a couple of input-massaging fixes (see
  `_wrap_exec_command`/`_wrap_write_stdin` in `strix/agents/factory.py`).
- Tools that must run against the sandbox container talk to it through the SDK's sandbox session
  primitives (`strix/runtime/session_manager.py`), not a custom HTTP tool server.

### LLM Integration

- `strix/config/models.py` — `StrixProvider(MultiProvider)` routes any non-OpenAI model prefix
  through LiteLLM, preserving the prefix (so users type `deepseek/deepseek-chat`, not
  `litellm/deepseek/deepseek-chat`). `configure_sdk_model_defaults()` is the single hook that wires
  Strix `Settings` into SDK-native defaults (default API key, LiteLLM compatibility flags, cost
  callback, OpenRouter attribution headers) at startup.
- Actual model calls go through `agents.extensions.models.litellm_model.LitellmModel`
  (`openai-agents[litellm]`), which wraps `litellm.acompletion()`. Strix does not call litellm
  directly for standard providers.
- **GitHub Copilot (fork-specific)**: `strix/llm/copilot.py` + the `--auth-github-copilot` CLI
  command. See "Fork-specific: GitHub Copilot support" below — this is the *only* fork addition to
  the LLM layer.

### Runtime (Docker Sandbox)

- `strix/runtime/backends.py` — `SandboxBackend` registry (`STRIX_RUNTIME_BACKEND`, default
  `"docker"`). `register_backend(name, backend)` is the extension point for adding a backend;
  there is currently only `"docker"` registered (no Podman backend in this fork).
- `strix/runtime/docker_client.py` — `StrixDockerSandboxClient`, a thin subclass of the SDK's
  Docker sandbox client adding NET_ADMIN/NET_RAW capabilities and `host.docker.internal` mapping.
- `strix/runtime/session_manager.py` — creates/reuses sandbox sessions per scan.
- `strix/runtime/caido_bootstrap.py` — starts the bundled Caido proxy inside the sandbox.
- `strix/runtime/local_dir_staging.py` — streams local target directories into the sandbox
  (file-by-file copy, or bind-mount via `--mount` for large repos).
- Sandbox image: `ghcr.io/usestrix/strix-sandbox` (Kali Linux), version pinned in
  `strix/config/settings.py` (`RuntimeSettings.image`, `STRIX_IMAGE` env var override).

### Config

- `strix/config/settings.py` — `pydantic-settings`-based `Settings` (env-var driven, nested:
  `llm`, `runtime`, `telemetry`, `integrations`, `viewer`).
- `strix/config/loader.py` — `load_settings()` (env + optional JSON override file via `--config`),
  `persist_current()` / `apply_config_override()`.
- `strix/config/models.py` — see LLM Integration above.

### Skills

- Unchanged in spirit from earlier Strix versions: Markdown files under
  `strix/skills/<category>/`, YAML frontmatter stripped, injected into the system prompt via
  Jinja2. Categories: `vulnerabilities/`, `frameworks/`, `technologies/`, `protocols/`, `cloud/`,
  `reconnaissance/`, `coordination/`, `scan_modes/`, `tooling/`, `custom/`.
- `register_skill_dir(path)` (`strix/skills/__init__.py`) lets downstream users add/override
  skills from an external directory without editing the package; last-registered directory wins.

### Reporting

- `strix/report/` — `state.py` (global report state + litellm cost callback), `dedupe.py`
  (finding dedup), `sarif.py` (SARIF export), `writer.py` (Markdown/CSV/JSON artifact writers),
  `usage.py` (token/cost usage tracking).

### Viewer

- `strix/viewer/` — a local web app (`strix view [<run>]`) that serves a React SPA
  (`strix/viewer/frontend/`, prebuilt into `strix/viewer/static/` via `make viewer`) for browsing
  past runs, live agent graphs, and vulnerability reports. `strix/viewer/server.py` is a stdlib
  `http.server`-based server, not FastAPI.

### Telemetry

- `strix/telemetry/posthog.py` / `scarf.py` — anonymous usage telemetry (`STRIX_TELEMETRY=false`
  to disable). No more OpenTelemetry/Traceloop — upstream removed that in the SDK migration.

### Interface

- `strix/interface/main.py` — CLI entry point (`strix = "strix.interface.main:main"`), argument
  parsing, environment validation, LLM warm-up.
- `strix/interface/cli.py` — non-interactive mode (Rich live display).
- `strix/interface/tui/` — interactive TUI (Textual), with `renderers/` per tool type
  (`strix/interface/tui/renderers/registry.py` maps tool name → renderer).
- `strix/interface/utils.py` — Docker connection checks, image pulling, target inference, diff
  scope resolution, etc.

---

## Fork-specific: GitHub Copilot support

This is the **entire fork delta** on top of upstream. See [MERGE.md](MERGE.md) for exactly which
files/lines this touches and how to re-apply it after a future upstream merge.

- litellm (already a dependency) ships a first-class `github_copilot` provider that handles the
  OAuth device-code flow, token caching/refresh, and every Copilot-specific request header
  entirely on its own. `StrixProvider._resolve_prefixed_model()` already routes
  `github_copilot/<model>` straight to litellm untouched — **no core routing changes were
  needed**.
- `strix/llm/copilot.py` only adds what litellm doesn't provide:
  - `GITHUB_COPILOT_HOSTNAME` env var → derives litellm's four separate GHE Cloud (`*.ghe.com`)
    URL overrides from one hostname.
  - A patched `Authenticator._poll_for_access_token` that honors the device-code response's own
    `interval`/`expires_in` instead of litellm's hardcoded 60-second window.
  - `has_cached_github_copilot_token()` / `validate_github_copilot_access_token()` /
    `clear_github_copilot_tokens()` for clear CLI messaging.
- `strix --auth-github-copilot [--github-copilot-host <host>]` (wired in
  `strix/interface/main.py`) runs the device-code login once, up front, and exits — this bypasses
  the normal `--target` requirement the same way the `strix view` subcommand does.
  `warm_up_llm()` also auto-triggers this on first use if no token is cached yet, before the TUI
  starts (so the device-code prompt is never obscured by the interface).
- **Important litellm bug workaround** (`strix/config/models.py::_configure_github_copilot_headers()`,
  called from `configure_sdk_model_defaults()`): litellm's `github_copilot` provider only adds its
  own required request headers (`editor-version`, etc.) when the caller passes *no*
  `extra_headers` — but the `openai-agents` SDK always does, so every real Copilot call fails
  without this fix. Sets the SDK's `HEADERS_OVERRIDE` context var so the headers are present
  regardless. See [MERGE.md](MERGE.md)'s "litellm header-injection bug" section for the full
  writeup — this is not obvious and easy to accidentally regress if `strix/llm/copilot.py` or this
  hook is refactored.
- Tests: `tests/test_copilot.py` (the `strix/llm/copilot.py` unit tests), `tests/test_copilot_cli.py`
  (CLI wiring), and `tests/test_models.py::TestConfigureGithubCopilotHeaders` (the header-bug fix).
- Docs: `docs/llm-providers/github-copilot.mdx`.

---

## Essential Commands

```bash
# Setup
make setup-dev               # uv sync (dev deps) + pre-commit hooks
uv sync --dev                # manual equivalent

# Development cycle (upstream Makefile has no `test` target — run pytest directly)
make dev                     # format + lint + type-check
uv run pytest -v             # run the test suite
make check-all               # format + lint + type-check + security

# Individual checks
make format                  # ruff format .
make lint                    # ruff check --fix .  (no pylint anymore)
make type-check              # mypy strix/ + pyright strix/
make security                # bandit -r strix/ -c pyproject.toml
make viewer                  # rebuild strix/viewer/frontend -> strix/viewer/static/ (commit output)

# Run the application
uv run strix --target https://example.com
uv run strix --target ./my-project --non-interactive
uv run strix --target example.com --instruction "Focus on auth vulns"
uv run strix --auth-github-copilot                                   # fork-specific
uv run strix --auth-github-copilot --github-copilot-host octodemo.ghe.com
uv run strix view                                                    # local run viewer

# Build binary
bash scripts/build.sh         # PyInstaller build
```

Note: there is no CI test job (`.github/workflows/build-release.yml` only builds/releases on tag
push) and no `test`/`test-cov` Makefile targets upstream. Run `uv run pytest -v` directly.

---

## Code Organization

```
strix/
├── agents/
│   ├── factory.py              # build_strix_agent(), register_agent_tools()
│   ├── prompt.py                # Jinja2 system-prompt renderer
│   └── prompts/system_prompt.jinja
├── config/
│   ├── settings.py              # pydantic-settings Settings (env-var driven)
│   ├── loader.py                # load_settings(), persist_current(), config overrides
│   └── models.py                # StrixProvider, configure_sdk_model_defaults()
├── core/
│   ├── runner.py                # run_strix_scan() — top-level entry point
│   ├── execution.py              # run_agent_loop(), spawn_child_agent()
│   ├── agents.py                 # AgentCoordinator — addressable multi-agent graph state
│   ├── hooks.py                  # RunHooks subclasses (budget, usage reporting)
│   ├── inputs.py                  # pure ModelSettings/task/scope builders
│   ├── sessions.py                # SQLiteSession wrapper (native session resume)
│   └── paths.py                   # strix_runs/<run>/ path helpers
├── interface/
│   ├── main.py                    # CLI entry point, arg parsing, env validation, warm-up
│   ├── cli.py                     # non-interactive mode
│   ├── tui/                       # interactive TUI (Textual) + renderers/
│   └── utils.py
├── llm/
│   └── copilot.py                 # FORK-SPECIFIC: GitHub Copilot device-flow + GHE Cloud
├── report/                        # dedupe, SARIF export, Markdown/CSV/JSON writers, cost usage
├── runtime/
│   ├── backends.py                 # SandboxBackend registry (docker only)
│   ├── docker_client.py             # StrixDockerSandboxClient
│   ├── session_manager.py            # create/reuse sandbox sessions
│   ├── caido_bootstrap.py             # starts Caido proxy in-sandbox
│   └── local_dir_staging.py           # local target -> sandbox staging
├── skills/                          # Markdown skill files by category (unchanged in spirit)
├── telemetry/                      # posthog.py, scarf.py (no more OTEL/Traceloop)
├── tools/                          # @function_tool modules, one dir per tool group
├── utils/                          # resource_paths.py, etc.
└── viewer/                         # local run viewer web app (server.py + React frontend)

tests/                              # flat layout (no more tests/<module>/ subpackages)
├── conftest.py (if present)
└── test_*.py                       # one file per concern, class-based within

containers/Dockerfile                # Kali Linux sandbox with nmap, sqlmap, nuclei, ffuf, ZAP, etc.
docs/                                 # Mintlify docs site (docs.json + *.mdx)
scripts/{build.sh,install.sh,docker.sh}
```

---

## Adding New Tools

1. Create `strix/tools/<name>/tools.py` (or `tool.py`):

   ```python
   from agents import function_tool


   @function_tool
   async def my_tool(param1: str) -> str:
       """One-line summary shown to the model.

       Longer description / usage guidance goes here — this whole docstring
       is sent to the LLM as the tool description.

       Args:
           param1: Description of param1.
       """
       return "result"
   ```

2. Re-export it from `strix/tools/<name>/__init__.py`.
3. Register it for every scan agent via `register_agent_tools(my_tool)`
   (`strix/agents/factory.py`) — call this once, before the first `build_strix_agent()` call
   (mirrors `strix.runtime.backends.register_backend`'s registration pattern).
4. Add a TUI renderer in `strix/interface/tui/renderers/` and register it in
   `strix/interface/tui/renderers/registry.py` if the tool needs custom TUI output.
5. Write tests in `tests/test_<name>.py` (flat layout, no subpackages).

There is no XML schema file to write and no `@register_tool(sandbox_execution=...)` decorator
anymore — the SDK derives everything from the function signature and docstring.

---

## Adding New Skills

Same as always: add a `.md` file under `strix/skills/<category>/` (optional YAML frontmatter is
stripped automatically); it's injected into the system prompt via Jinja2. Categories:
`vulnerabilities/`, `frameworks/`, `technologies/`, `protocols/`, `cloud/`, `reconnaissance/`,
`coordination/`, `scan_modes/`, `tooling/`, `custom/`.

---

## Code Style & Conventions

| Setting | Value |
|---------|-------|
| Line length | 100 characters |
| Quote style | Double quotes |
| Indentation | 4 spaces |
| Formatter/linter | ruff (`ruff format` + `ruff check --fix`; no pylint anymore) |
| Import sorting | isort via ruff, `strix` as known first party |
| Type checking | mypy strict + pyright strict, both must pass |
| `from __future__ import annotations` | used throughout (unlike some older Strix forks) |
| Union syntax | modern `X | Y` (Python 3.12+) |
| Naming | snake_case functions/modules, PascalCase classes |
| Logging | `logging.getLogger(__name__)` per module |
| Async | agent loop, tool execution, LLM calls are all `async def`/`await` |
| Lazy imports | heavy/optional deps (telemetry, litellm internals, provider SDKs) imported inside functions — see `pyproject.toml`'s `per-file-ignores` for `PLC0415` exceptions |

---

## Testing

- **pytest** with `pytest-asyncio` (`asyncio_mode = "auto"`).
- **Flat layout**: `tests/test_<concern>.py`, no `tests/<module>/` subpackages (upstream removed
  the old nested layout entirely in the SDK migration).
- Class-based grouping (`class TestXxx:`) with `@pytest.mark.parametrize` is still the preferred
  style within a file — see `tests/test_copilot.py` for a fork-added example.
- No CI test job — run `uv run pytest -v` locally before pushing.

---

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `STRIX_LLM` | LLM model name (litellm format, e.g. `openai/gpt-5.4`, `github_copilot/gpt-4o`) | — |
| `LLM_API_KEY` / `OPENAI_API_KEY` | API key for the LLM provider | — (optional; not needed for Copilot, local models, Vertex, Bedrock) |
| `LLM_API_BASE` / `OPENAI_API_BASE` / `OPENAI_BASE_URL` / `LITELLM_BASE_URL` / `OLLAMA_API_BASE` | Custom API base URL | — |
| `STRIX_REASONING_EFFORT` | Reasoning effort level | `high` |
| `STRIX_FORCE_REQUIRED_TOOL_CHOICE` | Force `tool_choice=required` | `false` |
| `LLM_TIMEOUT` | Per-request LLM timeout (seconds) | `300` |
| `STRIX_IMAGE` | Docker sandbox image override | `ghcr.io/usestrix/strix-sandbox:1.0.0` |
| `STRIX_RUNTIME_BACKEND` | Sandbox backend name | `docker` |
| `STRIX_MAX_LOCAL_COPY_MB` | Local target size cap before requiring `--mount` | `1024` |
| `STRIX_MAX_CONTEXT_IMAGES` | Max screenshot/image tool outputs kept live per agent | `3` |
| `STRIX_TELEMETRY` | Anonymous usage telemetry | `true` |
| `PERPLEXITY_API_KEY` | Enables `web_search` real-time research | — |
| `STRIX_APP_URL` | Base URL the local viewer proxies to for email/report delivery | `https://app.strix.ai` |
| `GITHUB_COPILOT_HOSTNAME` | **Fork-specific.** GHE Cloud hostname (e.g. `octodemo.ghe.com`); derives litellm's Copilot URL overrides | — (github.com) |

Settings are read by `strix/config/settings.py` (`pydantic-settings`) with JSON override support
via `--config <path>` / `~/.strix/cli-config.json`.

---

## Gotchas

1. **No XML anymore.** Tool calls go through the SDK's native function/custom-tool calling, not
   hand-rolled XML parsing. If you're used to older Strix forks, don't look for
   `strix/tools/registry.py` or `*_actions_schema.xml` — they don't exist.
2. **No FastAPI sidecar / tool server.** Sandbox tool execution goes through the SDK's own sandbox
   session/manifest machinery (`agents.sandbox`), not a custom HTTP server inside the container.
3. **Extension points, not invasive patches.** Prefer `register_backend()`
   (`strix/runtime/backends.py`), `register_agent_tools()` (`strix/agents/factory.py`), and
   `register_skill_dir()` (`strix/skills/__init__.py`) over editing upstream internals directly —
   this is exactly how the Copilot delta stays small across upstream merges.
4. **Lazy imports still matter.** Heavy/optional deps (litellm internals, provider SDKs, viewer
   report generation) are imported inside functions. Ruff's `PLC0415` has per-file ignores for this
   in `pyproject.toml` — add a new entry there rather than restructuring the whole module.
5. **Two type checkers.** Both mypy (strict) and pyright (strict) must pass; they sometimes
   disagree — check both (`make type-check`).
6. **Entry points**: `strix = "strix.interface.main:main"` (CLI) and `strix view` (dispatched
   before argument parsing in `main()`, since it doesn't need a `--target`).
7. **No Podman/FreeBSD in this fork.** If you're porting something from the old fork
   (`pre-merge-backup` branch), check whether it's actually Podman/FreeBSD-specific before
   re-adding it — most of that support doesn't apply to this fork's supported platforms.
8. **`strix_runs/`** is the gitignored runtime output directory (was previously also
   `agent_runs/` in older forks — that directory name is gone).
9. **Python 3.12+ required** (3.12–3.14 supported).
