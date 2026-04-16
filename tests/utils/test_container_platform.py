"""Tests for ``strix.utils.container_platform``."""

import pytest

from strix.utils.container_platform import (
    expected_image_cpu_architecture,
    linux_container_platform,
    normalize_oci_cpu_arch,
)


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


class TestNormalizeOciCpuArch:
    """Tests for ``normalize_oci_cpu_arch``."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("arm64", "arm64"),
            ("aarch64", "arm64"),
            ("AMD64", "amd64"),
            ("x86_64", "amd64"),
            ("amd64", "amd64"),
        ],
    )
    def test_aliases(self, raw: str, expected: str) -> None:
        """Common synonyms map to canonical tokens."""
        assert normalize_oci_cpu_arch(raw) == expected


class TestExpectedImageCpuArchitecture:
    """Tests for ``expected_image_cpu_architecture``."""

    def test_follows_linux_container_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Matches the CPU half of ``linux_container_platform()``."""
        monkeypatch.delenv("STRIX_CONTAINER_PLATFORM", raising=False)
        monkeypatch.setattr("strix.utils.container_platform.platform.machine", lambda: "aarch64")
        assert expected_image_cpu_architecture() == "arm64"

    def test_respects_platform_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``STRIX_CONTAINER_PLATFORM`` forces the expected arch."""
        monkeypatch.setenv("STRIX_CONTAINER_PLATFORM", "linux/amd64")
        monkeypatch.setattr("strix.utils.container_platform.platform.machine", lambda: "aarch64")
        assert expected_image_cpu_architecture() == "amd64"
