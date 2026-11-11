"""Configuration for Jarvis v2.

Everything is environment-driven.  The loader is deliberately forgiving: it
looks for ``.env`` files in the project root, in ``v2`` and (for convenience
when upgrading) in the old ``v1/ai/gemini`` location.  Real environment
variables always win because ``load_dotenv`` is called with ``override=False``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths / dotenv
# ---------------------------------------------------------------------------

PACKAGE_DIR = Path(__file__).resolve().parent
BASE_DIR = PACKAGE_DIR.parent  # .../v2
ROOT_DIR = BASE_DIR.parent  # .../Jarvis

for candidate in (
    BASE_DIR / ".env",
    ROOT_DIR / ".env",
    ROOT_DIR / "v1" / "ai" / "gemini" / ".env",
):
    if candidate.exists():
        load_dotenv(candidate, override=False)


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def _int(value: str | int | None, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _bool(value: str | bool | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]


def _read_secret(filename: str) -> str:
    for folder in (
        BASE_DIR,
        ROOT_DIR,
        ROOT_DIR / "v1",
        ROOT_DIR / "v1" / "ai" / "gemini",
    ):
        path = folder / filename
        if path.exists():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                pass
    return ""


def _json_env(name: str, default):
    raw = _env(name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

PROVIDER_KINDS = {"gemini", "openai", "anthropic"}


@dataclass(slots=True)
class ProviderConfig:
    name: str
    kind: str
    api_key: str = ""
    base_url: str = ""
    models: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = False
    discover: bool = False
    priority: int = 100
    label: str = ""

    @property
    def display(self) -> str:
        return self.label or self.name.title()

    @property
    def configured(self) -> bool:
        if not self.enabled:
            return False
        if self.kind in {"openai", "anthropic", "gemini"} and not self.api_key:
            # OpenAI-compatible local servers (Ollama/LM Studio) do not need a key.
            return self.name in {"ollama", "lmstudio", "custom"} or bool(self.base_url)
        return bool(self.models)


# Defaults are only starting points.  Gemini models are also auto-discovered at
# runtime, and every other list can be overridden with ``<PROVIDER>_MODELS``.
DEFAULT_MODELS: dict[str, list[str]] = {
    "gemini": [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ],
    "openrouter": [
        "google/gemini-2.0-flash-exp:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "deepseek/deepseek-chat-v3-0324:free",
        "qwen/qwen-2.5-72b-instruct:free",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "gemma2-9b-it",
    ],
    "openai": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4o"],
    "anthropic": ["claude-sonnet-4-20250514", "claude-3-5-haiku-latest"],
    "mistral": ["mistral-large-latest", "mistral-small-latest"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "together": ["meta-llama/Llama-3.3-70B-Instruct-Turbo"],
    "xai": ["grok-2-latest"],
    "ollama": ["llama3.2", "qwen2.5", "gemma3"],
    "lmstudio": ["local-model"],
}


def _build_providers() -> list[ProviderConfig]:
    providers: list[ProviderConfig] = []

    def add(
        name: str,
        kind: str,
        *,
        base_url: str = "",
        key_env: str = "",
        key: str = "",
        models_env: str = "",
        single_model_env: str = "",
        headers: dict[str, str] | None = None,
        discover: bool = False,
        default_enabled: bool = False,
    ) -> None:
        api_key = key or _env(key_env)
        configured_models = _csv(_env(models_env)) if models_env else []
        if not configured_models and single_model_env:
            configured_models = _csv(_env(single_model_env))
        models = configured_models or list(DEFAULT_MODELS.get(name, []))
        enabled = bool(api_key) or default_enabled
        providers.append(
            ProviderConfig(
                name=name,
                kind=kind,
                api_key=api_key,
                base_url=base_url,
                models=models,
                headers=headers or {},
                enabled=enabled,
                discover=discover,
                priority=len(providers),
                label=name.replace("_", " ").title(),
            )
        )

    add(
        "gemini",
        "gemini",
        base_url=_env("GEMINI_BASE_URL", default="https://generativelanguage.googleapis.com/v1beta"),
        key_env="GEMINI_API_KEY",
        models_env="GEMINI_MODELS",
        single_model_env="GEMINI_MODEL",
        discover=_bool(_env("GEMINI_AUTO_DISCOVER", default="true"), True),
    )
    add(
        "openrouter",
        "openai",
        base_url=_env("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1"),
        key_env="OPENROUTER_API_KEY",
        models_env="OPENROUTER_MODELS",
        headers={
            "HTTP-Referer": _env("OPENROUTER_SITE_URL", default="https://github.com/opencode"),
            "X-Title": _env("OPENROUTER_APP_NAME", default="Jarvis Discord Bot"),
        },
        discover=_bool(_env("OPENROUTER_AUTO_DISCOVER", default="false")),
    )
    add(
        "groq",
        "openai",
        base_url=_env("GROQ_BASE_URL", default="https://api.groq.com/openai/v1"),
        key_env="GROQ_API_KEY",
        models_env="GROQ_MODELS",
    )
    add(
        "openai",
        "openai",
        base_url=_env("OPENAI_BASE_URL", default="https://api.openai.com/v1"),
        key_env="OPENAI_API_KEY",
        models_env="OPENAI_MODELS",
    )
    add(
        "anthropic",
        "anthropic",
        base_url=_env("ANTHROPIC_BASE_URL", default="https://api.anthropic.com/v1"),
        key_env="ANTHROPIC_API_KEY",
        models_env="ANTHROPIC_MODELS",
    )
    add(
        "mistral",
        "openai",
        base_url=_env("MISTRAL_BASE_URL", default="https://api.mistral.ai/v1"),
        key_env="MISTRAL_API_KEY",
        models_env="MISTRAL_MODELS",
    )
    add(
        "deepseek",
        "openai",
        base_url=_env("DEEPSEEK_BASE_URL", default="https://api.deepseek.com/v1"),
        key_env="DEEPSEEK_API_KEY",
        models_env="DEEPSEEK_MODELS",
    )
    add(
        "together",
        "openai",
        base_url=_env("TOGETHER_BASE_URL", default="https://api.together.xyz/v1"),
        key_env="TOGETHER_API_KEY",
        models_env="TOGETHER_MODELS",
    )
    add(
        "xai",
        "openai",
        base_url=_env("XAI_BASE_URL", default="https://api.x.ai/v1"),
        key_env="XAI_API_KEY",
        models_env="XAI_MODELS",
    )
    add(
        "ollama",
        "openai",
        base_url=_env("OLLAMA_BASE_URL", default="http://localhost:11434/v1"),
        key_env="OLLAMA_API_KEY",
        models_env="OLLAMA_MODELS",
        default_enabled=_bool(_env("OLLAMA_ENABLED")),
        discover=_bool(_env("OLLAMA_AUTO_DISCOVER", default="true")),
    )
    add(
        "lmstudio",
        "openai",
        base_url=_env("LMSTUDIO_BASE_URL", default="http://localhost:1234/v1"),
        key_env="LMSTUDIO_API_KEY",
        models_env="LMSTUDIO_MODELS",
        default_enabled=_bool(_env("LMSTUDIO_ENABLED")),
        discover=_bool(_env("LMSTUDIO_AUTO_DISCOVER", default="true")),
    )

    # Fully custom OpenAI-compatible endpoint.
    custom_base = _env("CUSTOM_BASE_URL")
    if custom_base:
        providers.append(
            ProviderConfig(
                name=_env("CUSTOM_NAME", default="custom"),
                kind="openai",
                api_key=_env("CUSTOM_API_KEY"),
                base_url=custom_base,
                models=_csv(_env("CUSTOM_MODELS")) or ["default"],
                headers=_json_env("CUSTOM_HEADERS", {}) or {},
                enabled=True,
                priority=len(providers),
                label=_env("CUSTOM_LABEL", default="Custom"),
            )
        )

    # Additional providers from a JSON list:
    # PROVIDERS_JSON=[{"name":"nvidia","kind":"openai","base_url":"...","api_key":"...","models":["..."]}]
    for raw in _json_env("PROVIDERS_JSON", []) or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "custom"))
        kind = str(raw.get("kind", "openai")).lower()
        if kind not in PROVIDER_KINDS:
            continue
        providers.append(
            ProviderConfig(
                name=name,
                kind=kind,
                api_key=str(raw.get("api_key", "")),
                base_url=str(raw.get("base_url", "")),
                models=[str(m) for m in raw.get("models", [])],
                headers={str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
                enabled=bool(raw.get("enabled", True)),
                discover=bool(raw.get("discover", False)),
                priority=len(providers),
                label=str(raw.get("label", name.title())),
            )
        )

    order = _csv(_env("AI_PROVIDER_ORDER"))
    if order:
        rank = {name.lower(): i for i, name in enumerate(order)}
        providers.sort(key=lambda p: rank.get(p.name.lower(), 1000 + p.priority))
    return providers


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Settings:
    base_dir: Path
    token: str
    guild_id: int
    owner_ids: set[int]
    log_channel_id: int
    approval_channel_id: int
    mod_role_id: int
    admin_role_id: int
    game_category_id: int
    database_path: Path
    data_category_name: str
    data_flush_seconds: float
    log_level: str

    # AI behaviour
    providers: list[ProviderConfig]
    model_chain: list[tuple[str, str]]
    ai_temperature: float
    ai_max_output_tokens: int
    ai_max_tool_rounds: int
    ai_history_messages: int
    ai_history_chars: int
    cooldown_seconds: float
    session_seconds: int
    announce_model_switches: bool
    shutdown_on_total_failure: bool
    auto_discover: bool

    # Discord behaviour
    enable_presences: bool
    game_private_channels: bool
    game_idle_timeout: int
    web_search_enabled: bool
    music_enabled: bool
    auto_action_without_wake: bool
    max_images: int
    max_image_bytes: int

    def provider(self, name: str) -> ProviderConfig | None:
        wanted = name.lower()
        return next((p for p in self.providers if p.name.lower() == wanted), None)

    @property
    def enabled_providers(self) -> list[ProviderConfig]:
        return [p for p in self.providers if p.configured]


def _parse_model_chain(raw: str) -> list[tuple[str, str]]:
    chain: list[tuple[str, str]] = []
    for entry in _csv(raw):
        if ":" in entry:
            provider, model = entry.split(":", 1)
            chain.append((provider.strip().lower(), model.strip()))
        else:
            chain.append(("gemini", entry.strip()))
    return [(p, m) for p, m in chain if m]


def load_settings() -> Settings:
    token = _env("DISCORD_TOKEN") or _read_secret("token.txt")
    guild_id = _int(_env("GUILD_ID") or _read_secret("serverID.txt"))
    owner_ids = {_int(v) for v in _csv(_env("OWNER_IDS")) if _int(v)}

    providers = _build_providers()
    chain = _parse_model_chain(_env("AI_MODEL_CHAIN"))

    database_path = Path(
        _env("DATABASE_PATH", default=str(BASE_DIR / "jarvis.db"))
    ).expanduser()

    return Settings(
        base_dir=BASE_DIR,
        token=token,
        guild_id=guild_id,
        owner_ids=owner_ids,
        log_channel_id=_int(_env("LOG_CHANNEL_ID")),
        approval_channel_id=_int(_env("APPROVAL_CHANNEL_ID")),
        mod_role_id=_int(_env("MOD_ROLE_ID")),
        admin_role_id=_int(_env("ADMIN_ROLE_ID")),
        game_category_id=_int(_env("GAME_CATEGORY_ID") or _env("RPS_CATEGORY_ID")),
        database_path=database_path,
        data_category_name=_env("JARVIS_DATA_CATEGORY", default="Jarvis Data").strip()
        or "Jarvis Data",
        data_flush_seconds=float(_env("JARVIS_DATA_FLUSH_SECONDS", default="2") or 2),
        log_level=_env("LOG_LEVEL", default="INFO").upper(),
        providers=providers,
        model_chain=chain,
        ai_temperature=float(_env("AI_TEMPERATURE", default="0.7") or 0.7),
        ai_max_output_tokens=_int(_env("AI_MAX_OUTPUT_TOKENS"), 2048),
        ai_max_tool_rounds=_int(_env("AI_MAX_TOOL_ROUNDS"), 6),
        ai_history_messages=_int(_env("AI_HISTORY_MESSAGES"), 24),
        ai_history_chars=_int(_env("AI_HISTORY_CHARS"), 16000),
        cooldown_seconds=float(_env("AI_COOLDOWN_SECONDS", default="1.0") or 1.0),
        session_seconds=_int(_env("JARVIS_SESSION_SECONDS"), 240),
        announce_model_switches=_bool(_env("ANNOUNCE_MODEL_SWITCHES", default="true"), True),
        shutdown_on_total_failure=_bool(_env("SHUTDOWN_ON_TOTAL_FAILURE", default="false")),
        auto_discover=_bool(_env("GEMINI_AUTO_DISCOVER", default="true"), True),
        enable_presences=_bool(_env("ENABLE_PRESENCES")),
        game_private_channels=_bool(_env("GAME_PRIVATE_CHANNELS", default="true"), True),
        game_idle_timeout=_int(_env("GAME_IDLE_TIMEOUT"), 180),
        web_search_enabled=_bool(_env("WEB_SEARCH_ENABLED", default="true"), True),
        music_enabled=_bool(_env("MUSIC_ENABLED", default="true"), True),
        auto_action_without_wake=_bool(_env("AUTO_ACTION_WITHOUT_WAKE", default="true"), True),
        max_images=_int(_env("AI_MAX_IMAGES"), 4),
        max_image_bytes=_int(_env("AI_MAX_IMAGE_BYTES"), 8 * 1024 * 1024),
    )


def require_runtime_settings(settings: Settings) -> None:
    """Raise a helpful error when the minimum configuration is missing."""
    if not settings.token:
        raise RuntimeError(
            "No Discord token found. Put DISCORD_TOKEN=... in v2/.env, or create v2/token.txt."
        )
    if not settings.enabled_providers:
        raise RuntimeError(
            "No AI provider configured. Set GEMINI_API_KEY (recommended), or any of "
            "OPENROUTER_API_KEY / GROQ_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY / "
            "MISTRAL_API_KEY / DEEPSEEK_API_KEY / TOGETHER_API_KEY / XAI_API_KEY. "
            "You can also point OLLAMA_BASE_URL at a local server."
        )
