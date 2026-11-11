"""Provider factory."""

from __future__ import annotations

import aiohttp

from ...config import ProviderConfig
from .anthropic import AnthropicProvider
from .base import BaseProvider
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider


def create_provider(config: ProviderConfig, session: aiohttp.ClientSession) -> BaseProvider:
    if config.kind == "gemini":
        return GeminiProvider(config, session)
    if config.kind == "anthropic":
        return AnthropicProvider(config, session)
    return OpenAICompatProvider(config, session)
