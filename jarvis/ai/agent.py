"""The agent loop: conversation, tool calling, memory, and failover."""

from __future__ import annotations

import logging

import discord

from ..runner import ActionRunner
from ..util import split_message
from .prompt import build_simple_system, build_system_prompt
from .router import ModelRouter
from .tools import REGISTRY, ToolRegistry
from .types import AllModelsFailed, ChatMessage, ImageInput, ToolCall

log = logging.getLogger(__name__)


class JarvisAgent:
    def __init__(self, bot):
        self.bot = bot
        self.settings = bot.settings
        self.router: ModelRouter = bot.router
        self.registry: ToolRegistry = REGISTRY
        self.runner = ActionRunner(bot)
        self.memory = bot.memory

    # -- main entry point --------------------------------------------------
    async def respond(self, ctx, text: str, images: list[ImageInput] | None = None) -> str | None:
        text = str(text or "").strip()
        if not text and not images:
            return None

        messages: list[ChatMessage] = []
        for turn in self.memory.history(ctx.guild_id, ctx.channel_id, ctx.author_id):
            messages.append(ChatMessage(role=turn.role, content=turn.content))
        messages.append(ChatMessage(role="user", content=text, images=images or []))

        system = build_system_prompt(ctx, self.settings, self.registry)
        schemas = self.registry.schemas()
        visible_tools = 0
        executed_tools = 0

        async def on_event(event: dict) -> None:
            await self._handle_router_event(ctx, event)

        for _round in range(max(1, self.settings.ai_max_tool_rounds)):
            try:
                response = await self.router.chat(
                    system,
                    messages,
                    schemas,
                    on_event=on_event,
                )
            except AllModelsFailed as exc:
                log.error("%s", exc)
                await self._all_failed(ctx)
                return None

            if response.tool_calls:
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=response.text,
                        tool_calls=response.tool_calls,
                    )
                )
                for call in response.tool_calls[:6]:
                    executed_tools += 1
                    result = await self._run_tool(ctx, call)
                    if result.visible:
                        visible_tools += 1
                    messages.append(
                        ChatMessage(
                            role="tool",
                            content=result.summary or ("OK" if result.ok else "Failed"),
                            tool_call_id=call.id,
                            tool_name=call.name,
                        )
                    )
                continue

            final = (response.text or "").strip()
            if final:
                self.memory.add_user(ctx.guild_id, ctx.channel_id, ctx.author_id, text)
                self.memory.add_assistant(ctx.guild_id, ctx.channel_id, ctx.author_id, final)
                return final

            # The model may return an empty message after a tool call. If the
            # tools already spoke for themselves, stay quiet; otherwise say done.
            if executed_tools and visible_tools:
                self.memory.add_user(ctx.guild_id, ctx.channel_id, ctx.author_id, text)
                return None
            final = "Done, sir."
            self.memory.add_user(ctx.guild_id, ctx.channel_id, ctx.author_id, text)
            self.memory.add_assistant(ctx.guild_id, ctx.channel_id, ctx.author_id, final)
            return final

        fallback = "I got as far as I could, sir. Ask me to continue if you'd like."
        self.memory.add_user(ctx.guild_id, ctx.channel_id, ctx.author_id, text)
        self.memory.add_assistant(ctx.guild_id, ctx.channel_id, ctx.author_id, fallback)
        return fallback

    async def _run_tool(self, ctx, call: ToolCall):
        spec = self.registry.get(call.name)
        if spec is None:
            from .tools import ToolResult

            return ToolResult.error(f"I don't have a tool called `{call.name}`, sir.")
        try:
            return await self.runner.execute(ctx, spec, call.arguments)
        except Exception as exc:
            log.exception("Tool execution failed: %s", call.name)
            from .tools import ToolResult

            return ToolResult.error(f"I couldn't complete that, sir: {exc}")

    # -- quick text generation (used by utility actions) -------------------
    async def simple(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 900,
        temperature: float = 0.4,
    ) -> str:
        return await self.router.complete(
            system or build_simple_system(),
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    # -- provider events ---------------------------------------------------
    async def _handle_router_event(self, ctx, event: dict) -> None:
        kind = event.get("type")
        if kind == "switch" and self.settings.announce_model_switches:
            old = event.get("from", "model")
            new = event.get("to") or "the next model"
            reason = event.get("error", "unavailable")
            quota = " (quota exhausted)" if event.get("quota") else ""
            await _quiet_send(
                ctx.channel,
                f"⚠️ `{old}` is unavailable{quota}. Switching to `{new}`.",
            )
        elif kind == "recovered" and self.settings.announce_model_switches:
            # Only announce recovery if it follows a visible switch. Keep this
            # subtle to avoid spamming.
            log.info("AI recovered on %s", event.get("to"))
        elif kind == "failure":
            log.warning("AI model failure: %s", event.get("error"))

    async def _all_failed(self, ctx) -> None:
        attempts = []
        for status in self.router.status():
            if status.last_error:
                attempts.append(f"`{status.label}`: {status.last_error[:120]}")
        detail = "\n".join(attempts[-5:])
        message = "❌ Every configured AI model is unavailable right now, sir."
        if detail:
            message += f"\n{detail}"
        message += "\nI'll try again on your next message."
        await _quiet_send(ctx.channel, message)
        if self.settings.shutdown_on_total_failure:
            await self.bot.close()


async def _quiet_send(channel, text: str) -> None:
    try:
        for chunk in split_message(text):
            await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
    except (discord.HTTPException, AttributeError):
        pass
