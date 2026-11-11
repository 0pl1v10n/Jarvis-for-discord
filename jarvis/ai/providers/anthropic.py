"""Anthropic Claude provider (Messages API)."""

from __future__ import annotations

import base64
from typing import Any

from ..types import AIResponse, ChatMessage, ProviderError, ToolCall, ToolSchema, Usage
from .base import BaseProvider


class AnthropicProvider(BaseProvider):
    kind = "anthropic"

    def __init__(self, config, session):
        super().__init__(config, session)
        self.base_url = (config.base_url or "https://api.anthropic.com/v1").rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }
        headers.update(self.config.headers)
        return headers

    def _user_blocks(self, message: ChatMessage) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for image in message.images:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.mime or "image/png",
                        "data": base64.b64encode(image.data).decode(),
                    },
                }
            )
        if message.content:
            blocks.append({"type": "text", "text": message.content})
        if not blocks:
            blocks.append({"type": "text", "text": " "})
        return blocks

    def _messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        def append(role: str, blocks: list[dict[str, Any]]) -> None:
            if result and result[-1]["role"] == role and role == "user":
                result[-1]["content"].extend(blocks)
            else:
                result.append({"role": role, "content": blocks})

        for message in messages:
            if message.role == "system":
                continue
            if message.role == "user":
                append("user", self._user_blocks(message))
            elif message.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments or {},
                        }
                    )
                if blocks:
                    append("assistant", blocks)
            elif message.role == "tool":
                append(
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id or message.tool_name or "tool",
                            "content": message.content or "OK",
                        }
                    ],
                )
        return result

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
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": self._messages(messages),
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [tool.anthropic() for tool in tools]
            body["tool_choice"] = {"type": "auto"}

        data = await self._request_json(
            "POST",
            f"{self.base_url}/messages",
            headers=self._headers(),
            json_body=body,
        )

        blocks = data.get("content") or []
        texts: list[str] = []
        calls: list[ToolCall] = []
        for index, block in enumerate(blocks):
            kind = block.get("type")
            if kind == "text" and block.get("text"):
                texts.append(str(block["text"]))
            elif kind == "tool_use":
                name = str(block.get("name") or "")
                if name:
                    calls.append(
                        ToolCall(
                            id=str(block.get("id") or f"toolu_{index}"),
                            name=name,
                            arguments=block.get("input") if isinstance(block.get("input"), dict) else {},
                        )
                    )

        usage_data = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(usage_data.get("input_tokens") or 0),
            completion_tokens=int(usage_data.get("output_tokens") or 0),
            total_tokens=int(usage_data.get("input_tokens") or 0)
            + int(usage_data.get("output_tokens") or 0),
        )
        finish = str(data.get("stop_reason") or "")
        text = "\n".join(texts).strip()

        if not text and not calls and finish in {"refusal", "content_filter"}:
            raise ProviderError(f"Claude blocked the response ({finish})", retryable=False)

        return AIResponse(
            text=text,
            tool_calls=calls,
            model=model,
            provider=self.name,
            usage=usage,
            finish_reason=finish,
            raw=data,
        )

    async def list_models(self) -> list[str]:
        data = await self._request_json(
            "GET", f"{self.base_url}/models", headers=self._headers(), timeout=30
        )
        names: list[str] = []
        for item in data.get("data") or []:
            name = str(item.get("id") or "")
            if name:
                names.append(name)
        return sorted(set(names))
