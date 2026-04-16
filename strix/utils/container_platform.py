"""Host-appropriate OCI platform for Linux sandbox images (``linux/arm64`` vs ``linux/amd64``)."""

import os
import platform


def linux_container_platform() -> str:
    """Return a docker/podman ``platform`` string for the current CPU architecture.

    ``STRIX_CONTAINER_PLATFORM`` overrides the default (for example ``linux/amd64`` to
    force x86_64 images under emulation on ARM hosts).
    """
    override = os.environ.get("STRIX_CONTAINER_PLATFORM", "").strip()
    if override:
        return override
    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        return "linux/arm64"
    return "linux/amd64"


def normalize_oci_cpu_arch(arch: str) -> str:
    """Normalize CPU arch strings from OCI / ``docker image inspect``."""
    token = arch.strip().lower()
    if token in ("aarch64", "arm64"):
        return "arm64"
    if token in ("x86_64", "amd64"):
        return "amd64"
    return token


def expected_image_cpu_architecture() -> str:
    """CPU architecture (e.g. ``arm64``, ``amd64``) Strix expects for the sandbox image."""
    plat = linux_container_platform()
    parts = plat.split("/", 1)
    cpu = parts[1] if len(parts) == 2 else "amd64"
    return normalize_oci_cpu_arch(cpu)
