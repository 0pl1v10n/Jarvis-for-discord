"""Provider-independent AI data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ImageInput:
    data: bytes
    mime: str = "image/png"
    name: str = "image"


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    # Gemini returns an opaque signature with each function call; it must be
    # echoed back on the next turn or the API rejects the request.
    signature: str | None = None


@dataclass(slots=True)
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    images: list[ImageInput] = field(default_factory=list)


@dataclass(slots=True)
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]

    def openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def anthropic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def gemini(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": gemini_schema(self.parameters),
        }


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(slots=True)
class AIResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = ""
    raw: dict[str, Any] | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass(slots=True)
class ModelTarget:
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


class ProviderError(Exception):
    """An error returned by an AI provider."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str = "",
        retryable: bool = True,
        provider_fatal: bool = False,
        model_fatal: bool = False,
        quota: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.retryable = retryable
        self.provider_fatal = provider_fatal
        self.model_fatal = model_fatal
        self.quota = quota

    def short(self, limit: int = 180) -> str:
        bits = []
        if self.status:
            bits.append(str(self.status))
        if self.code:
            bits.append(self.code)
        head = " ".join(bits)
        text = f"{head}: {self.message}" if head else self.message
        return text.replace("\n", " ")[:limit]


class AllModelsFailed(Exception):
    def __init__(self, attempts: list[tuple[str, str, str]]):
        self.attempts = attempts
        detail = "; ".join(f"{label} -> {reason}" for label, reason, _ in attempts[:6])
        super().__init__(f"All AI models failed: {detail}")


# ---------------------------------------------------------------------------
# JSON-schema conversion
# ---------------------------------------------------------------------------

_JSON_TO_GEMINI = {
    "object": "OBJECT",
    "array": "ARRAY",
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "null": "NULL",
}


def gemini_schema(schema: Any) -> Any:
    """Convert a JSON schema to the shape Gemini's function declarations want."""
    if not isinstance(schema, dict):
        return schema
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {"$schema", "additionalProperties", "default", "title"}:
            continue
        if key == "type" and isinstance(value, str):
            result["type"] = _JSON_TO_GEMINI.get(value.lower(), value.upper())
        elif key == "properties" and isinstance(value, dict):
            result["properties"] = {k: gemini_schema(v) for k, v in value.items()}
        elif key == "items":
            result["items"] = gemini_schema(value)
        elif key in {"anyOf", "oneOf", "allOf"} and isinstance(value, list):
            result[key] = [gemini_schema(v) for v in value]
        else:
            result[key] = value
    return result
