# Installing Strix (xoro/strix fork)

This guide covers installing **[github.com/xoro/strix](https://github.com/xoro/strix)** — including extra features in this tree (for example GitHub Copilot integration and Podman on FreeBSD). See [MERGE.md](MERGE.md) for merge and maintenance notes.

Installation here is **from source** with **`git`** and **`uv`**. If you prefer a **prebuilt CLI** from the Strix project, use [strix.ai](https://strix.ai).

It covers getting Strix running on **macOS**, **Debian / Linux**, **Windows**, and **FreeBSD**. You need a **running container runtime** (Docker on most systems, Podman on FreeBSD in this fork) and an **LLM** (API key or GitHub Copilot).

- **Quick reference (providers & models):** [LLM provider overview](https://docs.strix.ai/llm-providers/overview)

---

## What you need on every platform

1. **Sandbox image** — Strix runs tools inside `ghcr.io/usestrix/strix-sandbox` (pulled automatically on first run when Docker/Podman works). Override with `STRIX_IMAGE` if required.
2. **LLM configuration** — Either:
   - **API key:** set `STRIX_LLM` (LiteLLM-style name, e.g. `openai/gpt-5.4`, `anthropic/claude-sonnet-4-6`) and `LLM_API_KEY`, or  
   - **GitHub Copilot:** set `STRIX_LLM` to `github_copilot/<model>` and run `uv run strix --auth-github-copilot` once (no `LLM_API_KEY`).
3. **Optional:** `~/.strix/cli-config.json` stores settings after you run Strix; you can also rely on environment variables only.

---

## Installation from source (`git` + `uv`)

Use this on **macOS**, **Linux** (including **ARM64**), **Windows**, and **FreeBSD**.

### Prerequisites

Install the following **before** cloning the repository. Every OS needs **Python 3.12+**, **[uv](https://docs.astral.sh/uv/)**, **Git**, and a **container runtime** (Docker on macOS, Linux, and Windows; Podman on FreeBSD for this fork).

#### macOS

| Requirement | Notes |
|-------------|--------|
| **Python 3.12+** | Install from [python.org](https://www.python.org/downloads/macos/), **Homebrew** (`brew install python@3.12`), or another manager. Check with `python3 --version`. |
| **uv** | `curl -LsSf https://astral.sh/uv/install.sh | sh` or `brew install uv`. |
| **Git** | Xcode Command Line Tools (`xcode-select --install`) or `brew install git`. |
| **Docker** | [Docker Desktop for Mac](https://docs.docker.com/desktop/install/mac-install/) — start Docker and confirm `docker info`. |

#### Debian and Ubuntu

| Requirement | Notes |
|-------------|--------|
| **Python 3.12+** | `sudo apt update && sudo apt install -y python3.12 python3.12-venv` (or `python3` if 3.12+ is the default). Check with `python3 --version`. |
| **uv** | `curl -LsSf https://astral.sh/uv/install.sh | sh` or see [uv install docs](https://docs.astral.sh/uv/getting-started/installation/). |
| **Git** | `sudo apt install -y git` |
| **Docker** | Install [Docker Engine](https://docs.docker.com/engine/install/debian/) (or Ubuntu equivalent), then `sudo usermod -aG docker "$USER"` and log out/in. Confirm with `docker info`. |

**Other Linux distributions** — Install Python 3.12+, Git, and Docker (or compatible engine) using your package manager; then install **uv** with the official installer above.

#### Windows

| Requirement | Notes |
|-------------|--------|
| **Python 3.12+** | Install from [python.org](https://www.python.org/downloads/windows/) or **winget** / **Chocolatey**. Check **“Add python.exe to PATH”** during setup. Verify with `py -3.12 --version` or `python --version`. |
| **uv** | `curl -LsSf https://astral.sh/uv/install.sh | sh` in **Git Bash**, or `pip install uv` / `winget install astral-sh.uv` if available. |
| **Git** | [Git for Windows](https://git-scm.com/download/win) (includes Git Bash). |
| **Docker** | [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) with **WSL 2** backend recommended. Start Docker and confirm `docker info` in PowerShell or WSL. |

#### FreeBSD

| Requirement | Notes |
|-------------|--------|
| **Python 3.12+** | `sudo pkg install -y python312` (or newer `python3xx` if available). Check with `python3.12 --version`. |
| **Rust (rustc + cargo)** | **Required before `uv sync`.** Some dependencies (for example **`pydantic-core`**) have **no FreeBSD wheels** on PyPI and are built with **maturin**; without a system compiler, the isolated build fails with errors like `Unsupported platform: 312` or `can't find Rust compiler`. Install Rust, then confirm `rustc --version` and `cargo --version` are on your `PATH`: `sudo pkg install -y rust` (or `lang/rust` from ports, depending on your tree). |
| **uv** | **Astral:** one-line install from [uv installation](https://docs.astral.sh/uv/getting-started/installation/) (typically `curl -LsSf https://astral.sh/uv/install.sh` piped to `sh`). **Or** use packages: `sudo pkg install -y uv` when your repositories provide it (version may lag the installer). |
| **Git** | `sudo pkg install -y git` |
| **Podman** | `sudo pkg install -y podman` — configure and start Podman per [Podman on FreeBSD](https://podman.io/). This fork defaults to **Podman** on FreeBSD (`strix/config/config.py`); use `STRIX_RUNTIME_BACKEND=podman` if you need to force it. Confirm with `podman info`. |
| **GNU make (`gmake`)** | **Only if** you install **development** dependencies (`make setup-dev` / `uv sync` with dev tools). Building **ruff** from source runs `gmake` while compiling **jemalloc**; without it you get `failed to execute command` / “No such file or directory” when `gmake` is missing. Install: `sudo pkg install -y gmake`. **Not needed** for a minimal CLI install — use `make install` or `uv sync --no-dev` (see below). |

### Clone and install dependencies

**FreeBSD — two paths:**

1. **Run Strix only (recommended on FreeBSD)** — production dependencies only; skips **ruff**, **pytest**, and other dev tools that may need long source builds. You still need **Rust** for packages like **`pydantic-core`** (see table above).

   ```bash
   git clone https://github.com/xoro/strix.git
   cd strix
   make install
   # same as: uv sync --no-dev
   ```

2. **Full developer setup** (linters, tests, pre-commit) — install **Rust** and **`gmake`**, then:

   ```bash
   git clone https://github.com/xoro/strix.git
   cd strix
   make setup-dev
   # equivalent: uv sync && uv run pre-commit install
   ```

**Other platforms** — typical flow:

```bash
git clone https://github.com/xoro/strix.git
cd strix
make setup-dev
# equivalent: uv sync && uv run pre-commit install
```

### Configure LLM and run

```bash
export STRIX_LLM="openai/gpt-5.4"
export LLM_API_KEY="your-api-key"

uv run strix --target https://example.com
```

**GitHub Copilot:**

```bash
export STRIX_LLM="github_copilot/gpt-4o"
uv run strix --auth-github-copilot
uv run strix --target https://example.com
```

### FreeBSD — sandbox image

After prerequisites above, pull the sandbox image once (tag matches `STRIX_IMAGE` / `strix/config/config.py`).

To use the **latest tag expected by your checkout**, read it from the repo: open `strix/config/config.py` and copy the value of **`strix_image`** (or run `grep strix_image strix/config/config.py` from the repository root after `git pull`).

```sh
podman pull ghcr.io/usestrix/strix-sandbox:0.1.13
```

If `uv run strix` fails to reach the runtime, check `podman info` and that **Podman** can run containers.

---

## Verify the install

```bash
cd /path/to/strix
uv run strix --help
```

Run a minimal non-interactive check (requires LLM + runtime):

```bash
uv run strix -n --target https://example.com --scan-mode quick
```

---

## Troubleshooting

| Issue | What to try |
|--------|-------------|
| Docker/Podman not running | Start **Docker Desktop** or **`systemctl start docker`** / Podman service; run `docker info` or `podman info`. |
| Sandbox image pull fails | `docker pull ghcr.io/usestrix/strix-sandbox:0.1.13` (or your `STRIX_IMAGE`). Check network and registry access. |
| `LLM_API_KEY` errors | Export the key for your provider; for Copilot models use `uv run strix --auth-github-copilot` instead of an API key. |
| `uv` not found | Install [uv](https://docs.astral.sh/uv/) and ensure it is on your `PATH`, or invoke it with an absolute path. |
| FreeBSD: Docker expected | Use Podman and this fork’s defaults, or set `STRIX_RUNTIME_BACKEND=podman`. |
| FreeBSD: `pydantic-core`, `maturin`, “Unsupported platform”, or “Rust not found” during `uv sync` | Install a **system Rust** toolchain so source builds can compile (see **Rust** row under FreeBSD prerequisites). Ensure `rustc` is on `PATH` in the same shell, then run `uv sync` again. |
| FreeBSD: `ruff` / `tikv-jemalloc-sys` / `failed to execute command` / `gmake` during dev install | Prefer **`make install`** or **`uv sync --no-dev`** if you only need the CLI. If you need dev tools (`make setup-dev`), install **GNU make**: `sudo pkg install -y gmake`, ensure `gmake` is on `PATH`, then retry. |

---

## Next steps

- **Repository:** [github.com/xoro/strix](https://github.com/xoro/strix) — issues and development on `main`.  
- **Docs:** [docs.strix.ai](https://docs.strix.ai) — scan modes, CI/CD, skills, providers.  
- **Quality checks:** `make check-all`, `make test`.  
- **Security / scope:** Only test systems you are authorized to test.
