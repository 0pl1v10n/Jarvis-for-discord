"""OpenAI-compatible chat-completions provider.

This one adapter covers OpenAI, OpenRouter, Groq, Mistral, DeepSeek, Together,
xAI, Ollama, LM Studio and any custom endpoint that speaks the same protocol.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from ..types import AIResponse, ChatMessage, ProviderError, ToolCall, ToolSchema, Usage
from .base import BaseProvider

_SKIP = ("embedding", "embed", "whisper", "tts", "dall-e", "moderation", "rerank")


class OpenAICompatProvider(BaseProvider):
    kind = "openai"

    def __init__(self, config, session):
        super().__init__(config, session)
        self.base_url = (config.base_url or "").rstrip("/")

    def _content_for_user(self, message: ChatMessage) -> Any:
        if not message.images:
            return message.content
        content: list[dict[str, Any]] = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        for image in message.images:
            mime = image.mime or "image/png"
            data = base64.b64encode(image.data).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data}"},
                }
            )
        return content or message.content

    def _messages(self, system: str, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if system:
            result.append({"role": "system", "content": system})
        for message in messages:
            if message.role == "system":
                continue
            if message.role == "user":
                result.append({"role": "user", "content": self._content_for_user(message)})
            elif message.role == "assistant":
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content or None,
                }
                if message.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments or {}),
                            },
                        }
                        for call in message.tool_calls
                    ]
                result.append(entry)
            elif message.role == "tool":
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id or message.tool_name or "tool",
                        "content": message.content or "",
                    }
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
        url = f"{self.base_url}/chat/completions"
        body: dict[str, Any] = {
            "model": model,
            "messages": self._messages(system, messages),
        }
        if tools:
            body["tools"] = [tool.openai() for tool in tools]
            body["tool_choice"] = "auto"

        # Reasoning models on the real OpenAI endpoint use different params.
        low = model.lower()
        reasoning = self.name == "openai" and (
            low.startswith(("o1", "o3", "o4", "gpt-5"))
            or "reasoning" in low
        )
        if reasoning:
            body["max_completion_tokens"] = max_tokens
        else:
            body["max_tokens"] = max_tokens
            body["temperature"] = temperature

        data = await self._request_json(
            "POST", url, headers=self._auth_headers(), json_body=body
        )

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("Provider returned no choices", retryable=True)
        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            text = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        else:
            text = str(content or "")

        calls: list[ToolCall] = []
        for index, raw_call in enumerate(message.get("tool_calls") or []):
            function = raw_call.get("function") or {}
            name = str(function.get("name") or "")
            if not name:
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except json.JSONDecodeError:
                    arguments = {"_raw": arguments}
            if not isinstance(arguments, dict):
                arguments = {"value": arguments}
            calls.append(
                ToolCall(
                    id=str(raw_call.get("id") or f"call_{index}"),
                    name=name,
                    arguments=arguments,
                )
            )

        usage_data = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(usage_data.get("prompt_tokens") or 0),
            completion_tokens=int(usage_data.get("completion_tokens") or 0),
            total_tokens=int(usage_data.get("total_tokens") or 0),
        )

        finish = str(choice.get("finish_reason") or "")
        if not text and not calls and finish in {"content_filter", "safety"}:
            raise ProviderError(f"Provider blocked the response ({finish})", retryable=False)

        return AIResponse(
            text=text.strip(),
            tool_calls=calls,
            model=model,
            provider=self.name,
            usage=usage,
            finish_reason=finish,
            raw=data,
        )

    async def list_models(self) -> list[str]:
        data = await self._request_json(
            "GET", f"{self.base_url}/models", headers=self._auth_headers(), timeout=30
        )
        names: list[str] = []
        for item in data.get("data") or data.get("models") or []:
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = str(item.get("id") or item.get("name") or "")
                name = name.removeprefix("models/")
            else:
                continue
            if not name:
                continue
            low = name.lower()
            if any(skip in low for skip in _SKIP):
                continue
            names.append(name)
        return sorted(set(names))
