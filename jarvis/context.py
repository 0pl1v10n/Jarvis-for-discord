"""Execution context shared by tools and event features."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import discord

from . import triggers
from .util import (
    resolve_channel,
    resolve_member,
    resolve_member_async,
    resolve_role,
    split_message,
)


@dataclass
class ActionContext:
    bot: Any
    message: Any
    command: str = ""
    images: list[Any] = field(default_factory=list)
    guild: discord.Guild | None = None
    channel: Any = None
    author: Any = None
    member: discord.Member | None = None

    @classmethod
    def from_message(cls, bot: Any, message: Any, command: str = "", images: list[Any] | None = None):
        return cls(
            bot=bot,
            message=message,
            command=command,
            images=images or [],
            guild=getattr(message, "guild", None),
            channel=getattr(message, "channel", None),
            author=getattr(message, "author", None),
            member=getattr(message, "author", None)
            if isinstance(getattr(message, "author", None), discord.Member)
            else None,
        )

    # -- identity / access -------------------------------------------------
    @property
    def settings(self):
        return self.bot.settings

    @property
    def db(self):
        return self.bot.db

    @property
    def router(self):
        return self.bot.router

    @property
    def agent(self):
        return self.bot.agent

    @property
    def memory(self):
        return self.bot.memory

    @property
    def is_dm(self) -> bool:
        return self.guild is None

    @property
    def guild_id(self) -> int:
        return self.guild.id if self.guild else 0

    @property
    def channel_id(self) -> int:
        return getattr(self.channel, "id", 0)

    @property
    def author_id(self) -> int:
        return getattr(self.author, "id", 0)

    @property
    def display_name(self) -> str:
        return getattr(self.author, "display_name", str(self.author))

    @property
    def is_owner(self) -> bool:
        if self.author is None:
            return False
        if self.author_id in self.settings.owner_ids:
            return True
        return bool(self.guild and self.author_id == self.guild.owner_id)

    @property
    def is_admin(self) -> bool:
        if self.is_owner:
            return True
        perms = getattr(self.member, "guild_permissions", None)
        return bool(perms and perms.administrator)

    def can(self, permission: str | tuple[str, ...] | list[str] | None) -> bool:
        if not permission:
            return True
        if self.is_owner or self.is_admin:
            return True
        perms = getattr(self.member, "guild_permissions", None)
        if perms is None:
            return False
        names = (permission,) if isinstance(permission, str) else tuple(permission)
        return any(bool(getattr(perms, name, False)) for name in names)

    # -- resolution --------------------------------------------------------
    def resolve_member(self, value: Any) -> discord.Member | None:
        return resolve_member(self.guild, value)

    async def resolve_member_async(self, value: Any) -> discord.Member | None:
        return await resolve_member_async(self.guild, value)

    def resolve_role(self, value: Any) -> discord.Role | None:
        return resolve_role(self.guild, value)

    def resolve_channel(self, value: Any, **kwargs):
        return resolve_channel(self.guild, value, **kwargs)

    # -- sending -----------------------------------------------------------
    async def say(self, content: str = "", *, embed: discord.Embed | None = None, **kwargs):
        kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
        try:
            return await self.channel.send(content=content or None, embed=embed, **kwargs)
        except (discord.HTTPException, AttributeError):
            return None

    async def reply(self, content: str = "", *, embed: discord.Embed | None = None, **kwargs):
        kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
        try:
            if hasattr(self.message, "reply"):
                return await self.message.reply(content=content or None, embed=embed, **kwargs)
            return await self.channel.send(content=content or None, embed=embed, **kwargs)
        except (discord.HTTPException, AttributeError):
            return await self.say(content, embed=embed, **kwargs)

    async def send_long(self, text: str, *, reply: bool = False) -> None:
        chunks = split_message(text)
        for index, chunk in enumerate(chunks):
            if index == 0 and reply:
                await self.reply(chunk)
            else:
                await self.say(chunk)

    # -- prompt context ----------------------------------------------------
    def prompt_context(self) -> str:
        if self.guild is None:
            return (
                f"Location: direct message\n"
                f"User: {self.display_name} (id {self.author_id})\n"
                f"Time (UTC): {discord.utils.utcnow().isoformat()}"
            )

        member = self.member
        roles = (
            ", ".join(role.name for role in member.roles if not role.is_default())
            if member
            else "unknown"
        )
        perms = member.guild_permissions if member else None
        important_perms = []
        if perms:
            for name in (
                "administrator",
                "manage_guild",
                "manage_channels",
                "manage_roles",
                "manage_messages",
                "moderate_members",
                "kick_members",
                "ban_members",
                "manage_webhooks",
                "manage_expressions",
                "manage_events",
                "move_members",
            ):
                if getattr(perms, name, False):
                    important_perms.append(name)

        channels = [c.name for c in self.guild.text_channels][:40]
        categories = [c.name for c in self.guild.categories][:20]
        voice = [c.name for c in self.guild.voice_channels][:20]
        roles_preview = [r.name for r in self.guild.roles if not r.is_default()][-30:]

        return (
            f"Location: server '{self.guild.name}' (id {self.guild.id}), "
            f"channel #{getattr(self.channel, 'name', 'unknown')}\n"
            f"User: {self.display_name} (id {self.author_id})\n"
            f"User roles: {truncate_text(roles, 500)}\n"
            f"User key permissions: {', '.join(important_perms) or 'none beyond default'}\n"
            f"Server: {self.guild.member_count or '?'} members, "
            f"{len(self.guild.text_channels)} text channels, "
            f"{len(self.guild.voice_channels)} voice channels, "
            f"{len(self.guild.roles)} roles\n"
            f"Text channels: {truncate_text(', '.join(channels), 900)}\n"
            f"Voice channels: {truncate_text(', '.join(voice), 400)}\n"
            f"Categories: {truncate_text(', '.join(categories), 400)}\n"
            f"Roles: {truncate_text(', '.join(roles_preview), 800)}\n"
            f"Time (UTC): {discord.utils.utcnow().isoformat()}"
        )

    def recent_channel(self, limit: int = 8) -> list[str]:
        return self.memory.recent_channel(self.guild_id, self.channel_id, limit=limit)


def truncate_text(text: str, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def command_remainder(message, bot_user) -> str:
    return triggers.strip_trigger(getattr(message, "content", ""), bot_user)
