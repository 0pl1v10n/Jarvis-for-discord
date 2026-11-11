"""Shared helpers for action modules."""

from __future__ import annotations

import re
from typing import Any

import discord

from ..ai.tools import ToolResult
from ..util import (
    channel_mention,
    escape,
    field_value,
    format_dt,
    format_number,
    parse_channel_link,
    parse_message_link,
    resolve_channel,
    resolve_member_async,
    resolve_role,
    snowflake,
    truncate,
    utcnow,
)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def ok(summary: str, *, visible: bool = True) -> ToolResult:
    return ToolResult.success(summary, visible=visible)


def fail(summary: str) -> ToolResult:
    return ToolResult.error(summary)


def sent(summary: str) -> ToolResult:
    return ToolResult.success(summary, visible=True)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


async def member_or_error(ctx, value: Any, *, label: str = "user"):
    member = await resolve_member_async(ctx.guild, value)
    if member is None:
        return None, fail(f"I couldn't find a member matching “{value}”, sir.")
    return member, None


def role_or_error(ctx, value: Any):
    role = resolve_role(ctx.guild, value)
    if role is None:
        return None, fail(f"I couldn't find a role matching “{value}”, sir.")
    return role, None


def channel_or_error(ctx, value: Any, *, default_current: bool = False):
    if (value is None or str(value).strip() == "") and default_current:
        return ctx.channel, None
    channel = resolve_channel(ctx.guild, value)
    if channel is None:
        return None, fail(f"I couldn't find a channel matching “{value}”, sir.")
    return channel, None


def category_or_error(ctx, value: Any):
    if value is None or str(value).strip() == "":
        return None, None
    category = next(
        (c for c in ctx.guild.categories if c.name.lower() == str(value).strip().lower()),
        None,
    )
    if category is None:
        category = resolve_channel(ctx.guild, value, include_voice=False, include_threads=False)
    if not isinstance(category, discord.CategoryChannel):
        return None, fail(f"I couldn't find a category matching “{value}”, sir.")
    return category, None


async def message_or_error(ctx, value: Any, *, default_last: bool = True):
    """Resolve a message from a link, ID, ``last``, or the replied-to message."""
    text = str(value or "").strip()

    if not text or text.lower() in {"last", "latest", "recent"}:
        if default_last and getattr(ctx.message, "reference", None) is not None:
            resolved = getattr(ctx.message.reference, "resolved", None)
            if resolved is not None:
                return resolved, None
        try:
            async for msg in ctx.channel.history(limit=1):
                return msg, None
        except discord.HTTPException:
            pass
        return None, fail("I couldn't find a recent message, sir.")

    if text.lower() in {"reply", "replied"}:
        resolved = getattr(getattr(ctx.message, "reference", None), "resolved", None)
        if resolved is None:
            return None, fail("That message isn't a reply, sir.")
        return resolved, None

    link = parse_message_link(text)
    if link is not None:
        guild_id, channel_id, message_id = link
        if ctx.guild and guild_id != ctx.guild.id:
            return None, fail("That message belongs to another server, sir.")
        channel = ctx.bot.get_channel(channel_id) or await ctx.bot.fetch_channel(channel_id)
        if channel is None:
            return None, fail("I can't access that channel, sir.")
        try:
            return await channel.fetch_message(message_id), None
        except discord.HTTPException:
            return None, fail("I couldn't fetch that message, sir.")

    message_id = snowflake(text)
    if message_id is not None:
        try:
            return await ctx.channel.fetch_message(message_id), None
        except discord.HTTPException:
            return None, fail("I couldn't fetch that message in this channel, sir.")

    return None, fail("Give me a message link, a message ID, or reply to the message, sir.")


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------


def hierarchy_error(ctx, target: discord.Member) -> ToolResult | None:
    guild = ctx.guild
    if guild is None:
        return fail("That only works inside a server, sir.")
    if target.id == guild.owner_id:
        return fail("I can't act on the server owner, sir.")
    if guild.me and target.id == guild.me.id:
        return fail("I can't act on myself, sir.")
    if target.top_role >= guild.me.top_role:
        return fail("That user outranks me, sir.")
    if (
        ctx.member
        and ctx.author_id != guild.owner_id
        and target.id != ctx.author_id
        and target.top_role >= ctx.member.top_role
        and not ctx.is_admin
    ):
        return fail("That user outranks you, sir.")
    return None


def role_hierarchy_error(ctx, role: discord.Role) -> ToolResult | None:
    if role.is_default() or role.managed:
        return fail("I can't modify that role, sir.")
    if role >= ctx.guild.me.top_role:
        return fail("That role is above mine, sir.")
    if (
        ctx.member
        and ctx.author_id != ctx.guild.owner_id
        and role >= ctx.member.top_role
        and not ctx.is_admin
    ):
        return fail("That role is above your own, sir.")
    return None


def bot_permissions_error(channel, **needed) -> ToolResult | None:
    me = channel.guild.me if hasattr(channel, "guild") else None
    if me is None:
        return None
    perms = channel.permissions_for(me)
    missing = [name for name, value in needed.items() if value and not getattr(perms, name, False)]
    if missing:
        names = ", ".join(name.replace("_", " ") for name in missing)
        return fail(f"I'm missing the **{names}** permission there, sir.")
    return None


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------


def member_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(title=f"👤 {member.display_name}", colour=member.colour or discord.Colour.blurple())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Username", value=str(member), inline=True)
    embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
    embed.add_field(name="Nickname", value=escape(member.nick or "—"), inline=True)
    embed.add_field(name="Joined", value=format_dt(member.joined_at), inline=True)
    embed.add_field(name="Created", value=format_dt(member.created_at), inline=True)
    embed.add_field(name="Top role", value=member.top_role.mention, inline=True)
    roles = [role.mention for role in member.roles if not role.is_default()]
    embed.add_field(
        name=f"Roles ({len(roles)})",
        value=field_value(", ".join(roles[:20]) or "—", 1024),
        inline=False,
    )
    embed.add_field(name="Status", value=str(member.status).title(), inline=True)
    embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
    embed.add_field(
        name="Timed out",
        value=format_dt(member.timed_out_until) if member.timed_out_until else "No",
        inline=True,
    )
    embed.set_footer(text=f"Account created {format_dt(member.created_at, 'f')}")
    return embed


def server_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(title=f"🏰 {guild.name}", colour=discord.Colour.blurple())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "—", inline=True)
    embed.add_field(name="Members", value=format_number(guild.member_count or 0), inline=True)
    embed.add_field(
        name="Boosts",
        value=f"{guild.premium_subscription_count} (level {guild.premium_tier})",
        inline=True,
    )
    embed.add_field(
        name="Channels",
        value=f"{len(guild.text_channels)} text · {len(guild.voice_channels)} voice",
        inline=True,
    )
    embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
    embed.add_field(name="Emojis", value=str(len(guild.emojis)), inline=True)
    embed.add_field(name="Created", value=format_dt(guild.created_at), inline=True)
    embed.add_field(name="Verification", value=str(guild.verification_level).title(), inline=True)
    embed.add_field(name="ID", value=f"`{guild.id}`", inline=True)
    if guild.banner:
        embed.set_image(url=guild.banner.url)
    embed.set_footer(text=f"Created {format_dt(guild.created_at, 'f')}")
    return embed


def role_embed(role: discord.Role) -> discord.Embed:
    embed = discord.Embed(title=f"🎭 {role.name}", colour=role.colour or discord.Colour.default())
    embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
    embed.add_field(name="Members", value=format_number(len(role.members)), inline=True)
    embed.add_field(name="Position", value=str(role.position), inline=True)
    embed.add_field(name="Colour", value=f"#{role.colour.value:06x}", inline=True)
    embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
    embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
    embed.add_field(name="Managed", value="Yes" if role.managed else "No", inline=True)
    perms = [name for name, enabled in role.permissions if enabled]
    embed.add_field(
        name="Key permissions",
        value=field_value(", ".join(sorted(perms)[:30]) or "None", 1024),
        inline=False,
    )
    embed.set_footer(text=f"Created {format_dt(role.created_at, 'f')}")
    return embed


def channel_embed(channel) -> discord.Embed:
    embed = discord.Embed(title=f"# {getattr(channel, 'name', channel)}", colour=discord.Colour.blurple())
    embed.add_field(name="ID", value=f"`{channel.id}`", inline=True)
    embed.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
    category = getattr(channel, "category", None)
    embed.add_field(name="Category", value=category.name if category else "—", inline=True)
    if isinstance(channel, discord.TextChannel):
        embed.add_field(name="Topic", value=field_value(channel.topic or "—"), inline=False)
        embed.add_field(name="Slowmode", value=f"{channel.slowmode_delay}s", inline=True)
        embed.add_field(name="NSFW", value="Yes" if channel.nsfw else "No", inline=True)
        embed.add_field(name="Members", value=str(len(channel.members)), inline=True)
    if isinstance(channel, discord.VoiceChannel):
        embed.add_field(name="Bitrate", value=f"{channel.bitrate // 1000} kbps", inline=True)
        embed.add_field(name="User limit", value=str(channel.user_limit or "∞"), inline=True)
        embed.add_field(name="Connected", value=str(len(channel.members)), inline=True)
    embed.add_field(name="Created", value=format_dt(channel.created_at), inline=True)
    return embed


def message_embed(message: discord.Message) -> discord.Embed:
    embed = discord.Embed(
        title=f"💬 Message in #{getattr(message.channel, 'name', 'unknown')}",
        description=truncate(message.content or "*No text*", 2000),
        colour=discord.Colour.blurple(),
        timestamp=message.created_at,
    )
    embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
    embed.add_field(name="ID", value=f"`{message.id}`", inline=True)
    embed.add_field(name="Author", value=message.author.mention, inline=True)
    embed.add_field(name="Jump", value=f"[link]({message.jump_url})", inline=True)
    if message.attachments:
        embed.add_field(
            name=f"Attachments ({len(message.attachments)})",
            value=field_value("\n".join(a.filename for a in message.attachments[:10])),
            inline=False,
        )
    return embed


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def action_list(values) -> str:
    return ", ".join(sorted(values))


def split_topic(text: str, limit: int = 1024) -> str:
    return truncate(text, limit)


def clean_reason(reason: str | None) -> str:
    value = str(reason or "").strip()
    return value[:400] if value else "No reason provided."


def audit_reason(ctx, reason: str | None) -> str:
    base = clean_reason(reason)
    return f"{base} | Requested by {ctx.author} via Jarvis"


def boolean(value: Any, default: bool = False) -> bool:
    from ..util import parse_bool

    return parse_bool(value, default)


def int_between(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def channels_of(guild: discord.Guild):
    return list(guild.categories) + list(guild.text_channels) + list(guild.voice_channels) + list(guild.stage_channels)


def channel_by_id(guild: discord.Guild, value: Any):
    channel_id = parse_channel_link(value)
    if channel_id:
        return guild.get_channel_or_thread(channel_id)
    if str(value or "").strip().isdigit():
        return guild.get_channel_or_thread(int(str(value).strip()))
    return resolve_channel(guild, value)


def message_link(guild: discord.Guild, channel, message) -> str:
    return f"https://discord.com/channels/{guild.id}/{channel.id}/{message.id}"


__all__ = [
    "ok",
    "fail",
    "sent",
    "member_or_error",
    "role_or_error",
    "channel_or_error",
    "category_or_error",
    "message_or_error",
    "hierarchy_error",
    "role_hierarchy_error",
    "bot_permissions_error",
    "member_embed",
    "server_embed",
    "role_embed",
    "channel_embed",
    "message_embed",
    "action_list",
    "split_topic",
    "clean_reason",
    "audit_reason",
    "boolean",
    "int_between",
    "channels_of",
    "channel_by_id",
]
