"""Model registry, discovery, health tracking, and automatic failover.

Jarvis never gets stuck on an exhausted model.  Every request walks the model
chain; a 429/quota error, a missing model, a 5xx, or a network failure moves to
the next *different* model (possibly a different provider entirely).  The
current healthy model is remembered for subsequent requests.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import aiohttp

from ..config import ProviderConfig, Settings
from .providers.base import BaseProvider
from .providers.factory import create_provider
from .types import (
    AIResponse,
    AllModelsFailed,
    ChatMessage,
    ModelTarget,
    ProviderError,
    ToolSchema,
)

log = logging.getLogger(__name__)

EventCallback = Callable[[dict], Awaitable[None]]


@dataclass
class TargetState:
    failures: int = 0
    cooldown_until: float = 0.0
    disabled: bool = False
    last_error: str = ""

    @property
    def cooling(self) -> bool:
        return time.monotonic() < self.cooldown_until


@dataclass
class RouterStatus:
    label: str
    provider: str
    model: str
    current: bool
    disabled: bool
    cooling: bool
    cooldown_seconds: int
    failures: int
    last_error: str


class ModelRouter:
    def __init__(
        self,
        settings: Settings,
        session: aiohttp.ClientSession,
        providers: dict[str, BaseProvider] | None = None,
    ):
        self.settings = settings
        self.session = session
        self.providers: dict[str, BaseProvider] = providers or {}
        self.targets: list[ModelTarget] = []
        self.states: dict[str, TargetState] = {}
        self.current_index = 0
        self._lock = asyncio.Lock()

        if not self.providers:
            for config in settings.providers:
                if config.configured:
                    self.providers[config.name] = create_provider(config, session)

    # -- setup -------------------------------------------------------------
    async def initialize(self, *, discover: bool = True) -> None:
        discovered: dict[str, list[str]] = {}
        if discover:
            await self._discover(discovered)
        self.targets = self._build_targets(discovered)
        for target in self.targets:
            self.states.setdefault(target.label, TargetState())
        log.info("AI model chain: %s", ", ".join(t.label for t in self.targets) or "(empty)")

    async def _discover(self, discovered: dict[str, list[str]]) -> None:
        async def one(provider: BaseProvider):
            try:
                names = await asyncio.wait_for(provider.list_models(), timeout=25)
            except Exception as exc:  # discovery is best-effort
                log.warning("Model discovery failed for %s: %s", provider.name, exc)
                return
            if names:
                discovered[provider.name] = names
                log.info("Discovered %d %s models", len(names), provider.name)

        tasks = []
        for provider in self.providers.values():
            config = provider.config
            if not config.discover:
                continue
            if config.kind == "gemini" or provider.name in {"ollama", "lmstudio"}:
                tasks.append(one(provider))
            elif provider.name in {"openrouter"} and config.discover:
                tasks.append(one(provider))
        if tasks:
            await asyncio.gather(*tasks)

    def _build_targets(self, discovered: dict[str, list[str]]) -> list[ModelTarget]:
        targets: list[ModelTarget] = []
        seen: set[tuple[str, str]] = set()

        def add(provider_name: str, model: str) -> None:
            model = str(model).strip()
            if not model:
                return
            key = (provider_name.lower(), model)
            if key in seen:
                return
            provider = self.providers.get(provider_name)
            if provider is None:
                return
            seen.add(key)
            targets.append(ModelTarget(provider=provider_name, model=model))

        if self.settings.model_chain:
            for provider_name, model in self.settings.model_chain:
                add(provider_name, model)

        if not targets:
            for config in self.settings.providers:
                if not config.configured or config.name not in self.providers:
                    continue
                for model in config.models:
                    add(config.name, model)
                for model in discovered.get(config.name, [])[:20]:
                    add(config.name, model)

        if not targets:
            # Last resort: every configured provider, first model.
            for config in self.settings.providers:
                if config.configured and config.models:
                    add(config.name, config.models[0])

        return targets

    # -- status / control --------------------------------------------------
    def status(self) -> list[RouterStatus]:
        now = time.monotonic()
        result: list[RouterStatus] = []
        for index, target in enumerate(self.targets):
            state = self.states.get(target.label, TargetState())
            result.append(
                RouterStatus(
                    label=target.label,
                    provider=target.provider,
                    model=target.model,
                    current=index == self.current_index,
                    disabled=state.disabled,
                    cooling=state.cooldown_until > now,
                    cooldown_seconds=max(0, int(state.cooldown_until - now)),
                    failures=state.failures,
                    last_error=state.last_error,
                )
            )
        return result

    def current(self) -> ModelTarget | None:
        if 0 <= self.current_index < len(self.targets):
            return self.targets[self.current_index]
        return None

    def force(self, provider: str, model: str) -> ModelTarget:
        for index, target in enumerate(self.targets):
            if target.provider.lower() == provider.lower() and target.model == model:
                self.current_index = index
                state = self.states.setdefault(target.label, TargetState())
                state.disabled = False
                state.cooldown_until = 0.0
                return target
        # Add it on the fly if the provider exists.
        if provider in self.providers:
            target = ModelTarget(provider=provider, model=model)
            self.targets.append(target)
            self.states.setdefault(target.label, TargetState())
            self.current_index = len(self.targets) - 1
            return target
        raise ValueError(f"Unknown provider/model: {provider}:{model}")

    # -- generation --------------------------------------------------------
    async def chat(
        self,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSchema],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_event: EventCallback | None = None,
    ) -> AIResponse:
        async with self._lock:
            return await self._chat_locked(
                system,
                messages,
                tools,
                temperature=self.settings.ai_temperature if temperature is None else temperature,
                max_tokens=self.settings.ai_max_output_tokens if max_tokens is None else max_tokens,
                on_event=on_event,
            )

    async def _chat_locked(
        self,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSchema],
        *,
        temperature: float,
        max_tokens: int,
        on_event: EventCallback | None,
    ) -> AIResponse:
        if not self.targets:
            raise AllModelsFailed([("chain", "no configured models", "")])

        attempts: list[tuple[str, str, str]] = []
        start = self.current_index
        skipped: list[str] = []

        for offset in range(len(self.targets)):
            index = (start + offset) % len(self.targets)
            target = self.targets[index]
            state = self.states.setdefault(target.label, TargetState())
            provider = self.providers.get(target.provider)

            if state.disabled:
                skipped.append(f"{target.label} (disabled)")
                continue
            if state.cooling:
                skipped.append(f"{target.label} ({int(state.cooldown_until - time.monotonic())}s cooldown)")
                continue
            if provider is None:
                state.disabled = True
                skipped.append(f"{target.label} (no provider)")
                continue

            try:
                response = await provider.chat(
                    target.model,
                    system,
                    messages,
                    tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except ProviderError as exc:
                self._record_failure(target, state, exc)
                attempts.append((target.label, exc.short(), target.provider))
                log.warning("Model %s failed: %s", target.label, exc.short())
                if on_event is not None:
                    nxt = self._peek_next(index)
                    await _emit(
                        on_event,
                        {
                            "type": "switch" if nxt and exc.retryable else "failure",
                            "from": target.label,
                            "to": nxt.label if nxt else None,
                            "error": exc.short(),
                            "quota": exc.quota,
                            "status": exc.status,
                        },
                    )
                if exc.provider_fatal:
                    for other in self.targets:
                        if other.provider == target.provider:
                            self.states.setdefault(other.label, TargetState()).disabled = True
                if not exc.retryable and not exc.model_fatal and not exc.quota:
                    # A malformed request will likely fail everywhere, but try
                    # other providers anyway; the error is reported at the end.
                    continue
                continue
            except Exception as exc:  # defensive: provider bugs become failover
                wrapped = ProviderError(f"{type(exc).__name__}: {exc}", retryable=True)
                self._record_failure(target, state, wrapped)
                attempts.append((target.label, wrapped.short(), target.provider))
                log.exception("Unexpected provider error on %s", target.label)
                if on_event is not None:
                    nxt = self._peek_next(index)
                    await _emit(
                        on_event,
                        {
                            "type": "switch" if nxt else "failure",
                            "from": target.label,
                            "to": nxt.label if nxt else None,
                            "error": wrapped.short(),
                            "quota": False,
                            "status": None,
                        },
                    )
                continue

            # Success: stick with this model next time.
            state.failures = 0
            state.cooldown_until = 0.0
            state.last_error = ""
            if index != self.current_index:
                log.info("AI switched to healthy model %s", target.label)
                if on_event is not None:
                    await _emit(
                        on_event,
                        {"type": "recovered", "to": target.label, "model": target.model},
                    )
            self.current_index = index
            return response

        reason = "all models are cooling down" if skipped else "no model succeeded"
        attempts.extend((item, "skipped", "") for item in skipped)
        if on_event is not None:
            await _emit(on_event, {"type": "all_failed", "attempts": attempts})
        raise AllModelsFailed(attempts)

    def _peek_next(self, index: int) -> ModelTarget | None:
        for offset in range(1, len(self.targets)):
            target = self.targets[(index + offset) % len(self.targets)]
            state = self.states.setdefault(target.label, TargetState())
            if not state.disabled and not state.cooling:
                return target
        return None

    def _record_failure(self, target: ModelTarget, state: TargetState, exc: ProviderError) -> None:
        state.failures += 1
        state.last_error = exc.short()
        now = time.monotonic()
        if exc.provider_fatal:
            state.disabled = True
            state.cooldown_until = now + 86400
        elif exc.quota:
            # Exhausted for a while; move on to a different model entirely.
            state.cooldown_until = now + 900
        elif exc.model_fatal:
            state.cooldown_until = now + 21600
        elif exc.status and exc.status >= 500:
            state.cooldown_until = now + min(600, 60 * max(1, state.failures))
        elif exc.status in {400, 422}:
            state.cooldown_until = now + 300
        else:
            state.cooldown_until = now + min(300, 30 * max(1, state.failures))

    # -- convenience -------------------------------------------------------
    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.5,
        max_tokens: int = 800,
        on_event: EventCallback | None = None,
    ) -> str:
        response = await self.chat(
            system,
            [ChatMessage(role="user", content=prompt)],
            [],
            temperature=temperature,
            max_tokens=max_tokens,
            on_event=on_event,
        )
        return response.text


async def _emit(callback: EventCallback, event: dict) -> None:
    try:
        await callback(event)
    except Exception:
        log.debug("AI event callback failed", exc_info=True)
