"""Tool declarations and action metadata.

A tool is a single function-calling surface (for example ``manage_member``) and
contains multiple named actions (``ban``, ``timeout``, ``warn``, ...).  Each
action carries its own risk and permission requirements so the AI can never
bypass moderation checks by choosing a different action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable

from ..util import parse_bool
from .types import ToolSchema


class Risk(IntEnum):
    SAFE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass(slots=True)
class ActionMeta:
    risk: Risk = Risk.SAFE
    permission: str | tuple[str, ...] | None = None
    confirm: bool | None = None
    approval: bool | None = None
    description: str = ""
    owner_only: bool = False
    cooldown: float = 0.0

    def __post_init__(self):
        if self.confirm is None:
            self.confirm = self.risk >= Risk.HIGH
        if self.approval is None:
            self.approval = self.permission is not None and self.risk >= Risk.MEDIUM


@dataclass(slots=True)
class ToolResult:
    ok: bool = True
    summary: str = ""
    visible: bool = False
    data: dict[str, Any] | None = None

    @classmethod
    def success(cls, summary: str, *, visible: bool = False, data: dict | None = None) -> "ToolResult":
        return cls(ok=True, summary=summary, visible=visible, data=data)

    @classmethod
    def error(cls, summary: str, *, visible: bool = True) -> "ToolResult":
        return cls(ok=False, summary=summary, visible=visible)

    @classmethod
    def done(cls, summary: str = "Done.") -> "ToolResult":
        return cls(ok=True, summary=summary, visible=True)


Handler = Callable[[Any, dict[str, Any]], Awaitable[ToolResult | str | None]]


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    category: str
    handler: Handler
    actions: dict[str, ActionMeta] | None = None
    default_meta: ActionMeta | None = None
    requires_guild: bool = True

    @property
    def action_names(self) -> list[str]:
        return list((self.actions or {}).keys())

    @property
    def default_action(self) -> str | None:
        if self.actions and len(self.actions) == 1:
            return next(iter(self.actions))
        return None

    def meta_for(self, action: str | None) -> ActionMeta:
        if self.actions:
            meta = self.actions.get(action or "")
            if meta is not None:
                return meta
            return ActionMeta(risk=Risk.SAFE)
        return self.default_meta or ActionMeta(risk=Risk.SAFE)

    def to_schema(self) -> ToolSchema:
        description = self.description
        if self.actions:
            action_list = ", ".join(self.actions)
            description = f"{description} Actions: {action_list}."
        return ToolSchema(name=self.name, description=description, parameters=self.parameters)

    def category_help(self) -> str:
        if not self.actions:
            return f"{self.name}: {self.description}"
        actions = ", ".join(
            f"{name} ({meta.risk.name.lower()})" for name, meta in self.actions.items()
        )
        return f"{self.name}: {actions}"


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Duplicate tool name: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def schemas(self) -> list[ToolSchema]:
        return [spec.to_schema() for spec in self._tools.values()]

    def categories(self) -> dict[str, list[ToolSpec]]:
        result: dict[str, list[ToolSpec]] = {}
        for spec in self._tools.values():
            result.setdefault(spec.category, []).append(spec)
        return result

    def catalog(self) -> str:
        lines: list[str] = []
        for category, specs in sorted(self.categories().items()):
            lines.append(f"\n[{category}]")
            for spec in specs:
                lines.append(f"- {spec.category_help()}")
        return "\n".join(lines).strip()

    def names(self) -> list[str]:
        return list(self._tools.keys())


REGISTRY = ToolRegistry()


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    *,
    category: str,
    actions: dict[str, ActionMeta] | None = None,
    default_meta: ActionMeta | None = None,
    requires_guild: bool = True,
):
    """Register an action handler as an AI-callable tool."""

    def decorator(fn: Handler) -> Handler:
        REGISTRY.register(
            ToolSpec(
                name=name,
                description=description,
                parameters=parameters,
                category=category,
                handler=fn,
                actions=actions,
                default_meta=default_meta,
                requires_guild=requires_guild,
            )
        )
        return fn

    return decorator


def action(
    risk: Risk,
    *,
    permission: str | tuple[str, ...] | None = None,
    confirm: bool | None = None,
    approval: bool | None = None,
    description: str = "",
    owner_only: bool = False,
    cooldown: float = 0.0,
) -> ActionMeta:
    return ActionMeta(
        risk=risk,
        permission=permission,
        confirm=confirm,
        approval=approval,
        description=description,
        owner_only=owner_only,
        cooldown=cooldown,
    )


# ---------------------------------------------------------------------------
# Argument coercion
# ---------------------------------------------------------------------------


def coerce_args(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Coerce model-supplied strings into the types the schema asks for."""
    if not isinstance(args, dict):
        return {}
    properties = schema.get("properties") or {}
    result: dict[str, Any] = dict(args)
    for key, value in list(result.items()):
        spec = properties.get(key)
        if not isinstance(spec, dict):
            continue
        result[key] = _coerce_value(spec, value)
    return result


def _coerce_value(spec: dict[str, Any], value: Any) -> Any:
    if value is None:
        return None
    kind = str(spec.get("type", "")).lower()
    try:
        if kind == "integer":
            if isinstance(value, str):
                return int(float(value.strip()))
            return int(value)
        if kind == "number":
            return float(value)
        if kind == "boolean":
            return parse_bool(value)
        if kind == "array":
            if isinstance(value, str):
                return [part.strip() for part in value.split(",") if part.strip()]
            if isinstance(value, (list, tuple, set)):
                item_spec = spec.get("items") if isinstance(spec.get("items"), dict) else {}
                return [_coerce_value(item_spec, item) for item in value]
            return [value]
        if kind == "object":
            if isinstance(value, str):
                import json

                try:
                    parsed = json.loads(value)
                    return parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    return {}
            return value if isinstance(value, dict) else {}
        if kind == "string":
            return str(value)
    except (TypeError, ValueError):
        return value
    return value


__all__ = [
    "Risk",
    "ActionMeta",
    "ToolResult",
    "ToolSpec",
    "ToolRegistry",
    "REGISTRY",
    "tool",
    "action",
    "coerce_args",
]
