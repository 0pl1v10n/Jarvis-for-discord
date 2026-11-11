"""Base class and HTTP helpers for AI providers."""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any

import aiohttp

from ...config import ProviderConfig
from ..types import AIResponse, ChatMessage, ProviderError, ToolSchema

# A hung model must not hold the whole conversation hostage.  The router can
# only fail over once this timeout expires, so keep it comfortably short.
DEFAULT_REQUEST_TIMEOUT = float(os.getenv("AI_REQUEST_TIMEOUT", "60") or 60)


class BaseProvider(ABC):
    kind = "base"

    def __init__(self, config: ProviderConfig, session: aiohttp.ClientSession):
        self.config = config
        self.name = config.name
        self.session = session

    @abstractmethod
    async def chat(
        self,
        model: str,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSchema],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AIResponse:
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        return []

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        effective_timeout = DEFAULT_REQUEST_TIMEOUT if timeout is None else timeout
        try:
            async with self.session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=effective_timeout),
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    self._raise_http(response.status, text)
                if not text.strip():
                    return {}
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    raise ProviderError(
                        f"Provider returned invalid JSON: {text[:200]}",
                        status=response.status,
                        retryable=True,
                    )
        except asyncio.TimeoutError:
            raise ProviderError("Request timed out", retryable=True)
        except aiohttp.ClientError as exc:
            raise ProviderError(f"Network error: {exc}", retryable=True)
        except ProviderError:
            raise

    def _raise_http(self, status: int, text: str) -> None:
        code = ""
        message = text[:500]
        try:
            data = json.loads(text)
            error = data.get("error", data) if isinstance(data, dict) else {}
            if isinstance(error, dict):
                code = str(error.get("code") or error.get("type") or error.get("status") or "")
                message = str(error.get("message") or error.get("detail") or message)
            elif isinstance(error, str):
                message = error
        except json.JSONDecodeError:
            pass

        lowered = f"{code} {message}".lower()
        provider_fatal = status in {401, 403} or any(
            word in lowered
            for word in (
                "api key not valid",
                "invalid api key",
                "incorrect api key",
                "unauthorized",
                "authentication",
                "permission denied",
            )
        )
        quota = status == 429 or any(
            word in lowered for word in ("resource_exhausted", "quota", "rate limit", "rate_limit")
        )
        model_fatal = status == 404 or any(
            word in lowered
            for word in (
                "model not found",
                "not found",
                "does not exist",
                "unsupported model",
                "unknown model",
                "no such model",
            )
        )
        retryable = (
            status in {400, 404, 408, 409, 413, 422, 425, 429, 500, 502, 503, 504, 529}
            or status >= 500
            or quota
            or model_fatal
        )
        if provider_fatal:
            retryable = False
        raise ProviderError(
            message,
            status=status,
            code=code,
            retryable=retryable,
            provider_fatal=provider_fatal,
            model_fatal=model_fatal,
            quota=quota,
        )

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        headers.update(self.config.headers)
        return headers
