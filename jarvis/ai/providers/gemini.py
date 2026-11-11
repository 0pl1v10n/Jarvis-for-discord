"""Google Gemini provider using the REST API (no SDK required)."""

from __future__ import annotations

import base64
import re
from typing import Any

from ...config import ProviderConfig
from ..types import AIResponse, ChatMessage, ImageInput, ProviderError, ToolCall, ToolSchema, Usage
from .base import BaseProvider

_SKIP = (
    "embedding",
    "image",
    "imagen",
    "tts",
    "live",
    "audio",
    "veo",
    "robotics",
    "computer-use",
    "aqa",
    "transcribe",
    "nano-banana",
)


class GeminiProvider(BaseProvider):
    kind = "gemini"

    def __init__(self, config: ProviderConfig, session):
        super().__init__(config, session)
        self.base_url = (config.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    # -- request building --------------------------------------------------
    def _parts_for_user(self, message: ChatMessage) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        if message.content:
            parts.append({"text": message.content})
        for image in message.images:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": image.mime or "image/png",
                        "data": base64.b64encode(image.data).decode(),
                    }
                }
            )
        return parts or [{"text": " "}]

    def _contents(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        pending_tool_parts: list[dict[str, Any]] = []

        def flush_tools() -> None:
            nonlocal pending_tool_parts
            if pending_tool_parts:
                contents.append({"role": "user", "parts": pending_tool_parts})
                pending_tool_parts = []

        for message in messages:
            if message.role == "system":
                continue
            if message.role == "tool":
                flush = False
                pending_tool_parts.append(
                    {
                        "functionResponse": {
                            "name": message.tool_name or "tool",
                            "response": {"result": message.content or "OK"},
                        }
                    }
                )
                # Tool responses are grouped into a single user turn.
                continue

            flush_tools()
            if message.role == "user":
                contents.append({"role": "user", "parts": self._parts_for_user(message)})
            elif message.role == "assistant":
                parts: list[dict[str, Any]] = []
                if message.content:
                    parts.append({"text": message.content})
                for call in message.tool_calls:
                    function_call: dict[str, Any] = {
                        "name": call.name,
                        "args": call.arguments or {},
                    }
                    if call.id and not call.id.startswith("gemini_"):
                        function_call["id"] = call.id
                    part: dict[str, Any] = {"functionCall": function_call}
                    if call.signature:
                        part["thoughtSignature"] = call.signature
                    parts.append(part)
                if parts:
                    contents.append({"role": "model", "parts": parts})
        flush_tools()
        return contents

    # -- provider API ------------------------------------------------------
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
        model = str(model).removeprefix("models/").strip()
        url = f"{self.base_url}/models/{model}:generateContent"
        body: dict[str, Any] = {
            "contents": self._contents(messages),
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        # Gemini "thinking" models can spend the entire output budget on hidden
        # reasoning and return an empty candidate.  Flash/lite/omni models accept
        # thinkingBudget=0, which keeps replies fast and cheap.  Pro and Gemma
        # models reject the field, so only send it where it is known to work.
        if _supports_thinking_budget(model):
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        if tools:
            body["tools"] = [
                {"functionDeclarations": [tool.gemini() for tool in tools]}
            ]
            body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

        data = await self._request_json(
            "POST",
            url,
            headers={"Content-Type": "application/json"},
            params={"key": self.config.api_key},
            json_body=body,
        )

        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback") or {}
            reason = feedback.get("blockReason") or "no candidates"
            raise ProviderError(
                f"Gemini returned no candidates ({reason})",
                retryable=False,
            )

        candidate = candidates[0]
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        texts: list[str] = []
        calls: list[ToolCall] = []
        for index, part in enumerate(parts):
            if part.get("thought"):
                continue
            if "text" in part and part["text"]:
                texts.append(str(part["text"]))
            call = part.get("functionCall")
            if call:
                name = str(call.get("name") or "")
                args = call.get("args") or {}
                if name:
                    calls.append(
                        ToolCall(
                            id=str(call.get("id") or f"gemini_{index}_{name}"),
                            name=name,
                            arguments=dict(args) if isinstance(args, dict) else {},
                            signature=part.get("thoughtSignature"),
                        )
                    )

        usage_data = data.get("usageMetadata") or {}
        usage = Usage(
            prompt_tokens=int(usage_data.get("promptTokenCount") or 0),
            completion_tokens=int(usage_data.get("candidatesTokenCount") or 0),
            total_tokens=int(usage_data.get("totalTokenCount") or 0),
        )
        finish = str(candidate.get("finishReason") or "")
        text = "".join(texts).strip()

        if not text and not calls and finish in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}:
            raise ProviderError(f"Gemini blocked the response ({finish})", retryable=False)
        if not text and not calls and finish == "MAX_TOKENS":
            # The model burned its whole budget on hidden thinking.  Treat it
            # as a transient model failure so the router moves to the next one.
            raise ProviderError(
                "Gemini returned an empty response (MAX_TOKENS)", retryable=True
            )

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
        names: list[str] = []
        page_token: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {"key": self.config.api_key, "pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            data = await self._request_json(
                "GET", f"{self.base_url}/models", params=params, timeout=30
            )
            for item in data.get("models") or []:
                name = str(item.get("name") or "").removeprefix("models/").strip()
                low = name.lower()
                methods = item.get("supportedGenerationMethods") or []
                if not name:
                    continue
                if methods and "generateContent" not in methods:
                    continue
                if any(skip in low for skip in _SKIP):
                    continue
                if not any(word in low for word in ("gemini", "gemma", "learnlm")):
                    continue
                names.append(name)
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return sorted(set(names), key=_rank)


def _supports_thinking_budget(model: str) -> bool:
    """Whether a Gemini model accepts ``thinkingConfig.thinkingBudget``.

    Flash models accept ``thinkingBudget=0``; lite variants reject the field
    (they simply do not think), and pro/gemma models do not support it either.
    """
    low = model.lower()
    if "gemma" in low or "lite" in low:
        return False
    if "pro" in low and "flash" not in low:
        return False
    return "flash" in low


def _rank(name: str):
    low = name.lower()
    version = re.search(r"(\d+(?:\.\d+)?)", low)
    return (
        "lite" in low,  # regular flash before flash-lite
        "flash" not in low,  # flash before pro/other
        "preview" in low or "exp" in low,  # stable before preview
        -(float(version.group(1)) if version else 0.0),  # newer first
        low,
    )
