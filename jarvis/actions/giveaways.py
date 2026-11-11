"""Giveaways with buttons, requirements, ending, and rerolls."""

from __future__ import annotations

import random
from datetime import timedelta

import discord

from ..ai.tools import Risk, action, tool
from ..util import format_dt, human_delta, parse_duration, truncate, utcnow
from .helpers import (
    audit_reason,
    channel_or_error,
    fail,
    int_between,
    role_or_error,
    sent,
)

ACTIONS = {
    "create": action(Risk.MEDIUM, permission="manage_guild"),
    "end": action(Risk.MEDIUM, permission="manage_guild"),
    "reroll": action(Risk.MEDIUM, permission="manage_guild"),
    "cancel": action(Risk.MEDIUM, permission="manage_guild"),
    "list": action(Risk.SAFE),
    "entries": action(Risk.SAFE),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "channel": {"type": "string"},
        "prize": {"type": "string"},
        "duration": {"type": "string", "description": "e.g. 1h, 2d"},
        "winners": {"type": "integer"},
        "role": {"type": "string", "description": "Required role to enter"},
        "level": {"type": "integer", "description": "Required level to enter"},
        "identifier": {"type": "string", "description": "Giveaway ID or message ID"},
    },
    "required": ["action"],
}


@tool(
    "manage_giveaway",
    "Create, list, end, reroll, cancel, or inspect giveaways.",
    SCHEMA,
    category="community",
    actions=ACTIONS,
)
async def manage_giveaway(ctx, args):
    operation = str(args["action"]).lower()
    guild_id = ctx.guild_id

    if operation == "create":
        prize = str(args.get("prize") or "").strip()
        if not prize:
            return fail("Tell me what the prize is, sir.")
        delta = parse_duration(args.get("duration") or "1h", default_unit="hours")
        if delta is None or delta.total_seconds() < 30:
            return fail("Give me a duration of at least 30 seconds, sir.")
        channel, error = channel_or_error(ctx, args.get("channel"), default_current=True)
        if error:
            return error
        if not isinstance(channel, discord.TextChannel):
            return fail("Giveaways need a text channel, sir.")
        winners = int_between(args.get("winners"), 1, 50, 1)
        role = None
        if args.get("role"):
            role, error = role_or_error(ctx, args.get("role"))
            if error:
                return error
        level = int_between(args.get("level"), 0, 1000, 0) or None
        ends_at = utcnow() + delta
        giveaway_id = ctx.db.add_giveaway(
            guild_id,
            channel.id,
            None,
            prize,
            winners,
            ends_at.isoformat(),
            ctx.author_id,
            role.id if role else None,
            level,
        )
        embed = _giveaway_embed(
            prize=prize,
            host=ctx.author.mention,
            ends_at=ends_at,
            winners=winners,
            entries=0,
            role=role,
            level=level,
            ended=False,
        )
        message = await channel.send(
            embed=embed,
            view=GiveawayView(ctx.bot, giveaway_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        ctx.db.set_giveaway_message(giveaway_id, message.id)
        await ctx.say(f"🎉 Giveaway **#{giveaway_id}** started in {channel.mention}.")
        return sent(f"Giveaway {giveaway_id} created.")

    if operation == "list":
        rows = ctx.db.active_giveaways()
        rows = [row for row in rows if int(row["guild_id"]) == guild_id]
        if not rows:
            await ctx.say("There are no active giveaways, sir.")
            return sent("No giveaways.")
        lines = [
            f"🎉 **#{row['id']}** {truncate(row['prize'], 80)} — ends {format_dt(_dt(row['ends_at']))} "
            f"({row['winners']} winner(s), {ctx.db.giveaway_entry_count(int(row['id']))} entries)"
            for row in rows[:30]
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(rows)} giveaways.")

    giveaway_id = _find_giveaway(ctx, args.get("identifier"))
    if giveaway_id is None:
        return fail("I couldn't find that giveaway, sir.")
    row = ctx.db.giveaway(giveaway_id)
    if row is None:
        return fail("I couldn't find that giveaway, sir.")

    if operation == "entries":
        count = ctx.db.giveaway_entry_count(giveaway_id)
        await ctx.say(f"🎟️ Giveaway **#{giveaway_id}** has **{count}** entr{'y' if count == 1 else 'ies'}.")
        return sent(f"{count} entries.")

    if operation == "cancel":
        ctx.db.end_giveaway(giveaway_id)
        await _update_giveaway_message(ctx.bot, row, ended=True, winners_text="Cancelled.")
        await ctx.say(f"🛑 Cancelled giveaway **#{giveaway_id}**.")
        return sent("Giveaway cancelled.")

    if operation == "reroll":
        winners = ctx.db.pick_giveaway_winners(giveaway_id, int(row["winners"]))
        if not winners:
            return fail("There are no entries to reroll, sir.")
        mentions = ", ".join(f"<@{user_id}>" for user_id in winners)
        await ctx.say(f"🎉 New winner(s) for **{truncate(row['prize'], 100)}**: {mentions}")
        return sent("Rerolled.")

    winners = await finish_giveaway(ctx.bot, giveaway_id)
    if winners is None:
        return fail("I couldn't finish that giveaway, sir.")
    await ctx.say(
        f"🎉 Giveaway **#{giveaway_id}** ended. Winner(s): "
        f"{', '.join(f'<@{w}>' for w in winners) or 'none'}"
    )
    return sent("Giveaway ended.")


def _dt(value: str):
    from datetime import datetime

    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _find_giveaway(ctx, identifier) -> int | None:
    text = str(identifier or "").strip()
    if text.isdigit():
        candidate = int(text)
        if ctx.db.giveaway(candidate) is not None:
            return candidate
        row = ctx.db.giveaway_by_message(ctx.guild_id, candidate)
        if row is not None:
            return int(row["id"])
    rows = [
        row
        for row in ctx.db.active_giveaways()
        if int(row["guild_id"]) == ctx.guild_id
    ]
    if len(rows) == 1:
        return int(rows[0]["id"])
    return None


def _giveaway_embed(
    *,
    prize: str,
    host: str,
    ends_at,
    winners: int,
    entries: int,
    role=None,
    level=None,
    ended: bool = False,
    winners_text: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title="🎉 Giveaway" + (" — ended" if ended else ""),
        description=f"### {truncate(prize, 400)}",
        colour=discord.Colour.gold() if not ended else discord.Colour.dark_grey(),
    )
    embed.add_field(name="Host", value=host, inline=True)
    embed.add_field(name="Winners", value=str(winners), inline=True)
    embed.add_field(name="Entries", value=str(entries), inline=True)
    if not ended and ends_at is not None:
        embed.add_field(name="Ends", value=f"{format_dt(ends_at)} ({format_dt(ends_at, 'f')})", inline=False)
    if role is not None:
        embed.add_field(name="Requirement", value=role.mention, inline=True)
    if level:
        embed.add_field(name="Level requirement", value=str(level), inline=True)
    if winners_text:
        embed.add_field(name="Result", value=winners_text, inline=False)
    embed.set_footer(text="Click the button to enter.")
    return embed


async def finish_giveaway(bot, giveaway_id: int) -> list[int] | None:
    row = bot.db.giveaway(giveaway_id)
    if row is None:
        return None
    if row["ended"]:
        return bot.db.giveaway_entries(giveaway_id)
    winners = bot.db.pick_giveaway_winners(giveaway_id, int(row["winners"]))
    bot.db.end_giveaway(giveaway_id)
    mentions = ", ".join(f"<@{user_id}>" for user_id in winners) or "No valid entries."
    channel = bot.get_channel(int(row["channel_id"]))
    message = None
    if channel is not None and row["message_id"]:
        try:
            message = await channel.fetch_message(int(row["message_id"]))
        except (discord.HTTPException, AttributeError):
            message = None
    if message is not None:
        embed = message.embeds[0] if message.embeds else discord.Embed(title="🎉 Giveaway")
        embed.colour = discord.Colour.dark_grey()
        embed.title = "🎉 Giveaway — ended"
        embed.add_field(name="Result", value=mentions, inline=False)
        try:
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass
    if channel is not None:
        try:
            await channel.send(
                f"🎉 Giveaway for **{truncate(row['prize'], 200)}** ended. Winner(s): {mentions}",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException:
            pass
    return winners


async def _update_giveaway_message(bot, row, *, ended: bool, winners_text: str) -> None:
    channel = bot.get_channel(int(row["channel_id"]))
    if channel is None or not row["message_id"]:
        return
    try:
        message = await channel.fetch_message(int(row["message_id"]))
        embed = message.embeds[0] if message.embeds else discord.Embed(title="🎉 Giveaway")
        embed.title = "🎉 Giveaway — ended"
        embed.colour = discord.Colour.dark_grey()
        embed.add_field(name="Result", value=winners_text, inline=False)
        await message.edit(embed=embed, view=None)
    except discord.HTTPException:
        pass


class GiveawayView(discord.ui.View):
    def __init__(self, bot, giveaway_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.giveaway_id = giveaway_id
        # Persistent view: give the button a stable, instance-specific ID so it
        # keeps working after a restart.
        self.enter.custom_id = f"jarvis:giveaway:{giveaway_id}:enter"

    @discord.ui.button(label="Enter", emoji="🎉", style=discord.ButtonStyle.success)
    async def enter(self, interaction: discord.Interaction, _button: discord.ui.Button):
        row = self.bot.db.giveaway(self.giveaway_id)
        if row is None or row["ended"]:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return
        member = interaction.user
        role_id = row["required_role_id"]
        if role_id and not any(role.id == int(role_id) for role in getattr(member, "roles", [])):
            await interaction.response.send_message(
                f"You need <@&{role_id}> to enter.", ephemeral=True
            )
            return
        level = row["required_level"]
        if level:
            data = self.bot.db.level(interaction.guild_id, member.id)
            if self.bot.db.level_from_xp(data["xp"]) < int(level):
                await interaction.response.send_message(
                    f"You need level **{level}** to enter.", ephemeral=True
                )
                return
        added = self.bot.db.enter_giveaway(self.giveaway_id, member.id)
        await interaction.response.send_message(
            "🎉 You're entered!" if added else "You're already entered.",
            ephemeral=True,
        )
        count = self.bot.db.giveaway_entry_count(self.giveaway_id)
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            for index, field in enumerate(embed.fields):
                if field.name == "Entries":
                    embed.set_field_at(index, name="Entries", value=str(count), inline=True)
                    break
            try:
                await interaction.message.edit(embed=embed)
            except discord.HTTPException:
                pass


__all__ = ["manage_giveaway", "finish_giveaway", "GiveawayView"]
