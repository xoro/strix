"""Minimal tiktoken API for litellm when PyPI wheels are unavailable (e.g. FreeBSD).

Counts are a rough ``len(text) // 4`` heuristic (same order as OpenAI estimates).
Real tiktoken is Rust-based; building from source requires a Rust toolchain.
"""

from __future__ import annotations

from typing import Any


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


class Encoding:
    """Subset of ``tiktoken.Encoding`` used by litellm."""

    def __init__(self, name: str) -> None:
        self.name = name

    def encode(self, text: str, *, disallowed_special: Any = ()) -> list[int]:
        n = _approx_tokens(text)
        return [0] * n

    def decode(self, tokens: list[int]) -> str:
        return ""


_encodings: dict[str, Encoding] = {}


def get_encoding(encoding_name: str) -> Encoding:
    if encoding_name not in _encodings:
        _encodings[encoding_name] = Encoding(encoding_name)
    return _encodings[encoding_name]


def encoding_for_model(model_name: str) -> Encoding:  # noqa: ARG001
    return get_encoding("cl100k_base")


__all__ = ["Encoding", "encoding_for_model", "get_encoding"]
