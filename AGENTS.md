# AGENTS.md

> Guide for AI agents working in the Strix codebase.

## Project Overview

**Strix** (`strix-agent`, v0.7.0) is an open-source AI-powered penetration testing agent. It uses LLMs to autonomously conduct security assessments by executing tools inside a Docker sandbox (Kali Linux container with preinstalled security tools). Licensed Apache-2.0. Python 3.12+.

---

## Essential Commands

All commands are defined in the `Makefile`. Use Poetry for dependency management.

```bash
# Setup
make setup-dev              # Install dev deps + pre-commit hooks
poetry install --with=dev   # Manual equivalent

# Development cycle
make dev                    # format + lint + type-check + test (quick dev loop)
make check-all              # format + lint + type-check + security (full quality gate)

# Individual checks
make format                 # ruff format .
make lint                   # ruff check --fix + pylint
make type-check             # mypy strix/ + pyright strix/
make security               # bandit -r strix/ -c pyproject.toml

# Testing
make test                   # pytest -v
make test-cov               # pytest -v --cov=strix --cov-report=term-missing --cov-report=html

# Other
make clean                  # Remove __pycache__, .pytest_cache, .mypy_cache, etc.
make pre-commit             # Run all pre-commit hooks

# Run the application
poetry run strix --target https://example.com
poetry run strix --target ./my-project --non-interactive
poetry run strix --target example.com --instruction "Focus on auth vulns"

# Build binary
bash scripts/build.sh       # PyInstaller build
```

---

## Code Organization

```
strix/                          # Main package
├── agents/                     # Agent system
│   ├── base_agent.py           # BaseAgent + AgentMeta metaclass (agent loop, tool execution)
│   ├── state.py                # AgentState Pydantic model (agent_id, messages, iterations, etc.)
│   └── StrixAgent/             # Main agent implementation
│       ├── strix_agent.py      # StrixAgent(BaseAgent) — scan orchestration
│       └── system_prompt.jinja # Jinja2 system prompt template
├── config/
│   └── config.py               # Config class — reads env vars + ~/.strix/cli-config.json
├── interface/
│   ├── main.py                 # CLI entry point: strix.interface.main:main
│   ├── cli.py                  # Non-interactive CLI mode (Rich live display)
│   ├── tui.py                  # Interactive TUI (Textual framework)
│   └── tool_components/        # TUI renderer classes per tool type
├── llm/
│   ├── llm.py                  # LLM class — streaming via litellm acompletion, retries, caching
│   └── config.py               # LLMConfig — model name, scan mode, timeout, skills
├── runtime/
│   ├── __init__.py             # Runtime factory: get_runtime() → DockerRuntime singleton
│   ├── runtime.py              # AbstractRuntime ABC + SandboxInfo TypedDict
│   └── docker_runtime.py       # Docker container management for sandboxed execution
├── skills/                     # Markdown skill files loaded into system prompts via Jinja2
│   ├── __init__.py             # Skills loader — reads .md files, strips frontmatter
│   ├── vulnerabilities/        # Vulnerability-specific skills
│   ├── frameworks/             # Framework-specific skills
│   ├── technologies/           # Technology-specific skills
│   ├── protocols/              # Protocol-specific skills
│   ├── cloud/                  # Cloud-specific skills
│   ├── reconnaissance/         # Recon skills
│   ├── coordination/           # Multi-agent coordination skills
│   ├── scan_modes/             # Scan mode definitions (quick/standard/deep)
│   └── custom/                 # User custom skills
├── tools/                      # Tool system (see "Adding New Tools" below)
│   ├── __init__.py             # Conditional tool imports (STRIX_SANDBOX_MODE + feature flags)
│   ├── registry.py             # @register_tool decorator, XML schema loading
│   ├── executor.py             # Tool execution (sandbox HTTP or local), argument validation
│   ├── context.py              # ContextVar for current agent ID
│   ├── terminal/               # Example tool: terminal_execute
│   ├── browser/                # Browser interaction tools
│   ├── python/                 # Python execution tools
│   ├── file_edit/              # File editing tools
│   ├── notes/                  # Note-taking tools
│   ├── proxy/                  # Proxy tools (Caido/ZAP)
│   ├── reporting/              # Vulnerability reporting tools
│   ├── agents_graph/           # Sub-agent spawning tools
│   ├── finish/                 # Scan completion tools
│   ├── thinking/               # Reasoning/thinking tools
│   ├── todo/                   # Task tracking tools
│   └── web_search/             # Web search tools
└── telemetry/                  # PostHog analytics + Tracer for scan tracking

tests/                          # Mirrors source structure
├── conftest.py
├── tools/
│   ├── conftest.py             # Fixtures: sample_function_with_types, etc.
│   └── test_argument_parser.py # Example: class-based tests with parametrize
├── llm/
├── agents/
├── runtime/
├── interface/
└── telemetry/

containers/
└── Dockerfile                  # Kali Linux sandbox with nmap, sqlmap, nuclei, ffuf, ZAP, etc.

scripts/
├── build.sh                    # PyInstaller binary build
└── install.sh                  # End-user install script
```

---

## Architecture

### Agent System

- `BaseAgent` (with `AgentMeta` metaclass) runs an **async agent loop**: LLM generation → parse XML tool calls → execute tools → feed results back.
- `StrixAgent` is the main concrete agent handling multi-target scans (repos, local code, web apps, IPs).
- State tracked via `AgentState` (Pydantic `BaseModel`): agent_id, messages, iterations, sandbox info, waiting states, completion status.
- Agents can spawn sub-agents via the `agents_graph` tool.
- System prompts are **Jinja2 templates** stored in each agent's directory (e.g., `StrixAgent/system_prompt.jinja`).

### Tool System

- Tools are Python functions decorated with `@register_tool`.
- Each tool lives in `strix/tools/<tool_name>/` with three files:
  - `__init__.py`
  - `*_actions.py` — implementation with `@register_tool` decorator
  - `*_actions_schema.xml` — XML schema describing parameters, types, descriptions, examples
- XML schemas are parsed at import time and included in the LLM system prompt.
- Tools execute either **locally** (client-side) or **remotely** in the Docker sandbox via HTTP.
- `STRIX_SANDBOX_MODE` env var controls execution mode.
- Tool invocations are parsed from LLM output as **XML** (not JSON function calling).

### LLM Integration

- Uses `litellm` for multi-provider support (OpenAI, Anthropic, etc.).
- Streaming via `acompletion` (async).
- Supports: prompt caching (Anthropic), vision, reasoning effort configuration.
- Memory compression for long conversations.
- Usage and cost tracking.

### Skills System

- Markdown files in `strix/skills/<category>/` organized by topic.
- Loaded by `strix/skills/__init__.py`, which strips YAML frontmatter.
- Injected into agent system prompts via Jinja2 template variables.

### Runtime

- `AbstractRuntime` ABC with `DockerRuntime` implementation.
- Container image: `ghcr.io/usestrix/strix-sandbox` (Kali Linux).
- Tool server runs inside the container; host communicates via HTTP (httpx).
- `get_runtime()` factory returns a global singleton.

---

## Adding New Tools

Each tool follows a strict three-file convention in `strix/tools/<tool_name>/`:

### 1. Create the schema (`<tool_name>_actions_schema.xml`)

```xml
<tool name="my_tool">
    <description>What this tool does</description>
    <parameters>
        <parameter name="param1" type="str" required="true">
            <description>Parameter description</description>
            <example>example_value</example>
        </parameter>
    </parameters>
</tool>
```

### 2. Create the implementation (`<tool_name>_actions.py`)

```python
from strix.tools.registry import register_tool


@register_tool(sandbox_execution=True)  # True = runs in Docker; False = client-side only
def my_tool(param1: str) -> str:
    """Tool docstring."""
    # Implementation
    return "result"
```

### 3. Register in `strix/tools/__init__.py`

Add the import to the appropriate section (sandbox or local), respecting the conditional import pattern based on `STRIX_SANDBOX_MODE`.

### 4. (Optional) Add a TUI renderer in `strix/interface/tool_components/`

---

## Adding New Skills

1. Create a `.md` file in the appropriate `strix/skills/<category>/` directory.
2. Optional YAML frontmatter is stripped automatically.
3. The skill content is injected into agent system prompts via Jinja2.
4. Available categories: `vulnerabilities/`, `frameworks/`, `technologies/`, `protocols/`, `cloud/`, `reconnaissance/`, `coordination/`, `scan_modes/`, `custom/`.

---

## Code Style & Conventions

### Formatting

| Setting | Value |
|---------|-------|
| Line length | 100 characters |
| Quote style | Double quotes |
| Indentation | 4 spaces |
| Formatter | ruff (also acts as linter) |
| Import sorting | isort with black profile, `strix` as known first party |

### Type Annotations

- **mypy strict mode** and **pyright strict mode** are both enforced.
- All functions must have complete type annotations.
- `from __future__ import annotations` is **NOT** used.
- Modern `X | Y` union syntax used directly (Python 3.12+).
- Pydantic `BaseModel` for structured data (e.g., `AgentState`).

### Naming

- Snake case for functions, variables, modules.
- PascalCase for classes.
- Tool directories: `strix/tools/<tool_name>/` (snake_case).
- Tool schema files: `<tool_name>_actions_schema.xml`.
- Tool implementation files: `<tool_name>_actions.py`.

### Patterns

- **Logging**: `logging.getLogger(__name__)` in every module.
- **Async throughout**: Agent loop, tool execution, LLM calls are all async (`async def`, `await`).
- **Lazy imports**: Heavy modules (telemetry, runtime) are imported inside functions, not at module level.
- **Conditional imports**: `strix/tools/__init__.py` conditionally imports tools based on `STRIX_SANDBOX_MODE` env var and feature flags.
- **Custom exceptions**: Domain-specific exception classes (e.g., `LLMRequestFailedError`, `SandboxInitializationError`, `ArgumentConversionError`, `ImplementedInClientSideOnlyError`).
- **Config via classmethods**: `Config` class uses `@classmethod` accessors that read env vars with fallback to JSON config.

---

## Testing

### Framework & Configuration

- **pytest** with `pytest-asyncio` (asyncio_mode = `"auto"` — async tests auto-detected).
- Additional plugins: `pytest-cov`, `pytest-mock`.
- Config in `pyproject.toml` under `[tool.pytest.ini_options]`.

### Conventions

- **Class-based organization**: Group related tests in `class TestXxx:` classes.
- **Parametrize**: Use `@pytest.mark.parametrize` for input variations.
- **Docstrings**: Include docstrings on test methods describing what they verify.
- **Fixtures**: Define in `conftest.py` files at appropriate directory levels.
- **Directory structure**: Tests mirror `strix/` source structure under `tests/`.

### Example Test Pattern

```python
import pytest

class TestMyFeature:
    """Tests for my_feature function."""

    @pytest.mark.parametrize(
        "input_val, expected",
        [
            ("a", True),
            ("b", False),
        ],
    )
    def test_basic_behavior(self, input_val: str, expected: bool) -> None:
        """Verify basic behavior for various inputs."""
        result = my_feature(input_val)
        assert result == expected
```

---

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `STRIX_LLM` | LLM model name (litellm format) | — |
| `LLM_API_KEY` | API key for the LLM provider | — |
| `LLM_API_BASE` | Custom API base URL | — |
| `STRIX_REASONING_EFFORT` | Reasoning effort level | — |
| `STRIX_IMAGE` | Docker sandbox image override | `ghcr.io/usestrix/strix-sandbox:0.1.11` |
| `STRIX_SANDBOX_MODE` | Controls tool execution mode (sandbox vs local) | — |

Config is read by `strix/config/config.py` from env vars (uppercase) with fallback to `~/.strix/cli-config.json`.

---

## CI/CD

- **GitHub Actions** workflow: `.github/workflows/build-release.yml`.
- Triggers on tag push (`v*`).
- Builds PyInstaller binaries for: macOS ARM64, macOS x86_64, Linux x86_64, Windows x86_64.
- Publishes as GitHub Release assets.

---

## Gotchas

1. **XML, not JSON**: Tool calls from the LLM are parsed as XML, not JSON function calling. Schemas are XML files.
2. **Conditional tool imports**: `strix/tools/__init__.py` selectively imports tools based on `STRIX_SANDBOX_MODE`. When adding a new tool, you must add it to the correct import section.
3. **Lazy imports**: Modules like telemetry and runtime are imported inside functions to avoid circular imports and reduce startup time. Follow this pattern for heavy dependencies.
4. **Two type checkers**: Both mypy (strict) and pyright (strict) must pass. They sometimes have different requirements — check both.
5. **XML schema required**: Every tool needs a `*_actions_schema.xml` file. The schema is loaded at import time by the registry and sent to the LLM in the system prompt.
6. **`sandbox_execution` parameter**: The `@register_tool` decorator takes `sandbox_execution=True|False`. Client-side-only tools (like `finish_scan`, `thinking`) set this to `False`.
7. **Entry point**: The application entry point is `strix.interface.main:main`, registered as `strix` console script in `pyproject.toml`.
8. **Pre-commit hooks**: ruff, mypy, bandit, and pyupgrade run as pre-commit hooks. Run `make pre-commit` to test locally.
9. **Python 3.12+ required**: Uses modern syntax (`X | Y` unions, etc.). Supports 3.12, 3.13, 3.14.
10. **Strix output directories**: `strix_runs/` and `agent_runs/` are gitignored runtime output directories.
