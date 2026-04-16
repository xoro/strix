import contextlib
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import httpx
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

from strix.config import Config
from strix.utils.container_platform import linux_container_platform

from . import SandboxInitializationError
from .runtime import AbstractRuntime, SandboxInfo


logger = logging.getLogger(__name__)

HOST_GATEWAY_HOSTNAME = "host.containers.internal"
PODMAN_TIMEOUT = 60
CONTAINER_TOOL_SERVER_PORT = 48081
# docker-entrypoint.sh: Caido (~30s) then proxy + tool server; often >60s before /health on host.
_FREEBSD_TOOL_SERVER_BOOTSTRAP_WAIT_SEC = 90

_FREEBSD_ENTRYPOINT_WRAPPER = (
    "sed 's/sudo tee/tee/g; s/sudo -E -u pentester //g; s/sudo -u pentester //g' "
    "/usr/local/bin/docker-entrypoint.sh > /tmp/entrypoint-nosudo.sh && "
    "chmod +x /tmp/entrypoint-nosudo.sh && "
    "sed -i 's|exec \"$@\"|tail -f /dev/null|' /tmp/entrypoint-nosudo.sh && "
    "bash /tmp/entrypoint-nosudo.sh"
)


def _podman_executable() -> str:
    """Absolute path to ``podman`` when on ``PATH`` (required for ``doas``/``sudoers`` ``cmd`` rules)."""
    return shutil.which("podman") or "podman"


def _podman_cli_argv() -> list[str]:
    """Argv prefix for Podman CLI subprocesses.

    FreeBSD has no rootless Podman; ``podman create`` / ``start`` must run as root.
    As a normal user, prepend ``sudo``/``doas`` with **-n** (non-interactive): password
    prompts do not work reliably from Python's subprocess, so ``NOPASSWD`` /
    ``permit nopass`` is required. Use the **resolved** podman path so it matches
    ``NOPASSWD: /usr/local/bin/podman`` and ``permit nopass … cmd /usr/local/bin/podman``.
    Alternatively run ``uv run strix`` under ``sudo -E`` (euid 0). PodmanClient (HTTP API)
    may still be used as a non-root ``operator`` member.
    """
    podman_exe = _podman_executable()
    if sys.platform.startswith("freebsd") and os.geteuid() != 0:
        if shutil.which("sudo"):
            return ["sudo", "-n", podman_exe]
        if shutil.which("doas"):
            return ["doas", "-n", podman_exe]
    return [podman_exe]


def _query_podman_info_socket() -> str | None:
    try:
        result = subprocess.run(  # noqa: S603
            _podman_cli_argv() + ["info", "--format", "{{.Host.RemoteSocket.Path}}"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            sock_path = result.stdout.strip()
            if sock_path and Path(sock_path).exists():
                return f"unix://{sock_path}"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


class PodmanRuntime(AbstractRuntime):
    def __init__(self) -> None:
        try:
            from podman import PodmanClient  # type: ignore[import-untyped]
            from podman.errors import APIError  # type: ignore[import-untyped]

            # Prefer explicit Podman URI, then Docker's (set by check_docker_connection on FreeBSD),
            # then auto-detect. Without DOCKER_HOST here, the first matching path from detection
            # can differ from the socket docker-py used for pull/ping.
            podman_uri = (
                os.getenv("CONTAINER_HOST")
                or os.getenv("DOCKER_HOST")
                or self._detect_podman_socket()
            )
            self.client: PodmanClient = PodmanClient(  # type: ignore[no-any-unimported]
                base_url=podman_uri, timeout=PODMAN_TIMEOUT
            )
            self._api_error_cls = APIError
            if not self.client.ping():
                msg = (
                    f"Connected to '{podman_uri}' but ping failed. "
                    "Ensure the Podman service is running: "
                    "'podman system service --time=0 &'"
                )
                raise SandboxInitializationError("Podman socket not responding", msg)  # noqa: TRY301
        except SandboxInitializationError:
            raise
        except Exception as e:
            raise SandboxInitializationError(
                "Podman is not available",
                "Please ensure Podman is installed, the Podman socket is running "
                "(e.g. service podman_service start on FreeBSD), and the Python package "
                "'podman' is installed (uv sync on FreeBSD). "
                f"Underlying error: {type(e).__name__}: {e}",
            ) from e

        self._scan_container: object | None = None
        self._tool_server_port: int | None = None
        self._tool_server_token: str | None = None
        self._container_ip: str | None = None

    @staticmethod
    def _is_freebsd() -> bool:
        return sys.platform.startswith("freebsd")

    @staticmethod
    def _detect_podman_socket() -> str:
        uid = os.getuid()

        candidates = [
            f"unix:///run/user/{uid}/podman/podman.sock",
            f"unix:///tmp/podman-run-{uid}/podman/podman.sock",
            "unix:///run/podman/podman.sock",
            "unix:///var/run/podman/podman.sock",
        ]

        for candidate in candidates:
            sock_path = candidate.replace("unix://", "")
            if Path(sock_path).exists():
                return candidate

        info_socket = _query_podman_info_socket()
        if info_socket:
            return info_socket

        raise SandboxInitializationError(
            "Podman socket not found",
            "No Podman socket found at any of the expected locations: "
            + ", ".join(c.replace("unix://", "") for c in candidates)
            + ". Start the Podman socket service: 'podman system service --time=0 &'",
        )

    def _find_available_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return cast("int", s.getsockname()[1])

    def _get_scan_id(self, agent_id: str) -> str:
        try:
            from strix.telemetry.tracer import get_global_tracer

            tracer = get_global_tracer()
            if tracer and tracer.scan_config:
                return str(tracer.scan_config.get("scan_id", "default-scan"))
        except (ImportError, AttributeError):
            pass
        return f"scan-{agent_id.split('-')[0]}"

    def _verify_image_available(self, image_name: str, max_retries: int = 3) -> None:
        from podman.errors import ImageNotFound  # type: ignore[import-untyped]

        for attempt in range(max_retries):
            try:
                image = self.client.images.get(image_name)
                if not image.id or not image.attrs:
                    raise ImageNotFound(  # type: ignore[misc]  # noqa: TRY301
                        f"Image {image_name} metadata incomplete"
                    )
            except (ImageNotFound, Exception):
                if attempt == max_retries - 1:
                    raise
                time.sleep(2**attempt)
            else:
                return

    def _recover_container_state(self, container: object) -> None:
        attrs = getattr(container, "attrs", {})
        config = attrs.get("Config", {})

        for env_var in config.get("Env", []):
            if env_var.startswith("TOOL_SERVER_TOKEN="):
                self._tool_server_token = env_var.split("=", 1)[1]
                break

        port_bindings = attrs.get("NetworkSettings", {}).get("Ports", {})
        port_key = f"{CONTAINER_TOOL_SERVER_PORT}/tcp"
        if port_bindings.get(port_key):
            binding = port_bindings[port_key]
            if isinstance(binding, list) and binding:
                host_port = int(binding[0].get("HostPort", 0))
                if host_port > 0:
                    self._tool_server_port = host_port

    @staticmethod
    def _start_container(container_name: str) -> None:
        result = subprocess.run(  # noqa: S603
            _podman_cli_argv() + ["start", container_name],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise SandboxInitializationError(
                "Failed to start container",
                f"podman start failed: {result.stderr.strip()}",
            )

    def _tool_server_published_host_port_cli(self, container_name: str) -> int | None:
        """Parse ``podman port`` for the tool-server container port (authoritative on FreeBSD)."""
        result = subprocess.run(  # noqa: S603
            [*_podman_cli_argv(), "port", container_name],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            return None
        needle = f"{CONTAINER_TOOL_SERVER_PORT}/tcp"
        for line in result.stdout.splitlines():
            line = line.strip()
            if needle not in line or "->" not in line:
                continue
            right = line.split("->", 1)[1].strip()
            if ":" not in right:
                continue
            port_str = right.rsplit(":", 1)[-1].rstrip("]")
            try:
                mapped = int(port_str)
            except ValueError:
                continue
            if mapped > 0:
                return mapped
        return None

    def _sync_tool_server_host_port_after_start(
        self, container_name: str, fallback_port: int
    ) -> None:
        """Refresh host port from ``podman port`` / inspect (Podman may omit maps in API attrs)."""
        if self._is_freebsd():
            mapped = self._tool_server_published_host_port_cli(container_name)
            if mapped is not None:
                self._tool_server_port = mapped
                return
        if self._tool_server_port is None or self._tool_server_port <= 0:
            self._tool_server_port = fallback_port

    def _wait_for_tool_server(self, max_retries: int = 30, timeout: int = 5) -> None:
        # FreeBSD: use the published host port on loopback. Bridge IPs from ``podman inspect`` are
        # often wrong for host-to-container HTTP with Podman on FreeBSD.
        host = self._resolve_host()
        port = CONTAINER_TOOL_SERVER_PORT if self._container_ip else self._tool_server_port
        health_url = f"http://{host}:{port}/health"
        logger.info("Waiting for tool server at %s", health_url)

        if self._is_freebsd():
            time.sleep(_FREEBSD_TOOL_SERVER_BOOTSTRAP_WAIT_SEC)
        else:
            time.sleep(5)

        attempts = max(max_retries, 45) if self._is_freebsd() else max_retries
        for attempt in range(attempts):
            try:
                with httpx.Client(trust_env=False, timeout=timeout) as client:
                    response = client.get(health_url)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("status") == "healthy":
                            return
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
                pass

            time.sleep(min(2**attempt * 0.5, 5))

        raise SandboxInitializationError(
            "Tool server failed to start",
            "Container initialization timed out. Please try again.",
        )

    def _create_container(self, scan_id: str, max_retries: int = 2) -> object:
        container_name = f"strix-scan-{scan_id}"
        image_name = Config.get("strix_image")
        if not image_name:
            raise ValueError("STRIX_IMAGE must be configured")

        self._verify_image_available(image_name)

        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                self._remove_existing_container(container_name)

                self._tool_server_port = self._find_available_port()
                self._tool_server_token = secrets.token_urlsafe(32)
                execution_timeout = Config.get("strix_sandbox_execution_timeout") or "120"

                env_vars = {
                    "PYTHONUNBUFFERED": "1",
                    "TOOL_SERVER_PORT": str(CONTAINER_TOOL_SERVER_PORT),
                    "TOOL_SERVER_TOKEN": self._tool_server_token,
                    "STRIX_SANDBOX_EXECUTION_TIMEOUT": str(execution_timeout),
                    "HOST_GATEWAY": HOST_GATEWAY_HOSTNAME,
                }

                if self._is_freebsd():
                    self._create_container_cli(
                        container_name,
                        image_name,
                        env_vars,
                        scan_id,
                    )
                else:
                    self.client.containers.create(
                        image_name,
                        name=container_name,
                        hostname=container_name,
                        platform=linux_container_platform(),
                        ports={f"{CONTAINER_TOOL_SERVER_PORT}/tcp": self._tool_server_port},
                        cap_add=["NET_ADMIN", "NET_RAW"],
                        labels={"strix-scan-id": scan_id},
                        environment=env_vars,
                        extra_hosts={HOST_GATEWAY_HOSTNAME: "host-gateway"},
                        tty=True,
                    )
                    self._start_container(container_name)

                saved_host_port = self._tool_server_port
                container_obj = self.client.containers.get(container_name)
                container_obj.reload()
                self._recover_container_state(container_obj)
                if self._tool_server_port is None or self._tool_server_port <= 0:
                    self._tool_server_port = saved_host_port
                self._sync_tool_server_host_port_after_start(container_name, saved_host_port)
                self._scan_container = container_obj
                self._wait_for_tool_server()

            except (RequestsConnectionError, RequestsTimeout) as e:
                last_error = e
                if attempt < max_retries:
                    self._reset_connection_state()
                    time.sleep(2**attempt)
            except Exception as e:  # noqa: BLE001
                last_error = e
                if attempt < max_retries:
                    self._reset_connection_state()
                    time.sleep(2**attempt)
            else:
                return container_obj

        raise SandboxInitializationError(
            "Failed to create container",
            f"Container creation failed after {max_retries + 1} attempts: {last_error}",
        ) from last_error

    def _podman_rm_force_cli(self, container_name: str) -> None:
        """Run ``podman rm -f`` via CLI (FreeBSD: ``doas -n`` / ``sudo -n`` + absolute ``podman``)."""
        subprocess.run(  # noqa: S603
            [*_podman_cli_argv(), "rm", "-f", container_name],
            capture_output=True,
            timeout=180,
            check=False,
        )

    def _remove_existing_container(self, container_name: str) -> None:
        """Drop a leftover scan container: Podman API first, then CLI."""
        from podman.errors import NotFound  # type: ignore[import-untyped]

        try:
            existing = self.client.containers.get(container_name)
        except NotFound:
            time.sleep(0.5)
            return
        except (self._api_error_cls, OSError, RequestsConnectionError, RequestsTimeout) as e:
            logger.debug(
                "Podman API get failed for %s (%s); falling back to CLI rm",
                container_name,
                e,
            )
            self._podman_rm_force_cli(container_name)
            time.sleep(0.5)
            return

        try:
            with contextlib.suppress(Exception):
                existing.stop(timeout=10)
            existing.remove(force=True)
        except (self._api_error_cls, OSError, RequestsConnectionError, RequestsTimeout) as e:
            logger.debug(
                "Podman API remove failed for %s (%s); falling back to CLI rm",
                container_name,
                e,
                exc_info=True,
            )
            self._podman_rm_force_cli(container_name)
        time.sleep(0.5)

    def _reset_connection_state(self) -> None:
        self._tool_server_port = None
        self._tool_server_token = None
        self._container_ip = None

    def _create_container_cli(
        self,
        container_name: str,
        image_name: str,
        env_vars: dict[str, str],
        scan_id: str,
    ) -> None:
        # Map the host port we already chose to the tool server port in the container (same as
        # DockerRuntime ports={f"{CONTAINER_TOOL_SERVER_PORT}/tcp": host_port}). A lone "-p 48081"
        # does not bind to self._tool_server_port and breaks publish + health checks on FreeBSD.
        cmd: list[str] = _podman_cli_argv() + [
            "create",
            "--platform",
            linux_container_platform(),
            "--name",
            container_name,
            "--hostname",
            container_name,
            "-p",
            f"{self._tool_server_port}:{CONTAINER_TOOL_SERVER_PORT}",
            "--cap-add",
            "NET_ADMIN",
            "--cap-add",
            "NET_RAW",
            "--user",
            "root",
            "--entrypoint",
            "bash",
            "--tty",
            "--label",
            f"strix-scan-id={scan_id}",
        ]
        for key, value in env_vars.items():
            cmd.extend(["-e", f"{key}={value}"])

        cmd.extend([image_name, "-c", _FREEBSD_ENTRYPOINT_WRAPPER])

        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise SandboxInitializationError(
                "Failed to create container",
                f"podman create failed: {result.stderr.strip()}",
            )
        self._start_container(container_name)

    def _get_or_create_container(self, scan_id: str) -> object:
        from podman.errors import NotFound  # type: ignore[import-untyped]

        container_name = f"strix-scan-{scan_id}"

        if self._scan_container:
            try:
                self._scan_container.reload()  # type: ignore[union-attr]
                if self._scan_container.status == "running":  # type: ignore[union-attr]
                    return self._scan_container
            except NotFound:
                self._scan_container = None
                self._tool_server_port = None
                self._tool_server_token = None
                self._container_ip = None

        try:
            container = self.client.containers.get(container_name)
            container.reload()

            if container.status != "running":
                self._start_container(container_name)
                time.sleep(2)

            self._scan_container = container
            self._recover_container_state(container)
        except NotFound:
            pass
        else:
            return container

        try:
            containers = self.client.containers.list(
                all=True, filters={"label": f"strix-scan-id={scan_id}"}
            )
            if containers:
                container = containers[0]
                if container.status != "running":
                    c_name = getattr(container, "name", container_name)
                    self._start_container(c_name)
                    time.sleep(2)

                self._scan_container = container
                self._recover_container_state(container)
                return container
        except Exception:  # noqa: BLE001, S110
            pass

        return self._create_container(scan_id)

    def _copy_local_directory_to_container(
        self, container: object, local_path: str, target_name: str | None = None
    ) -> None:
        import tarfile
        from io import BytesIO

        try:
            local_path_obj = Path(local_path).resolve()
            if not local_path_obj.exists() or not local_path_obj.is_dir():
                return

            container_name = getattr(container, "name", None)

            if self._is_freebsd() and container_name:
                self._copy_local_directory_to_container_cli(
                    container_name,
                    local_path_obj,
                    target_name,
                )
                return

            tar_buffer = BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                for item in local_path_obj.rglob("*"):
                    if item.is_file():
                        rel_path = item.relative_to(local_path_obj)
                        arcname = Path(target_name) / rel_path if target_name else rel_path
                        tar.add(item, arcname=arcname)

            tar_buffer.seek(0)
            container.put_archive("/workspace", tar_buffer.getvalue())  # type: ignore[union-attr]
            container.exec_run(  # type: ignore[union-attr]
                "chown -R pentester:pentester /workspace && chmod -R 755 /workspace",
                user="root",
            )
        except (OSError, Exception):  # noqa: S110
            pass

    def _copy_local_directory_to_container_cli(
        self, container_name: str, local_path: Path, target_name: str | None = None
    ) -> None:
        try:
            dest_dir = f"/workspace/{target_name}" if target_name else "/workspace"
            subprocess.run(  # noqa: S603
                _podman_cli_argv() + ["exec", container_name, "mkdir", "-p", dest_dir],  # noqa: S607
                check=True,
                capture_output=True,
            )
            subprocess.run(  # noqa: S603
                _podman_cli_argv() + ["cp", f"{local_path}/.", f"{container_name}:{dest_dir}/"],  # noqa: S607
                check=True,
                capture_output=True,
            )
            subprocess.run(  # noqa: S603
                _podman_cli_argv()  # noqa: S607
                + [
                    "exec",
                    container_name,
                    "chmod",
                    "-R",
                    "755",
                    "/workspace",
                ],
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError):
            pass

    async def create_sandbox(
        self,
        agent_id: str,
        existing_token: str | None = None,
        local_sources: list[dict[str, str]] | None = None,
    ) -> SandboxInfo:
        scan_id = self._get_scan_id(agent_id)
        container = self._get_or_create_container(scan_id)

        source_copied_key = f"_source_copied_{scan_id}"
        if local_sources and not hasattr(self, source_copied_key):
            for index, source in enumerate(local_sources, start=1):
                source_path = source.get("source_path")
                if not source_path:
                    continue
                target_name = (
                    source.get("workspace_subdir") or Path(source_path).name or f"target_{index}"
                )
                self._copy_local_directory_to_container(container, source_path, target_name)
            setattr(self, source_copied_key, True)

        container_id = getattr(container, "id", None)
        if container_id is None:
            raise RuntimeError("Podman container ID is unexpectedly None")

        token = existing_token or self._tool_server_token
        if self._tool_server_port is None or token is None:
            raise RuntimeError("Tool server not initialized")

        host = self._resolve_host()
        port = CONTAINER_TOOL_SERVER_PORT if self._container_ip else self._tool_server_port
        api_url = f"http://{host}:{port}"

        await self._register_agent(api_url, agent_id, token)

        return {
            "workspace_id": container_id,
            "api_url": api_url,
            "auth_token": token,
            "tool_server_port": port,
            "agent_id": agent_id,
        }

    async def _register_agent(self, api_url: str, agent_id: str, token: str) -> None:
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(
                    f"{api_url}/register_agent",
                    params={"agent_id": agent_id},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30,
                )
                response.raise_for_status()
        except httpx.RequestError:
            pass

    async def get_sandbox_url(self, container_id: str, port: int) -> str:
        from podman.errors import NotFound  # type: ignore[import-untyped]

        try:
            self.client.containers.get(container_id)
        except NotFound:
            raise ValueError(f"Container {container_id} not found.") from None
        return f"http://{self._resolve_host()}:{port}"

    def _resolve_host(self) -> str:
        if self._container_ip:
            return self._container_ip
        container_host = os.getenv("CONTAINER_HOST", "")
        if container_host:
            from urllib.parse import urlparse

            parsed = urlparse(container_host)
            if parsed.scheme in ("tcp", "http", "https") and parsed.hostname:
                return parsed.hostname
        return "127.0.0.1"

    async def destroy_sandbox(self, container_id: str) -> None:
        from podman.errors import NotFound  # type: ignore[import-untyped]

        try:
            container = self.client.containers.get(container_id)
            container.stop()
            container.remove()
            self._scan_container = None
            self._tool_server_port = None
            self._tool_server_token = None
            self._container_ip = None
        except (NotFound, Exception):  # noqa: S110
            pass

    def cleanup(self) -> None:
        if self._scan_container is not None:
            container_name = getattr(self._scan_container, "name", None)
            self._scan_container = None
            self._tool_server_port = None
            self._tool_server_token = None
            self._container_ip = None

            if container_name is None:
                return

            subprocess.Popen(  # noqa: S603
                _podman_cli_argv() + ["rm", "-f", container_name],  # noqa: S607
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
