# Merging Upstream Strix into Our Fork

This document describes how to merge the upstream [usestrix/strix](https://github.com/usestrix/strix) repository into our fork.

## Overview

Our fork adds several features on top of upstream:

- **GitHub Copilot LLM integration** (`strix/llm/copilot.py`)
- **Podman container runtime** (`strix/strix/runtime/podman_runtime.py`)
- **FreeBSD platform support** (platform markers in `pyproject.toml`, auto-detection in `config.py`)
- **Copilot authentication flow** (functions in `strix/interface/main.py`, CLI flag `--auth-github-copilot`)
- **Copilot/Podman tests** (`tests/llm/test_copilot.py`, `tests/interface/test_github_copilot_auth.py`)

## Prerequisites

The upstream remote should already be configured:

```bash
git remote -v
# origin    git@gitlab.pallach.de:stoicism/strix.git
# upstream  https://github.com/usestrix/strix.git
```

If `upstream` is missing:

```bash
git remote add upstream https://github.com/usestrix/strix.git
```

## Merge Steps

### 1. Create a safety backup branch

```bash
git checkout main
git checkout -b pre-merge-backup
git checkout main
```

### 2. Fetch upstream

```bash
git fetch upstream
```

### 3. Check upstream version

```bash
git log upstream/main --oneline -5
```

### 4. Merge upstream (prefer upstream on conflicts)

```bash
git merge upstream/main --strategy-option theirs -m "Merge upstream strix vX.Y.Z into fork"
```

Resolve any conflicts that `--strategy-option theirs` cannot handle automatically (e.g., rename/delete conflicts):

```bash
git status  # check for conflicts
git checkout --theirs <conflicted-file>
git add <conflicted-file>
git commit  # only if the merge wasn't committed yet
```

### 5. Restore fork-specific changes

The merge with `--strategy-option theirs` overwrites our customizations in shared files. The following must be manually restored after every merge.

#### 5a. `pyproject.toml` — Podman dependency & Docker platform marker

Replace:
```toml
docker = "^7.1.0"
```

With:
```toml
docker = {version = "^7.1.0", markers = "sys_platform != 'freebsd'"}
podman = {version = "^5.0.0", markers = "sys_platform == 'freebsd'"}
```

Add to mypy `[[tool.mypy.overrides]]` module list:
```toml
    "docker.*",
    "podman.*",
```

#### 5b. `strix/config/config.py` — FreeBSD runtime auto-detection

Add the `sys` import and `_default_runtime_backend()` function:

```python
import sys

def _default_runtime_backend() -> str:
    if sys.platform.startswith("freebsd"):
        return "podman"
    return "docker"
```

Replace hardcoded `strix_runtime_backend = "docker"` with:

```python
strix_runtime_backend = _default_runtime_backend()
```

#### 5c. `strix/llm/__init__.py` — Copilot integration

Add:
```python
from .copilot import configure_copilot_litellm
```

Add after `litellm._logging._disable_debugging()`:
```python
litellm.suppress_debug_info = True
```

Add at end of file:
```python
configure_copilot_litellm()
```

#### 5d. `strix/runtime/__init__.py` — Podman runtime

Add the Podman branch to `get_runtime()`, after the Docker branch:

```python
if runtime_backend == "podman":
    from .podman_runtime import PodmanRuntime

    if _global_runtime is None:
        _global_runtime = PodmanRuntime()
    return _global_runtime
```

#### 5e. `strix/llm/llm.py` — Copilot support in LLM class

Restore three things:

1. **Copilot headers** in `_build_completion_args()`:
   ```python
   args.update(maybe_copilot_headers(self.config.model_name))
   ```

2. **`_is_copilot()` method**:
   ```python
   def _is_copilot(self) -> bool:
       if not self.config.model_name:
           return False
       return self.config.model_name.lower().startswith("github_copilot/")
   ```

3. **"Continue." message** in `_prepare_messages()` (before the Anthropic cache control check):
   ```python
   if self._is_copilot() and messages and messages[-1].get("role") != "user":
       messages.append({"role": "user", "content": "Continue."})
   ```

#### 5f. `strix/llm/dedupe.py` — Copilot headers

Add after the `api_base` check in `check_duplicate()`:

```python
from strix.llm.copilot import maybe_copilot_headers
completion_kwargs.update(maybe_copilot_headers(model_name))
```

#### 5g. `strix/llm/memory_compressor.py` — Copilot headers

Add after the `api_base` check in `_summarize_messages()`:

```python
from strix.llm.copilot import maybe_copilot_headers
completion_args.update(maybe_copilot_headers(model))
```

#### 5h. `strix/interface/main.py` — Copilot auth flow

This is the largest restoration. The upstream version removes all Copilot auth code. Restore:

1. **Imports**: Add `json`, `os`, `from datetime import UTC`
2. **Helper functions** (before `validate_environment()`):
   - `_is_github_copilot_model()`
   - `_get_github_copilot_token_path()`
   - `_has_github_copilot_token()`
   - `_validate_github_copilot_token()`
   - `_clear_github_copilot_tokens()`
   - `authenticate_github_copilot()`
3. **`validate_environment()`**: Make `LLM_API_KEY` not required for Copilot models (`if not Config.get("llm_api_key") and not is_copilot:`)
4. **`warm_up_llm()`**: Add Copilot token validation checks and `maybe_copilot_headers()` call
5. **`parse_arguments()`**:
   - Add `--auth-github-copilot` argument
   - Remove `required=True` from `--target`
   - Early return when `args.auth_github_copilot` is set
   - Manual target validation: `if not args.target: parser.error(...)`
   - Add Copilot example to epilog
6. **`main()`**: Handle `args.auth_github_copilot` before `check_docker_installed()`

#### 5i. `Makefile` — Pylint (optional)

Check whether upstream has restored/changed the `lint` target. Decide if you want pylint enabled.

### 6. Update tests if needed

Check that fork-specific tests still reference the correct symbols. Common issues after upstream refactoring:

- `strix.llm.dedupe.Config.get` → may become `strix.llm.dedupe.resolve_llm_config`
- `strix.llm.memory_compressor.Config.get` → may become `strix.llm.memory_compressor.resolve_llm_config`
- `strix.interface.main.check_container_runtime_installed` → may become `strix.interface.main.check_docker_installed`

### 7. Run tests

```bash
poetry run python -m pytest tests/ -x --tb=short
```

### 8. Commit the restorations

```bash
git add -A
git commit -m "Restore fork-specific Copilot/Podman/FreeBSD features after upstream merge"
```

### 9. Verify

```bash
git log --oneline --graph -10
git diff upstream/main --stat  # should only show fork additions
```

## Files unique to our fork (should never be deleted by merge)

| File | Purpose |
|------|---------|
| `strix/llm/copilot.py` | Copilot litellm headers & config |
| `strix/runtime/podman_runtime.py` | Podman container runtime |
| `tests/llm/test_copilot.py` | Copilot integration tests |
| `tests/interface/test_github_copilot_auth.py` | Copilot auth tests |
| `AGENTS.md` | AI agent coding guide |
| `MERGE.md` | This file |

## Merge history

| Date | Upstream version | Notes |
|------|-----------------|-------|
| 2026-02-20 | v0.8.0 | Initial merge. Upstream refactored LLM config to `resolve_llm_config()`, removed `strix/cli/` directory. |
