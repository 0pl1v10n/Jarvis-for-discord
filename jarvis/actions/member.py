"""Member moderation, notes, roles on members, DMs, and voice moderation."""

from __future__ import annotations

from datetime import timedelta

import discord

from ..ai.tools import Risk, action, tool
from ..database import Database
from ..util import (
    best_match,
    format_dt,
    human_delta,
    parse_duration,
    snowflake,
    truncate,
    utcnow,
)
from .helpers import (
    audit_reason,
    boolean,
    channel_or_error,
    fail,
    hierarchy_error,
    int_between,
    member_embed,
    member_or_error,
    ok,
    role_hierarchy_error,
    role_or_error,
    sent,
)

ACTIONS = {
    "info": action(Risk.SAFE, description="Show member details"),
    "warn": action(Risk.LOW, permission="moderate_members", confirm=False),
    "warnings": action(Risk.SAFE, confirm=False),
    "clear_warnings": action(Risk.MEDIUM, permission="moderate_members", confirm=False),
    "remove_warning": action(Risk.MEDIUM, permission="moderate_members", confirm=False),
    "timeout": action(Risk.HIGH, permission="moderate_members", confirm=True),
    "remove_timeout": action(Risk.LOW, permission="moderate_members", confirm=False),
    "kick": action(Risk.HIGH, permission="kick_members", confirm=True),
    "ban": action(Risk.HIGH, permission="ban_members", confirm=True),
    "softban": action(Risk.HIGH, permission="ban_members", confirm=True),
    "tempban": action(Risk.HIGH, permission="ban_members", confirm=True),
    "unban": action(Risk.MEDIUM, permission="ban_members", confirm=False),
    "set_nickname": action(Risk.MEDIUM, permission="manage_nicknames", confirm=False),
    "reset_nickname": action(Risk.MEDIUM, permission="manage_nicknames", confirm=False),
    "add_role": action(Risk.MEDIUM, permission="manage_roles", confirm=False),
    "remove_role": action(Risk.MEDIUM, permission="manage_roles", confirm=False),
    "dm": action(Risk.MEDIUM, permission="manage_messages", confirm=False),
    "note": action(Risk.LOW, permission="moderate_members", confirm=False),
    "notes": action(Risk.SAFE, confirm=False),
    "clear_notes": action(Risk.MEDIUM, permission="moderate_members", confirm=False),
    "voice_mute": action(Risk.MEDIUM, permission="mute_members", confirm=False),
    "voice_unmute": action(Risk.MEDIUM, permission="mute_members", confirm=False),
    "voice_deafen": action(Risk.MEDIUM, permission="deafen_members", confirm=False),
    "voice_undeafen": action(Risk.MEDIUM, permission="deafen_members", confirm=False),
    "move_voice": action(Risk.MEDIUM, permission="move_members", confirm=False),
    "disconnect_voice": action(Risk.MEDIUM, permission="move_members", confirm=False),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": "The member operation to perform.",
        },
        "user": {
            "type": "string",
            "description": "Member username, display name, nickname, or @mention. Never ask for an ID.",
        },
        "reason": {"type": "string", "description": "Reason for the action or note"},
        "duration": {
            "type": "string",
            "description": "Duration such as 10m, 2h, 7d. Used by timeout/tempban.",
        },
        "minutes": {"type": "integer", "description": "Legacy duration in minutes"},
        "days": {"type": "integer", "description": "Days of messages to delete for bans (0-7)"},
        "role": {"type": "string", "description": "Role name or @mention"},
        "nickname": {"type": "string", "description": "New nickname (max 32 characters)"},
        "message": {"type": "string", "description": "DM message text"},
        "note": {"type": "string", "description": "Staff note text"},
        "warning_id": {"type": "integer", "description": "Warning ID to remove"},
        "channel": {"type": "string", "description": "Voice channel name for move_voice"},
        "silent": {"type": "boolean", "description": "Suppress the confirmation message"},
    },
    "required": ["action", "user"],
}


@tool(
    "manage_member",
    "Moderate or manage one member: info, warn, warnings, clear/remove warnings, timeout, "
    "kick, ban, softban, tempban, unban, nickname, add/remove role, DM, staff notes, and "
    "voice mute/deafen/move/disconnect.",
    SCHEMA,
    category="moderation",
    actions=ACTIONS,
)
async def manage_member(ctx, args):
    raw_action = str(args["action"]).lower()
    user_value = args.get("user") or args.get("member")

    if raw_action == "unban":
        return await _unban(ctx, args)
    if raw_action in {"warnings", "notes"}:
        return await _view_records(ctx, args, raw_action)

    target, error = await member_or_error(ctx, user_value)
    if error:
        return error

    if raw_action == "info":
        embed = member_embed(target)
        await ctx.say(embed=embed)
        warnings = ctx.db.warning_count(ctx.guild_id, target.id)
        if warnings:
            await ctx.say(f"⚠️ {target.mention} has **{warnings}** warning(s).")
        return sent(f"Showed information for {target.display_name}.")

    if raw_action == "warn":
        reason = args.get("reason") or "No reason provided."
        warning_id = ctx.db.add_warning(ctx.guild_id, target.id, ctx.author_id, reason)
        count = ctx.db.warning_count(ctx.guild_id, target.id)
        embed = discord.Embed(
            title="⚠️ Warning issued",
            colour=discord.Colour.orange(),
            description=f"{target.mention} has been warned.",
        )
        embed.add_field(name="Reason", value=truncate(reason, 500))
        embed.add_field(name="Warning ID", value=f"`{warning_id}`", inline=True)
        embed.add_field(name="Total warnings", value=str(count), inline=True)
        await ctx.say(embed=embed)
        return sent(f"Warned {target.display_name} (warning #{warning_id}).")

    if raw_action == "clear_warnings":
        removed = ctx.db.clear_warnings(ctx.guild_id, target.id)
        await ctx.say(f"🧹 Cleared **{removed}** warning(s) for {target.mention}.")
        return sent(f"Cleared {removed} warnings for {target.display_name}.")

    if raw_action == "remove_warning":
        warning_id = int_between(args.get("warning_id"), 0, 2**31, 0)
        if not warning_id:
            return fail("Please provide the warning ID to remove, sir.")
        removed = ctx.db.remove_warning(ctx.guild_id, warning_id)
        if not removed:
            return fail("I couldn't find that warning, sir.")
        await ctx.say(f"✅ Removed warning `{warning_id}` for {target.mention}.")
        return sent(f"Removed warning {warning_id}.")

    if raw_action == "timeout":
        problem = hierarchy_error(ctx, target)
        if problem:
            return problem
        if target.id == ctx.author_id:
            return fail("You can't time yourself out, sir.")
        seconds = _duration_seconds(args)
        if seconds <= 0:
            return fail("Tell me a duration from 1 second up to 28 days, sir.")
        seconds = min(seconds, 28 * 86400)
        until = utcnow() + timedelta(seconds=seconds)
        await target.edit(
            timed_out_until=until,
            reason=audit_reason(ctx, args.get("reason") or "Timeout"),
        )
        await ctx.say(
            f"🔇 {target.mention} has been timed out for **{human_delta(timedelta(seconds=seconds))}**."
        )
        return sent(f"Timed out {target.display_name}.")

    if raw_action == "remove_timeout":
        problem = hierarchy_error(ctx, target)
        if problem:
            return problem
        await target.edit(
            timed_out_until=None,
            reason=audit_reason(ctx, "Timeout removed"),
        )
        await ctx.say(f"🔊 {target.mention}'s timeout has been removed.")
        return sent(f"Removed timeout from {target.display_name}.")

    if raw_action in {"kick", "ban", "softban", "tempban"}:
        problem = hierarchy_error(ctx, target)
        if problem:
            return problem
        if target.id == ctx.author_id and raw_action != "ban":
            return fail("I can't do that to you, sir.")
        reason = audit_reason(ctx, args.get("reason"))
        days = int_between(args.get("days"), 0, 7, 0)
        duration = args.get("duration") or args.get("minutes")

        if raw_action == "kick":
            await target.kick(reason=reason)
            await ctx.say(f"👢 {target.mention} has been kicked.")
            return sent(f"Kicked {target.display_name}.")

        if raw_action == "ban":
            await target.ban(reason=reason, delete_message_days=days)
            await ctx.say(f"🔨 {target.mention} has been banned.")
            return sent(f"Banned {target.display_name}.")

        if raw_action == "softban":
            await target.ban(reason=f"{reason} [softban]", delete_message_days=max(1, days))
            await ctx.guild.unban(target, reason=f"{reason} [softban unban]")
            await ctx.say(f"🧹 {target.mention} has been softbanned (messages removed).")
            return sent(f"Softbanned {target.display_name}.")

        # tempban
        delta = parse_duration(duration, default_unit="minutes")
        if delta is None or delta.total_seconds() <= 0:
            return fail("Tell me how long the tempban should last, sir (e.g. 2h, 7d).")
        if delta.total_seconds() > 365 * 86400:
            return fail("That tempban is too long, sir.")
        await target.ban(reason=reason, delete_message_days=days)
        run_at = (utcnow() + delta).isoformat()
        ctx.db.add_scheduled(
            ctx.guild_id,
            "unban",
            target.id,
            run_at,
            {"reason": "Temporary ban expired"},
        )
        await ctx.say(
            f"🔨 {target.mention} has been banned for **{human_delta(delta)}**."
        )
        return sent(f"Tempbanned {target.display_name} for {human_delta(delta)}.")

    if raw_action in {"set_nickname", "reset_nickname"}:
        problem = hierarchy_error(ctx, target)
        if problem:
            return problem
        nickname = None
        if raw_action == "set_nickname":
            nickname = str(args.get("nickname") or "").strip()[:32]
            if not nickname:
                return fail("Tell me the new nickname, sir.")
        await target.edit(nick=nickname, reason=audit_reason(ctx, "Nickname change"))
        if nickname:
            await ctx.say(f"✏️ {target.mention}'s nickname is now **{nickname}**.")
        else:
            await ctx.say(f"✏️ Reset {target.mention}'s nickname.")
        return sent("Nickname updated.")

    if raw_action in {"add_role", "remove_role"}:
        role, error = role_or_error(ctx, args.get("role"))
        if error:
            return error
        role_problem = role_hierarchy_error(ctx, role)
        if role_problem:
            return role_problem
        if raw_action == "add_role":
            await target.add_roles(role, reason=audit_reason(ctx, "Role added"))
            await ctx.say(f"✅ Gave **{role.name}** to {target.mention}.")
        else:
            await target.remove_roles(role, reason=audit_reason(ctx, "Role removed"))
            await ctx.say(f"➖ Removed **{role.name}** from {target.mention}.")
        return sent(f"{raw_action.replace('_', ' ').title()} complete.")

    if raw_action == "dm":
        text = str(args.get("message") or "").strip()
        if not text:
            return fail("Tell me what to send, sir.")
        embed = discord.Embed(
            title="Message from Jarvis",
            description=truncate(text, 3900),
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(text=f"Sent on behalf of {ctx.author}")
        try:
            await target.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return fail(f"{target.display_name} has DMs closed, sir.")
        await ctx.say(f"📨 Sent a DM to {target.mention}.")
        return sent("DM sent.")

    if raw_action in {"note", "clear_notes"}:
        if raw_action == "note":
            note = str(args.get("note") or args.get("reason") or "").strip()
            if not note:
                return fail("Tell me the note text, sir.")
            note_id = ctx.db.add_note(ctx.guild_id, target.id, ctx.author_id, note)
            await ctx.say(f"🗒️ Note `{note_id}` added for {target.mention}.")
            return sent("Note added.")
        removed = ctx.db.clear_notes(ctx.guild_id, target.id)
        await ctx.say(f"🧹 Cleared **{removed}** note(s) for {target.mention}.")
        return sent("Notes cleared.")

    if raw_action in {
        "voice_mute",
        "voice_unmute",
        "voice_deafen",
        "voice_undeafen",
        "move_voice",
        "disconnect_voice",
    }:
        if target.voice is None or target.voice.channel is None:
            return fail(f"{target.display_name} isn't in a voice channel, sir.")
        if raw_action == "voice_mute":
            await target.edit(mute=True, reason=audit_reason(ctx, "Voice mute"))
            return sent(f"🔇 Server-muted {target.display_name}.")
        if raw_action == "voice_unmute":
            await target.edit(mute=False, reason=audit_reason(ctx, "Voice unmute"))
            return sent(f"🔊 Server-unmuted {target.display_name}.")
        if raw_action == "voice_deafen":
            await target.edit(deafen=True, reason=audit_reason(ctx, "Voice deafen"))
            return sent(f"🔕 Server-deafened {target.display_name}.")
        if raw_action == "voice_undeafen":
            await target.edit(deafen=False, reason=audit_reason(ctx, "Voice undeafen"))
            return sent(f"🔔 Server-undeafened {target.display_name}.")
        if raw_action == "move_voice":
            channel, error = channel_or_error(ctx, args.get("channel"))
            if error:
                return error
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                return fail("That isn't a voice channel, sir.")
            await target.move_to(channel, reason=audit_reason(ctx, "Voice move"))
            return sent(f"Moved {target.display_name} to {channel.name}.")
        await target.move_to(None, reason=audit_reason(ctx, "Voice disconnect"))
        return sent(f"Disconnected {target.display_name} from voice.")

    return fail(f"I don't know how to do `{raw_action}` for a member, sir.")


async def _unban(ctx, args):
    value = args.get("user")
    ban = await _find_ban(ctx.guild, value)
    if ban is None:
        return fail(f"I couldn't find a ban matching “{value}”, sir.")
    await ctx.guild.unban(
        ban.user,
        reason=audit_reason(ctx, args.get("reason") or "Ban removed"),
    )
    await ctx.say(f"🔓 **{ban.user}** has been unbanned.")
    return sent(f"Unbanned {ban.user}.")


async def _find_ban(guild: discord.Guild, value):
    text = str(value or "").strip()
    if not text:
        return None
    target_id = snowflake(text)
    try:
        bans = [entry async for entry in guild.bans(limit=None)]
    except discord.HTTPException:
        return None
    if target_id is not None:
        return next((entry for entry in bans if entry.user.id == target_id), None)
    return best_match(
        text.lstrip("@").strip(),
        bans,
        lambda entry: [entry.user.name, entry.user.display_name, str(entry.user)],
    )


async def _view_records(ctx, args, kind: str):
    value = args.get("user")
    if value and str(value).strip().lower() not in {"me", "myself", "self"}:
        target, error = await member_or_error(ctx, value)
        if error:
            return error
    else:
        target = ctx.member or ctx.author
    if not isinstance(target, discord.Member):
        return fail("That only works inside a server, sir.")

    if target.id != ctx.author_id and not ctx.can("moderate_members"):
        return fail("Only moderators can view another member's records, sir.")

    if kind == "warnings":
        rows = ctx.db.warnings(ctx.guild_id, target.id)
        if not rows:
            await ctx.say(f"✅ {target.mention} has no warnings.")
            return sent("No warnings.")
        embed = discord.Embed(
            title=f"⚠️ Warnings for {target.display_name}",
            colour=discord.Colour.orange(),
            description=f"**{len(rows)}** warning(s) on record.",
        )
        for row in rows[:15]:
            embed.add_field(
                name=f"#{row['id']} · {format_dt(_parse_iso(row['created_at']))}",
                value=f"**Reason:** {truncate(row['reason'], 300)}\n**By:** <@{row['moderator_id']}>",
                inline=False,
            )
        await ctx.say(embed=embed)
        return sent(f"Showed {len(rows)} warning(s).")

    rows = ctx.db.notes(ctx.guild_id, target.id)
    if not rows:
        await ctx.say(f"🗒️ {target.mention} has no staff notes.")
        return sent("No notes.")
    embed = discord.Embed(
        title=f"🗒️ Notes for {target.display_name}",
        colour=discord.Colour.blurple(),
        description=f"**{len(rows)}** note(s) on record.",
    )
    for row in rows[:15]:
        embed.add_field(
            name=f"#{row['id']} · {format_dt(_parse_iso(row['created_at']))}",
            value=f"{truncate(row['note'], 500)}\n— <@{row['author_id']}>",
            inline=False,
        )
    await ctx.say(embed=embed)
    return sent(f"Showed {len(rows)} note(s).")


def _parse_iso(value: str):
    from datetime import datetime

    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _duration_seconds(args) -> int:
    raw = args.get("duration")
    if raw in (None, ""):
        raw = args.get("minutes")
    delta = parse_duration(raw, default_unit="minutes")
    return int(delta.total_seconds()) if delta else 0
