import logging
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

litellm._logging._disable_debugging()
litellm.suppress_debug_info = True
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").propagate = False
warnings.filterwarnings("ignore", category=RuntimeWarning, module="asyncio")

configure_copilot_litellm()
