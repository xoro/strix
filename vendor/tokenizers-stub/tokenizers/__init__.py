"""Minimal ``tokenizers.Tokenizer`` for litellm import on platforms without HF wheels.

litellm only needs the class to exist at import time; with
``litellm.disable_hf_tokenizer_download=True`` (set for FreeBSD in Strix),
OpenAI/tiktoken-based counting is used instead of Hugging Face tokenizers.
"""

from __future__ import annotations

from typing import Any


class Encoding:
    def __init__(self, ids: list[int]) -> None:
        self.ids = ids


class Tokenizer:
    """Stub; real HF tokenizers are not used when disable_hf_tokenizer_download is set."""

    @classmethod
    def from_pretrained(cls, *_args: Any, **_kwargs: Any) -> Tokenizer:
        return cls()

    @classmethod
    def from_str(cls, _json: str) -> Tokenizer:
        return cls()

    def encode(self, text: str) -> Encoding:
        n = max(0, len(text) // 4)
        return Encoding([0] * n)

    def decode(self, _tokens: list[int]) -> str:
        return ""


__all__ = ["Tokenizer"]
