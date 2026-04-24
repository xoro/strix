# Merging Upstream Strix into Our Fork

This document describes how to merge the upstream [usestrix/strix](https://github.com/usestrix/strix) repository into our fork.

## Overview

Our fork adds several features on top of upstream:

- **GitHub Copilot LLM integration** (`strix/llm/copilot.py`)
- **Podman container runtime** (`strix/runtime/podman_runtime.py`)
- **FreeBSD platform support** (platform markers in `pyproject.toml`, auto-detection in `config.py`)
- **Copilot authentication flow** (functions in `strix/interface/main.py`, CLI flag `--auth-github-copilot`)
- **Copilot/Podman tests** (`tests/llm/test_copilot.py`, `tests/interface/test_github_copilot_auth.py`)

## Prerequisites

The **upstream** remote should point at `usestrix/strix`. **origin** is usually your GitHub fork (and optionally a **gitlab** remote for the old GitLab clone):

```bash
git remote -v
# origin    git@github.com:<you>/strix.git
# upstream  https://github.com/usestrix/strix.git
# gitlab    git@gitlab.pallach.de:stoicism/strix.git   # optional
```

If `upstream` is missing:

```bash
git remote add upstream https://github.com/usestrix/strix.git
```

**Tooling:** Upstream migrated from Poetry to **uv** (`uv.lock`, `Makefile` uses `uv sync` / `uv run`). Use `uv run …` below, not `poetry run …`.

## Merge Steps

### 1. Create a safety backup branch

```bash
git checkout main
git branch -D pre-merge-backup 2>/dev/null; git checkout -b pre-merge-backup
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

Upstream uses **PEP 621** (`[project]` / `[dependency-groups]`) and **`uv.lock`**, not `[tool.poetry]`. Edit the **`dependencies`** array under **`[project]`**.

#### 5a. `pyproject.toml` / `uv.lock` — FreeBSD & fork-only dependencies

Upstream may add or reorder dependencies. After each merge, **re-apply** the fork’s
**`platform_system`** markers and related entries. Do **not** use **`sys_platform == 'freebsd'`**
for FreeBSD: on modern releases **`sys.platform`** is e.g. **`freebsd15`**, so those markers
miss. Use **`platform_system == 'FreeBSD'`** (matches `platform.system()`).

**Runtime `[project]` → `dependencies` — verify or restore:**

| Concern | Fork pattern |
|--------|----------------|
| Docker vs Podman | **`docker`** PyPI package on **all** platforms (Docker SDK talks to Podman’s API on FreeBSD via **`DOCKER_HOST`** / default socket). **`podman`** PyPI only on FreeBSD for runtime bindings. |
| LiteLLM extras | `"litellm[proxy]>=…; platform_system != 'FreeBSD'"` and plain `"litellm>=…; platform_system == 'FreeBSD'"` (`[proxy]` pulls **pyroscope-io**, no FreeBSD wheels). |
| Traceloop | `"traceloop-sdk>=…; platform_system != 'FreeBSD'"` if upstream adds it unguarded. |
| scrubadub / sklearn / scipy | `"scrubadub>=…; platform_system != 'FreeBSD'"` — avoids **scikit-learn** → **SciPy** source builds on FreeBSD. |

**`[tool.uv.sources]`** — keep path overrides for **`vendor/fastuuid-stub`**, **`vendor/tiktoken-stub`**, **`vendor/tokenizers-stub`** with FreeBSD markers where applicable (PyPI wheels often exclude FreeBSD; stubs avoid huge Rust builds).

**`[dependency-groups]` dev** — optional: exclude **`ruff`** and **`pyinstaller`** on FreeBSD (`platform_system != 'FreeBSD'`) so `uv sync` does not compile massive native stacks on small ARM64 hosts.

Add to mypy `[[tool.mypy.overrides]]` → `module` list (merge with upstream’s third-party stubs — do not drop upstream entries such as `opentelemetry.*`, `scrubadub.*`, `traceloop.*`):

```toml
    "docker.*",
    "podman.*",
```

After edits, refresh the lockfile: **`uv lock`** (or **`uv sync`**), then commit **`uv.lock`** if it changed.

**Related Python files (if upstream touches them):**

- **`strix/telemetry/utils.py`** — If **`scrubadub`** is omitted on FreeBSD (see table above),
  **`TelemetrySanitizer`** must not import scrubadub unconditionally. Keep **`_HAS_SCRUBADUB`**,
  regex-only string cleaning without scrubadub, and full **`Scrubber`** when scrubadub is installed.
- Update **`INSTALL.md`** when dependency or FreeBSD install policy changes.

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

#### 5c. `strix/llm/__init__.py` — Copilot integration + FreeBSD tokenizer

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

**FreeBSD:** If upstream refactors this module, preserve **`litellm.disable_hf_tokenizer_download = True`**
(or equivalent) when **`platform.system() == "FreeBSD"`** so stub **`tokenizers`** (see 5a) does not
trigger Hugging Face downloads.

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

1. **Top-level import** (already present in fork, verify it survived):
   ```python
   from strix.llm.copilot import maybe_copilot_headers
   ```

2. **Copilot headers** in `_build_completion_args()` (after the `reasoning_effort` block):
   ```python
   args.update(maybe_copilot_headers(self.config.model_name))
   ```
   Note: `_build_completion_args()` uses `self.config.litellm_model` (the resolved API name)
   as the `model` arg since v0.8.1. `maybe_copilot_headers()` still receives
   `self.config.model_name` (the raw user-facing name) for Copilot detection.

3. **`_is_copilot()` method** (after `_is_anthropic()`):
   ```python
   def _is_copilot(self) -> bool:
       if not self.config.model_name:
           return False
       return self.config.model_name.lower().startswith("github_copilot/")
   ```

4. **"Continue." message** in `_prepare_messages()` (before the Anthropic cache control check).

   **CRITICAL:** Upstream adds `<meta>Continue the task.</meta>` when the last message is
   `assistant`, but only in **non-interactive** mode (`not self.config.interactive`). The
   Copilot branch MUST be an `if/elif` with that check — NOT two separate `if` blocks:

   ```python
   if self._is_copilot() and messages and messages[-1].get("role") != "user":
       messages.append({"role": "user", "content": "Continue."})
   elif messages[-1].get("role") == "assistant" and not self.config.interactive:
       messages.append({"role": "user", "content": "<meta>Continue the task.</meta>"})
   ```

   Replace any upstream-only `if messages[-1].get("role") == "assistant":` (without the
   interactive guard) with the `elif` above. Using two separate `if` blocks will double-append for Copilot models.

#### 5f. `strix/llm/dedupe.py` — Copilot headers

Add after the `api_base` check in `check_duplicate()`. As of v0.8.1, upstream also calls
`resolve_strix_model()` to resolve `model_name` into a `litellm_model` for the `model` arg.
The Copilot headers call uses the **pre-resolution** `model_name` (for detection), which is
the value returned directly by `resolve_llm_config()`:

```python
from strix.llm.copilot import maybe_copilot_headers
completion_kwargs.update(maybe_copilot_headers(model_name))
```

#### 5g. `strix/llm/memory_compressor.py` — Copilot headers + retry loop

**WARNING: This function is the most fragile merge point.** The merge strategy
(`--strategy-option theirs`) will overwrite our retry loop with upstream's simple
`try/except`, sometimes leaving orphaned `except` blocks at the wrong indentation that
cause a `SyntaxError`. Always verify this function compiles after the merge.

The entire body of `_summarize_messages()` from the `completion_args` dict through the
end of the function must look like this after restoration:

```python
    completion_args: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "timeout": timeout,
    }
    if api_key:
        completion_args["api_key"] = api_key
    if api_base:
        completion_args["api_base"] = api_base

    from strix.llm.copilot import maybe_copilot_headers

    completion_args.update(maybe_copilot_headers(model))

    last_exc: Exception | None = None
    backoff = SUMMARIZE_INITIAL_BACKOFF

    for attempt in range(1, SUMMARIZE_MAX_RETRIES + 1):
        try:
            response = litellm.completion(**completion_args)
            summary = response.choices[0].message.content or ""
            if not summary.strip():
                return messages[0]
            summary_msg = "<context_summary message_count='{count}'>{text}</context_summary>"
            return {
                "role": "user",
                "content": summary_msg.format(count=len(messages), text=summary),
            }
        except (litellm.exceptions.Timeout, litellm.exceptions.APIConnectionError) as exc:
            last_exc = exc
            if attempt < SUMMARIZE_MAX_RETRIES:
                logger.warning(
                    "Summarize attempt %d/%d timed out, retrying in %.1fs...",
                    attempt,
                    SUMMARIZE_MAX_RETRIES,
                    backoff,
                )
                time.sleep(backoff)
                backoff *= 2
            else:
                logger.error(
                    "Failed to summarize messages after %d attempts: %s",
                    SUMMARIZE_MAX_RETRIES,
                    exc,
                )
        except Exception:
            logger.exception("Failed to summarize messages")
            return messages[0]

    if last_exc is not None:
        logger.error("All summarize attempts failed: %s", last_exc)
    return messages[0]
```

Key points:
- `role` is `"user"` (not `"assistant"`) in the success return
- The outer `for` loop + inner `try/except` pattern must be preserved exactly
- There is NO outer `try/except` wrapping the loop; only the inner ones inside the loop
- `time` must be imported at the top of the file

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

Also check for behavioral changes:

- **`test_non_copilot_appends_meta_continue_when_last_is_assistant`** in `tests/llm/test_copilot.py`:
  Upstream appends `<meta>Continue the task.</meta>` when the last message is `assistant`
  **and** the session is non-interactive. If upstream changes that condition, update this test
  and the `if/elif` structure in `_prepare_messages()` together.

### 7. Verify fragile merge points

Before running the full test suite, do a quick syntax check on the two most fragile files:

```bash
uv run python -c "import strix.llm.memory_compressor; print('memory_compressor OK')"
uv run python -c "import strix.llm.llm; print('llm OK')"
```

If either fails with a `SyntaxError`, the merge mangled that file. See sections 5e and 5g
for the exact expected structure.

### 8. Run tests

```bash
uv run pytest tests/ -x --tb=short
```

### 9. Commit the restorations

```bash
git add -A
git commit -m "Restore fork-specific Copilot/Podman/FreeBSD features after upstream merge"
```

### 10. Verify

```bash
git log --oneline --graph -10
git diff upstream/main --stat  # should only show fork additions
```

## Known fragile merge points

These files are consistently mangled by `--strategy-option theirs` and require careful
inspection after every merge:

| File | Why fragile | What to check |
|------|-------------|---------------|
| `strix/llm/memory_compressor.py` | Fork wraps `litellm.completion` in a retry loop; upstream uses a simple `try/except`. The merge leaves orphaned `except` blocks outside the loop and sometimes changes `role` from `"user"` to `"assistant"`. | Verify the full function structure matches section 5g exactly. Run the syntax check from section 7. |
| `strix/llm/llm.py` | Fork's Copilot `"Continue."` check and upstream's `<meta>Continue the task.</meta>` (non-interactive) check both touch the same lines in `_prepare_messages()`. The merge collapses them into the upstream version only. | Verify the `if/elif` from section 5e is present (including `not self.config.interactive`), not two separate `if` blocks. |

## Files unique to our fork (should never be deleted by merge)

| File | Purpose |
|------|---------|
| `strix/llm/copilot.py` | Copilot litellm headers & config |
| `strix/runtime/podman_runtime.py` | Podman container runtime |
| `tests/llm/test_copilot.py` | Copilot integration tests |
| `tests/interface/test_github_copilot_auth.py` | Copilot auth tests |
| `MERGE.md` | This maintenance guide (not in upstream) |

Upstream also ships `AGENTS.md`; keep any **local** edits in sync manually if you maintain a fork-specific copy.

## Merge history

| Date | Upstream version | Notes |
|------|-----------------|-------|
| 2026-02-20 | v0.8.0 | Initial merge. Upstream refactored LLM config to `resolve_llm_config()`, removed `strix/cli/` directory. |
| 2026-02-20 | — (post-merge fix) | Added retry with exponential backoff to `memory_compressor.py` `_summarize_messages()`. Increased default timeout from 30s to 120s. Wired up the previously unused `SUMMARIZE_MAX_RETRIES` / `SUMMARIZE_INITIAL_BACKOFF` constants. Fixes repeated `litellm.Timeout` errors during CI runs with Copilot. |
| 2026-02-22 | v0.8.1 | Upstream added `normalize_tool_format` / `resolve_strix_model` utilities, centralized strix model resolution with separate API and capability names (`litellm_model` / `canonical_model` in `LLMConfig`), fixed tool-call tag parsing, added `<meta>Continue the task.</meta>` fallback for all models when last message is assistant. Restored all fork-specific Copilot/Podman/FreeBSD changes. Fixed merge-mangled `_summarize_messages()` (orphaned except block + wrong `role: assistant`). Updated `test_non_copilot_no_append_even_with_assistant_last` → `test_non_copilot_appends_meta_continue_when_last_is_assistant` to reflect new upstream behaviour. |
| 2026-04 | v0.8.3+ (upstream uv) | Upstream migrated to **uv** (`uv.lock`, hatchling, `[project]` deps). Integrated fork on GitHub (`xoro/strix`); merge conflicts resolved with PEP 621 dependency strings for docker/podman, `strix_image` 0.1.13, and `_prepare_messages()` Copilot + **non-interactive** meta-continue `elif`. |
| 2026-04 | — (docs) | **MERGE.md §5a** updated: use **`platform_system`** (not **`sys_platform`**) for FreeBSD; document **litellm** / **traceloop** / **scrubadub** / **uv.sources** stubs / dev **ruff**–**pyinstaller** exclusions; **§5c** FreeBSD tokenizer note; telemetry scrubadub fallback. |
| 2026-04-24 | v0.8.3 (9fb1012) | Upstream: Kubernetes security skill, NoSQL injection guide, `--config` full override fix, `asyncio.wait_for` wrap for indefinite hang prevention. Fork: added GHES support for GitHub Copilot auth (`_GHESAuthenticator`, `GITHUB_COPILOT_*` env vars, `GITHUB_COPILOT_USER_API_URL`), bumped litellm to `>=1.83.0` (vanilla PyPI, no local fork). All fork features survived merge cleanly. |
