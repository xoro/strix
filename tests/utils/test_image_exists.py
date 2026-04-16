"""Tests for ``image_exists`` (sandbox image presence + CPU architecture)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from docker.errors import ImageNotFound

from strix.interface.utils import image_exists


class TestImageExists:
    """``image_exists`` considers OCI CPU architecture for multi-arch tags."""

    def test_missing_image(self) -> None:
        """No local image → False."""
        client = MagicMock()
        client.images.get.side_effect = ImageNotFound("none")
        assert image_exists(client, "ghcr.io/usestrix/strix-sandbox:0.1.13") is False

    def test_matching_arch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Local arch matches host expectation → True."""
        monkeypatch.delenv("STRIX_CONTAINER_PLATFORM", raising=False)
        monkeypatch.setattr("strix.utils.container_platform.platform.machine", lambda: "aarch64")
        client = MagicMock()
        img = MagicMock()
        img.attrs = {"Architecture": "arm64"}
        client.images.get.return_value = img
        assert image_exists(client, "ghcr.io/usestrix/strix-sandbox:0.1.13") is True

    def test_mismatched_arch_triggers_repull(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Wrong arch for this host (e.g. amd64 image on arm64) → False so startup can pull."""
        monkeypatch.delenv("STRIX_CONTAINER_PLATFORM", raising=False)
        monkeypatch.setattr("strix.utils.container_platform.platform.machine", lambda: "aarch64")
        client = MagicMock()
        img = MagicMock()
        img.attrs = {"Architecture": "amd64"}
        client.images.get.return_value = img
        assert image_exists(client, "ghcr.io/usestrix/strix-sandbox:0.1.13") is False

    def test_missing_architecture_attr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No Architecture in attrs (older API) → True to avoid blocking."""
        monkeypatch.delenv("STRIX_CONTAINER_PLATFORM", raising=False)
        monkeypatch.setattr("strix.utils.container_platform.platform.machine", lambda: "aarch64")
        client = MagicMock()
        img = MagicMock()
        img.attrs = {}
        client.images.get.return_value = img
        assert image_exists(client, "ghcr.io/usestrix/strix-sandbox:0.1.13") is True
