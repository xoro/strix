# MERGE.md

> How to re-apply this fork's GitHub Copilot support after merging a newer upstream Strix.

## Overview

This fork carries exactly one delta on top of [usestrix/strix](https://github.com/usestrix/strix):
**GitHub Copilot as an LLM provider** (device-code OAuth authentication + GitHub Enterprise Cloud
support). There is no Podman/FreeBSD support — `main` is based directly on upstream's tree.

Because litellm (an upstream dependency, not something this fork adds) already implements the
entire Copilot OAuth flow, token caching/refresh, and Copilot-specific request headers as a
first-class provider, the fork delta is small and self-contained:

| File | Status | Purpose |
|------|--------|---------|
| `strix/llm/__init__.py` | fork-only | package init |
| `strix/llm/copilot.py` | fork-only | GHE Cloud hostname → litellm URL env vars, patched device-code poll timing, cached-token validity check, `github_copilot_extra_headers()` litellm-bug workaround |
| `strix/interface/main.py` | **modifies upstream file** | `--auth-github-copilot`/`--github-copilot-host` CLI flags, `_run_github_copilot_auth_command()`, `warm_up_llm()` auto-auth hook |
| `strix/config/models.py` | **modifies upstream file** | `_configure_github_copilot_headers()` called from `configure_sdk_model_defaults()` — sets the SDK's `HEADERS_OVERRIDE` context var so every Copilot request includes the headers litellm's own provider fails to add (see "litellm header-injection bug" below) |
| `pyproject.toml` | **modifies upstream file** | per-file ruff ignores for `strix/llm/copilot.py` (`PLC0415`) and `tests/test_copilot.py` (`S105`) |
| `docs/llm-providers/github-copilot.mdx` | fork-only | user-facing docs |
| `docs/docs.json` | **modifies upstream file** | nav entry for the doc above |
| `docs/llm-providers/overview.mdx` | **modifies upstream file** | provider card |
| `tests/test_copilot.py` | fork-only | unit tests for `strix/llm/copilot.py` |
| `tests/test_copilot_cli.py` | fork-only | unit tests for the CLI wiring |
| `tests/test_models.py` | **modifies upstream file** | `TestConfigureGithubCopilotHeaders` — tests for the `HEADERS_OVERRIDE` wiring in `strix/config/models.py` |
| `AGENTS.md` | fork-only | this repo's AI-agent guide (describes upstream architecture + this delta) |
| `MERGE.md` | fork-only | this file |

Nothing else is touched. In particular: **no changes to `strix/config/models.py`,
`StrixProvider`, or any core routing logic** — `github_copilot/<model>` already routes correctly
through litellm with zero code changes (see "Why so little code" below).

## Prerequisites

```bash
git remote add upstream https://github.com/usestrix/strix.git
git fetch upstream
git log upstream/main --oneline -5   # check the version you're merging
```

## Merge steps

### 1. Safety backup

```bash
git checkout main
git branch -D pre-merge-backup 2>/dev/null; git checkout -b pre-merge-backup
git checkout main
```

### 2. Reset `main` to upstream's tree

Because the fork delta is small, self-contained, and doesn't touch upstream's core logic, the
simplest and most reliable approach is a **hard reset to upstream, then re-apply the delta as
fresh commits** — not a merge commit. This avoids inheriting upstream's history churn into a merge
commit and keeps the delta trivially diffable at any time:

```bash
git reset --hard upstream/main
```

`main` is now byte-identical to upstream. The previous fork state (including this delta) is still
reachable via `pre-merge-backup` and reflog.

### 3. Re-apply the Copilot delta

Cherry-pick or manually re-apply the files listed in the table above from `pre-merge-backup`
(or an earlier commit before your reset). If upstream hasn't changed
`strix/interface/main.py`'s `parse_arguments()`/`main()`/`warm_up_llm()` structure much, a
straight `git checkout pre-merge-backup -- strix/llm strix/llm/__init__.py` for the fork-only
files plus a manual re-diff of `strix/interface/main.py` is usually enough:

```bash
git checkout pre-merge-backup -- \
  strix/llm/__init__.py \
  strix/llm/copilot.py \
  docs/llm-providers/github-copilot.mdx \
  tests/test_copilot.py \
  tests/test_copilot_cli.py \
  AGENTS.md \
  MERGE.md

git diff pre-merge-backup upstream/main -- strix/interface/main.py docs/docs.json \
  docs/llm-providers/overview.mdx pyproject.toml
# ^ inspect, then hand-apply the Copilot-specific hunks (search for "copilot"/"Copilot"
# case-insensitively — the delta is small and localized, see the diff hunks below).
```

### 4. Verify

```bash
uv sync --dev
uv run python -c "import strix.interface.main; import strix.llm.copilot"
uv run pytest tests/test_copilot.py tests/test_copilot_cli.py -v
uv run pytest -q   # full suite — compare failures against a pristine upstream/main checkout
                    # to confirm you haven't introduced new ones (there are usually a couple of
                    # pre-existing upstream failures unrelated to this fork; see below)
make format && make lint && make type-check
```

### 5. Commit

```bash
git add -A
git commit -m "Re-apply GitHub Copilot support after upstream merge to <version>"
```

Update the "Merge history" table below with what changed.

---

## Why so little code is needed

Upstream v1.2.0 already routes any `github_copilot/<model>` through litellm untouched:
`StrixProvider._resolve_prefixed_model()` (`strix/config/models.py`) falls through to the litellm
provider with the model name preserved for any prefix it doesn't special-case. litellm's own
`github_copilot` provider (`litellm.llms.github_copilot`) then handles:

- The OAuth device-code login flow (`Authenticator._login()`).
- Token caching/refresh (`~/.config/litellm/github_copilot/{access-token,api-key.json}`, or
  wherever `GITHUB_COPILOT_TOKEN_DIR` points).
- Every Copilot-specific request header (`editor-version`, `X-Initiator`,
  `Copilot-Vision-Request`, etc.) via `GithubCopilotConfig.validate_environment()` — **but only
  when the caller passes no `extra_headers` at all; see the litellm bug below.**
- `LLM_API_KEY` is already optional in upstream's `validate_environment()`
  (`strix/interface/main.py`) — Copilot doesn't need it.

So this fork only adds the UX litellm doesn't provide on its own:

1. A single `GITHUB_COPILOT_HOSTNAME` env var that derives litellm's four separate GHE Cloud URL
   overrides, instead of requiring users to set `GITHUB_COPILOT_DEVICE_CODE_URL`,
   `GITHUB_COPILOT_ACCESS_TOKEN_URL`, `GITHUB_COPILOT_API_KEY_URL`, and `GITHUB_COPILOT_API_BASE`
   by hand.
2. A patched device-code poll loop that honors the `interval`/`expires_in` fields GitHub's
   device-code endpoint actually returns, instead of litellm's hardcoded 60-second window (some
   GHE Cloud orgs configure longer windows).
3. A `strix --auth-github-copilot` command so the (blocking, `print()`-based) device-code login
   happens once, up front, before the TUI starts — instead of potentially triggering mid-scan and
   colliding with the Textual UI.
4. A workaround for a real litellm request-header bug (below) — without it, every real Copilot
   call through the SDK fails.

If a future litellm/upstream release adds GHE Cloud hostname support or a longer default poll
window natively, most or all of `strix/llm/copilot.py` could shrink further or be dropped in favor
of upstream's native handling — check litellm's `github_copilot` provider changelog before
re-applying this delta.

## The litellm header-injection bug (why `strix/config/models.py` is patched)

**Confirmed by inspecting the actual outgoing HTTP request** (2026-07-22, litellm bundled with
`openai-agents[litellm]==0.14.6`): `GithubCopilotConfig.validate_environment()` — the method that
adds `editor-version`, `x-github-api-version`, `copilot-integration-id`, etc. — is only invoked by
litellm when the caller passes **no** `extra_headers` argument at all to `litellm.acompletion()`.

The `openai-agents` SDK's `LitellmModel._fetch_response()` **always** passes a non-empty
`extra_headers` (merged from `agents.models.chatcmpl_helpers.HEADERS`, which always contains at
least `User-Agent`). This means **every real Copilot call made through the SDK hits this bug** and
fails server-side with `Github_copilotException - bad request: missing Editor-Version header for
IDE auth` — confirmed via a direct `httpx.AsyncClient.send` monkeypatch showing none of the
Copilot-required headers reach the wire once any non-empty `extra_headers` is present. Setting
`litellm.headers` (the mechanism `_configure_openrouter_attribution()` already uses for OpenRouter)
has the identical failure mode — it is *not* a safe alternative here.

**The fix** (`strix/config/models.py::_configure_github_copilot_headers()`, called from
`configure_sdk_model_defaults()`): set the SDK's own `HEADERS_OVERRIDE` context var
(`agents.models.chatcmpl_helpers.HEADERS_OVERRIDE`) to `strix.llm.copilot.github_copilot_extra_headers()`'s
output. `HEADERS_OVERRIDE` takes precedence over everything else `_merge_headers()` combines, so
the Copilot headers are present in `extra_headers` regardless of what else the SDK adds — which
keeps litellm's server-side check happy without needing to hit `validate_environment()` at all
(the caller supplies the exact same headers `validate_environment()` would have). Since
`configure_sdk_model_defaults()` runs synchronously, once, before any task spawning, the
context-var value propagates to every subsequently-spawned agent task (root + children) in the
same process — no per-call-site plumbing needed.

If litellm fixes this upstream (making `validate_environment()` run regardless of `extra_headers`),
`_configure_github_copilot_headers()` and `github_copilot_extra_headers()` become unnecessary —
check this before re-applying the delta after a litellm version bump.

## Known fragile points

- **`strix/interface/main.py`**: upstream may restructure `parse_arguments()` / `main()` /
  `warm_up_llm()` between releases. The Copilot delta needs: (a) `--auth-github-copilot` /
  `--github-copilot-host` args added *before* `args = parser.parse_args()`, with an early return
  right after parsing (mirroring how `--resume` and the `strix view` subcommand bypass the
  `--target` requirement); (b) the `main()` dispatch added right after `args = parse_arguments()`,
  before `check_docker_installed()` (Copilot auth needs no Docker); (c) the `warm_up_llm()` hook
  added right after `raw_model = (llm.model or "").strip()`, before the "unknown model name"
  check.
- **litellm's `github_copilot` internals** (`_build_authenticator()` in `strix/llm/copilot.py`
  subclasses `litellm.llms.github_copilot.authenticator.Authenticator`): if litellm renames or
  restructures `Authenticator._poll_for_access_token` / `_login` / `_get_github_headers`, the
  subclass will silently stop overriding them (Python duck-typing — no error, just reverts to
  litellm's default 60s poll window). Re-check these method names exist and match signatures
  after bumping litellm.

## Merge history

| Date | Upstream version | Notes |
|------|------------------|-------|
| 2026-07-22 | v1.2.0 (2bb730c) | Full re-architecture: reset fork `main` to upstream's tree (previously based on v0.8.3, 226 commits behind across a full SDK migration — see upstream's `openai-agents` adoption, new `strix/core/`, `strix/config/`, `strix/report/`, `strix/viewer/`, removal of the XML tool-call harness and FastAPI sidecar). Dropped Podman/FreeBSD support entirely (previously in `strix/runtime/podman_runtime.py`, `strix/utils/container_platform.py` FreeBSD bits, `vendor/*-stub` packages — all removed, not re-added). Re-implemented GitHub Copilot support from scratch on the new architecture as a thin wrapper around litellm's native `github_copilot` provider, replacing the old fork's much larger hand-rolled OAuth/header implementation. Old fork state preserved on `pre-merge-backup` branch. |
| 2026-07-22 | — (bugfix, same day) | Found and fixed a real litellm bug during live end-to-end testing against a GHE Cloud instance: litellm's `github_copilot` provider silently drops its own required request headers (`editor-version`, etc.) whenever the caller passes non-empty `extra_headers` — which the `openai-agents` SDK always does. Every real Copilot call failed with `missing Editor-Version header for IDE auth` until this was patched via `strix/config/models.py::_configure_github_copilot_headers()` (sets the SDK's `HEADERS_OVERRIDE` context var). See "The litellm header-injection bug" section above. Verified with a full `--scan-mode deep` run against a local CWE-22 test target using `github_copilot/claude-sonnet-5` — 1 HIGH finding correctly reported end-to-end. |