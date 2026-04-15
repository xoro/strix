"""Pure-Python stand-in for PyPI ``fastuuid``.

Upstream ships Rust wheels for Linux/macOS/Windows only; building from source on
FreeBSD pulls ``maturin`` and often fails without a full Rust toolchain.

``litellm`` imports ``fastuuid`` as a drop-in for :mod:`uuid` (see ``litellm._uuid``).
Re-export the standard library API unchanged.
"""

from uuid import *  # noqa: F403
