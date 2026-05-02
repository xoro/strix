import logging
import platform
import warnings

import litellm

from .config import LLMConfig
from .copilot import configure_copilot_litellm
from .llm import LLM, LLMRequestFailedError


__all__ = [
    "LLM",
    "LLMConfig",
    "LLMRequestFailedError",
]

# Avoid Hugging Face `tokenizers` paths when using the FreeBSD import shim (no Rust wheels).
if platform.system() == "FreeBSD":
    litellm.disable_hf_tokenizer_download = True

litellm._logging._disable_debugging()
litellm.suppress_debug_info = True
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").propagate = False
warnings.filterwarnings("ignore", category=RuntimeWarning, module="asyncio")
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

configure_copilot_litellm()
