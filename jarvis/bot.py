"""Jarvis v2 Discord client."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp
import discord
from discord import app_commands

from . import triggers
from .actions import *  # noqa: F401,F403  (imports every action module)
from .ai.agent import JarvisAgent
from .ai.router import ModelRouter
from .ai.types import ImageInput
from .ai.tools import Risk
from .confirmations import ApprovalManager, ConfirmationManager
from .context import ActionContext
from .database import Database
from .features import Features
from .memory import ConversationMemory
from .util import split_message, truncate, utcnow
from .actions.voice import MusicManager

log = logging.getLogger(__name__)


class JarvisBot(discord.Client):
    def __init__(self, settings):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.voice_states = True
        intents.presences = settings.enable_presences
        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True, replied_user=False
            ),
        )
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.db = Database(
            self,
            category_name=settings.data_category_name,
            flush_seconds=settings.data_flush_seconds,
            legacy_path=settings.database_path,
        )
        self.memory = ConversationMemory(settings)
        self.session: aiohttp.ClientSession | None = None
        self.router: ModelRouter | None = None
        self.agent: JarvisAgent | None = None
        self.confirmations = ConfirmationManager(settings)
        self.approvals = ApprovalManager(self)
        self.features = Features(self)
        self.music = MusicManager(self)
        self.started_at = utcnow()
        self._seen_ready = False
        self._features_started = False
        self._last_request: dict[int, float] = {}
        self._active_requests: set[int] = set()

    # -- lifecycle ---------------------------------------------------------
    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120),
            headers={"User-Agent": "Jarvis/2.0 (Discord bot)"},
        )
        self.router = ModelRouter(self.settings, self.session)
        await self.router.initialize(discover=self.settings.auto_discover)
        self.agent = JarvisAgent(self)

        from .slash import setup_commands

        await setup_commands(self)
        log.info("Setup complete; %d AI targets loaded", len(self.router.targets))

    async def on_ready(self) -> None:
        if self._seen_ready:
            log.info("Reconnected as %s", self.user)
        else:
            self._seen_ready = True
            self.started_at = utcnow()
            log.info("Jarvis is online as %s (%s)", self.user, self.user.id if self.user else "?")
        try:
            await self.db.initialize()
        except Exception:
            log.exception("Could not initialise the Discord channel data store")
        if not self._features_started:
            try:
                await self.features.startup()
                self._features_started = True
            except Exception:
                log.exception("Could not start community features")
        try:
            await self.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.listening,
                    name="“Jarvis” — at your service",
                ),
                status=discord.Status.online,
            )
        except Exception:
            pass
        if not getattr(self, "_synced", False):
            try:
                if self.settings.guild_id:
                    guild = discord.Object(id=self.settings.guild_id)
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                else:
                    await self.tree.sync()
                self._synced = True
                log.info("Slash commands synced")
            except Exception:
                log.exception("Slash command sync failed")

    async def close(self) -> None:
        try:
            await self.features.stop()
        except Exception:
            log.debug("Shutdown cleanup failed", exc_info=True)
        if self.music is not None:
            for guild in list(self.guilds):
                voice = guild.voice_client
                if voice is not None:
                    try:
                        await voice.disconnect(force=True)
                    except Exception:
                        pass
        if self.session is not None:
            await self.session.close()
        try:
            await self.db.close()
        except Exception:
            log.debug("Data store cleanup failed", exc_info=True)
        await super().close()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        try:
            await self.db.ensure_guild(guild)
        except Exception:
            log.exception("Could not prepare the data store for guild %s", guild.id)

    # -- messages ----------------------------------------------------------
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or self.user is None:
            return

        try:
            if await self.confirmations.handle_message(message):
                return
            if await self.approvals.handle_message(message):
                return
        except Exception:
            log.exception("Confirmation/approval handling failed")

        try:
            await self.features.on_message(message)
        except Exception:
            log.exception("Feature message handling failed")

        auto = self.settings.auto_action_without_wake and message.guild is not None
        if not triggers.should_handle(message, self.user, auto_action=auto):
            return

        if message.author.id in self._active_requests:
            return
        now = time.monotonic()
        last = self._last_request.get(message.author.id, 0.0)
        if now - last < max(0.5, self.settings.cooldown_seconds * 0.5):
            return
        self._last_request[message.author.id] = now
        self._active_requests.add(message.author.id)
        try:
            await self._respond(message)
        except Exception:
            log.exception("Failed to respond to message")
            try:
                await message.channel.send("I ran into a problem handling that, sir.")
            except discord.HTTPException:
                pass
        finally:
            self._active_requests.discard(message.author.id)

    async def _respond(self, message: discord.Message) -> None:
        ctx = ActionContext.from_message(self, message)
        prompt = triggers.strip_trigger(message.content, self.user)
        images = await self._images(message)

        if not prompt.strip() and not images:
            await message.channel.send(triggers.empty_prompt_reply())
            return

        async with message.channel.typing():
            reply = await self.agent.respond(ctx, prompt, images) if self.agent else None

        if not reply:
            return
        for index, chunk in enumerate(split_message(reply)):
            try:
                if index == 0 and getattr(message, "guild", None) is not None:
                    await message.reply(
                        chunk,
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions(
                            everyone=False, roles=False, users=True, replied_user=False
                        ),
                    )
                else:
                    await message.channel.send(chunk)
            except discord.HTTPException:
                break

    async def _images(self, message: discord.Message) -> list[ImageInput]:
        images: list[ImageInput] = []
        if not message.attachments:
            return images
        for attachment in message.attachments[: self.settings.max_images]:
            content_type = attachment.content_type or ""
            if not content_type.startswith("image/"):
                continue
            if attachment.size > self.settings.max_image_bytes:
                continue
            try:
                data = await attachment.read()
            except discord.HTTPException:
                continue
            images.append(
                ImageInput(data=data, mime=content_type, name=attachment.filename)
            )
        return images

    # -- auditing ----------------------------------------------------------
    async def record_action(
        self,
        ctx,
        *,
        spec,
        action: str,
        args: dict[str, Any],
        result,
        approver=None,
        risk=Risk.SAFE,
    ) -> None:
        summary = truncate(result.summary or action, 400)
        detail = (
            f"{ctx.author} ({ctx.author_id}) used {spec.name}/{action} in "
            f"#{getattr(ctx.channel, 'name', 'DM')} ({ctx.guild_id}). "
            f"Args: {truncate(str(args), 1200)}. Result: {summary}"
        )
        try:
            self.db.audit(ctx.guild_id, ctx.author_id, f"{spec.name}:{action}", detail)
        except Exception:
            log.debug("Audit write failed", exc_info=True)
        if risk < Risk.LOW:
            return

        channel = None
        if self.settings.log_channel_id:
            channel = self.get_channel(self.settings.log_channel_id)
        if channel is None and ctx.guild is not None:
            config = self.db.get_config(ctx.guild.id, "logging", {}) or {}
            channel = ctx.guild.get_channel(int(config.get("channel_id") or 0))
        if channel is None:
            return

        colour = {
            Risk.LOW: discord.Colour.blurple(),
            Risk.MEDIUM: discord.Colour.gold(),
            Risk.HIGH: discord.Colour.orange(),
            Risk.CRITICAL: discord.Colour.red(),
        }.get(risk, discord.Colour.dark_grey())
        embed = discord.Embed(
            title=f"{'✅' if result.ok else '❌'} {spec.name} · {action}",
            description=truncate(result.summary or "—", 1500),
            colour=colour,
            timestamp=utcnow(),
        )
        embed.add_field(name="Actor", value=f"{ctx.author.mention} (`{ctx.author_id}`)", inline=True)
        embed.add_field(name="Risk", value=risk.name, inline=True)
        embed.add_field(name="Channel", value=getattr(ctx.channel, "mention", "DM"), inline=True)
        if approver is not None:
            embed.add_field(name="Approved by", value=str(approver), inline=True)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # -- helpers -----------------------------------------------------------
    @property
    def resolved_ai(self) -> JarvisAgent | None:
        return self.agent

    def current_model(self) -> str | None:
        if self.router is None or self.router.current() is None:
            return None
        return self.router.current().label


__all__ = ["JarvisBot"]
