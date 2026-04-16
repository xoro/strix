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
| **GCC (`gfortran`)** | **Fortran** is required only if you build **SciPy** and similar stacks from source (for example after re-adding **`scrubadub`** / **sklearn**). There is **no** separate `gfortran` package in `pkg search` — **`gfortran`** ships with **GCC**. Install a compiler package such as **`gcc14`**, then optionally the **`gcc`** meta-port so **`/usr/local/bin/gfortran`** and **`gcc`** exist: `sudo pkg install -y gcc14 gcc`. Confirm with `gfortran --version`. For **Meson** builds you also want **`pkgconf`** (provides **`pkg-config`**): `sudo pkg install -y pkgconf`. The default **xoro/strix** tree **omits scrubadub on FreeBSD**, so a normal **`uv sync`** does **not** need GCC for Fortran unless you change dependencies. |
| **uv** | **Astral:** one-line install from [uv installation](https://docs.astral.sh/uv/getting-started/installation/) (typically `curl -LsSf https://astral.sh/uv/install.sh` piped to `sh`). **Or** use packages: `sudo pkg install -y uv` when your repositories provide it (version may lag the installer). |
| **Git** | `sudo pkg install -y git` |
| **Podman** | `sudo pkg install -y podman` — see [Podman on FreeBSD](https://podman.io/). This fork defaults to **Podman** (`strix/config/config.py`); use `STRIX_RUNTIME_BACKEND=podman` if needed. **`podman info`** and **`podman ps`** can work even when **`/var/run/podman/podman.sock`** is missing: Strix uses the **`docker`** **Python** SDK, which talks to Podman’s **Docker-compatible HTTP API** on that socket. Start the API listener (as root), e.g. **`mkdir -p /var/run/podman`** then **`podman system service --time=0 unix:///var/run/podman/podman.sock`** (foreground; use **`&`**, **tmux**, or an **rc.d** wrapper for persistence). Then **`ls -l /var/run/podman/podman.sock`** should succeed. Default **`DOCKER_HOST=unix:///var/run/podman/podman.sock`** is set when unset. You do **not** need the **`docker`** OS package. Podman is **root-only** on FreeBSD — use **`sudo -E env HOME=$HOME PATH=$PATH uv run strix …`** from a normal user so **`$HOME`** keeps **GitHub Copilot** tokens (`-E` preserves the environment). **Linux** sandbox images need **`podman pull --platform linux/amd64 …`** on FreeBSD. |
| **GNU make (`gmake`)** | **If** you build **ruff** from source (for example after re-adding it to dev dependencies), the **jemalloc** step may invoke **`gmake`**. Without it you can see `failed to execute command` / “No such file or directory”. Install: `sudo pkg install -y gmake`. The default **dev** dependency set on **FreeBSD omits `ruff`** (see below) to avoid huge Rust builds on small hosts, so **`gmake`** is often unnecessary for `uv sync` on FreeBSD. **Not needed** for a minimal CLI install — use `make install` or `uv sync --no-dev`. |

### Clone and install dependencies

**FreeBSD — two paths:**

1. **Run Strix only (recommended on FreeBSD)** — production dependencies only; skips **pytest**, **mypy**, and other dev-only tools. You still need **Rust** for packages like **`pydantic-core`** (see table above).

   ```bash
   git clone https://github.com/xoro/strix.git
   cd strix
   make install
   # same as: uv sync --no-dev
   ```

2. **Full developer setup** (tests, pre-commit, type-checking) — install **Rust** first. **`gmake`** is only needed if you build tools that require it (see table). Then:

   ```bash
   git clone https://github.com/xoro/strix.git
   cd strix
   make setup-dev
   # equivalent: uv sync && uv run pre-commit install
   ```

   **`ruff`**, **`scrubadub`**, and **PyInstaller** are omitted on FreeBSD in `pyproject.toml`: there are no **ruff** wheels on PyPI for FreeBSD, and building it from source is a large **Rust/maturin** compile that often hits **OOM (SIGKILL)** on small **ARM64** VPS hosts. **`scrubadub`** pulls **scikit-learn** → **SciPy**, which has no FreeBSD wheels; building **SciPy** needs **Fortran** (`gfortran`), **`pkgconf`**, and a lot of RAM. Telemetry redaction on FreeBSD uses a **regex-only** fallback (no **scrubadub**). **`make format`** and **`make lint`** use `uv run ruff`; on FreeBSD they will fail unless you install **ruff** some other way or run those targets on **Linux/macOS/Windows** or **CI**. **PyInstaller** has no reliable FreeBSD story; use **GitHub Actions** / another OS to produce release binaries (`scripts/build.sh`).

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

Strix passes the correct **`--platform`** when it pulls the image (`linux/arm64` on AArch64 hosts, `linux/amd64` on typical x86_64). To pre-pull manually, match your CPU (see **`uname -m`**: **`aarch64`** / **`arm64`** → **`linux/arm64`**, else usually **`linux/amd64`**):

```sh
# AArch64 (Apple Silicon, ARM servers, AArch64 FreeBSD, …)
podman pull --platform linux/arm64 ghcr.io/usestrix/strix-sandbox:0.1.13

# x86_64 / amd64 hosts
podman pull --platform linux/amd64 ghcr.io/usestrix/strix-sandbox:0.1.13
```

Override automatic selection if needed: **`STRIX_CONTAINER_PLATFORM=linux/amd64`** (for example to force the amd64 image with emulation on ARM).

If Strix says the **container engine** is unavailable: ensure **`podman system service`** is running so **`/var/run/podman/podman.sock`** exists (see **Podman** row above). **`podman info`** alone is not enough to verify the API socket.

**Boot (`podman_service` / rc.d):** the **podman** package ships **`/usr/local/etc/rc.d/podman`** and **`podman_service`** (the latter runs the Docker-compatible API). As root: **`sysrc podman_enable=YES`**, **`sysrc podman_service_enable=YES`**, then **`service podman start`** and **`service podman_service start`**. Confirm with **`service podman_service status`** and **`ls -l /var/run/podman/podman.sock`**. The **`podman`** script may not implement **`service podman status`** — that is expected. For a one-off shell, use **`podman system service --time=0 unix:///var/run/podman/podman.sock`** (see **Podman** row).

**Socket group (`operator`):** the service socket is typically **`root:operator`** with mode **`0770`**. To run **`uv run strix`** as a normal user (not **`sudo`**), add your account to **`operator`**: **`sudo pw groupmod operator -m youruser`**, then log out and back in (or start a new login session). Alternatively use **`sudo -E env HOME=$HOME PATH=$PATH uv run strix …`** so the client can open the socket as root while keeping **`$HOME`** for GitHub Copilot.

**CNI / `pf`:** if **`podman start`** fails with **`The pf kernel module must be loaded to support ipMasq networks`**, Podman’s default bridge network needs the **pf** module. As root: **`kldload pf`** (verify with **`kldstat | grep pf`**). Persist across reboots with **`pf_load="YES"`** in **`/boot/loader.conf`**. Enabling the firewall also loads it: **`sysrc pf_enable=YES`** and a valid **`/etc/pf.conf`**, then **`service pf start`** — or ask your admin if **`pf`** is intentionally disabled.

**ZFS (optional):** if Podman’s store was under **`zroot/ROOT/…`** and **`podman`/`zfs destroy`** errors mention **dependent clones** across boot environments, move **`graphroot`** to a dedicated dataset (e.g. **`zroot/podman`** mounted at **`/var/db/containers`**) per **`/usr/local/etc/containers/storage.conf`** — see **Podman**/**ZFS** docs or your sysadmin runbook.

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
| FreeBSD: `ruff` build **`signal: 9 (SIGKILL)`** / **maturin** / **cargo** OOM | Usually **out of memory** while compiling **ruff** from source (common on **ARM64** with little RAM). This tree **excludes `ruff` from dev dependencies on FreeBSD** so `uv sync` does not pull it. **`git pull`**, run **`uv sync`** again, and use **`make install`** / **`uv sync --no-dev`** if you only need the CLI. To use **ruff** anyway, add swap/RAM, try **`CARGO_BUILD_JOBS=1`**, or run **format/lint** on another OS / CI. |
| FreeBSD: `ruff` / `tikv-jemalloc-sys` / `failed to execute command` / `gmake` during dev install | If you **re-add** **ruff** or hit **jemalloc** in another package: install **GNU make**: `sudo pkg install -y gmake` (spelled **`gmake`**, not `gmak`), ensure `gmake` is on `PATH`, then retry. Otherwise prefer the default tree that **omits ruff** on FreeBSD. |
| FreeBSD: `pkg: No packages available … gmak` | The package is **`gmake`**: `sudo pkg install -y gmake`. |
| FreeBSD: **PyInstaller** / bootloader / `ieeefp.h` / `__BEGIN_DECLS` during `uv sync` | Current trees skip PyInstaller on FreeBSD. **`git pull`** and run **`uv sync`** again. Release binaries are built on CI / non-FreeBSD hosts. |
| FreeBSD: **pre-commit** / **ruff** hooks fail | The **ruff-pre-commit** hooks expect a supported platform binary. Skip them for one commit: `SKIP=ruff-lint,ruff-format git commit …`, or rely on **CI** for lint/format. |
| FreeBSD: **`scipy`** / **`scikit-learn`** / **Unknown compiler `gfortran`** / **`pkg-config`** during `uv sync` | **`scrubadub`** pulls **sklearn** → **SciPy**, which often builds from source on FreeBSD. This tree **omits scrubadub on FreeBSD** (regex redaction in telemetry instead). **`git pull`** and **`uv sync`** again. To build SciPy yourself you would need **`pkg install pkgconf`** and a GCC with **`gfortran`** (e.g. **`gcc14`**) plus enough RAM/swap. |
| FreeBSD: **container engine** / **`podman.sock`** missing / Strix cannot pull image | **`service podman start`** may not open the **Docker API** socket. Run **`podman system service --time=0 unix:///var/run/podman/podman.sock`** (see **Podman** prerequisite row). Verify **`ls -l /var/run/podman/podman.sock`**. |

---

## Next steps

- **Repository:** [github.com/xoro/strix](https://github.com/xoro/strix) — issues and development on `main`.  
- **Docs:** [docs.strix.ai](https://docs.strix.ai) — scan modes, CI/CD, skills, providers.  
- **Quality checks:** `make check-all`, `make test`.  
- **Security / scope:** Only test systems you are authorized to test.
