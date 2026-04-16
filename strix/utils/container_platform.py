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
