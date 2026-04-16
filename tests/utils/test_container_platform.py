"""Tests for ``strix.utils.container_platform``."""

import pytest

from strix.utils.container_platform import linux_container_platform


class TestLinuxContainerPlatform:
    """Tests for ``linux_container_platform``."""

    @pytest.mark.parametrize(
        ("machine", "expected"),
        [
            ("aarch64", "linux/arm64"),
            ("ARM64", "linux/arm64"),
            ("arm64", "linux/arm64"),
            ("x86_64", "linux/amd64"),
            ("amd64", "linux/amd64"),
        ],
    )
    def test_arch_mapping(
        self, monkeypatch: pytest.MonkeyPatch, machine: str, expected: str
    ) -> None:
        """Maps host ``machine`` to the expected Linux OCI platform."""
        monkeypatch.delenv("STRIX_CONTAINER_PLATFORM", raising=False)
        monkeypatch.setattr("strix.utils.container_platform.platform.machine", lambda: machine)
        assert linux_container_platform() == expected

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``STRIX_CONTAINER_PLATFORM`` overrides automatic detection."""
        monkeypatch.setenv("STRIX_CONTAINER_PLATFORM", "linux/amd64")
        monkeypatch.setattr("strix.utils.container_platform.platform.machine", lambda: "aarch64")
        assert linux_container_platform() == "linux/amd64"
