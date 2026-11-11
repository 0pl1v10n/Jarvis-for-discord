"""Levelling: XP, ranks, leaderboards, and role rewards."""

from __future__ import annotations

import discord

from ..ai.tools import Risk, action, tool
from ..util import format_number, progress_bar, truncate
from .helpers import fail, int_between, member_or_error, role_hierarchy_error, role_or_error, sent

ACTIONS = {
    "rank": action(Risk.SAFE),
    "leaderboard": action(Risk.SAFE),
    "config": action(Risk.MEDIUM, permission="manage_guild"),
    "set_xp": action(Risk.HIGH, permission="manage_guild", confirm=True),
    "add_xp": action(Risk.MEDIUM, permission="manage_guild"),
    "reset": action(Risk.HIGH, permission="manage_guild", confirm=True),
    "add_reward": action(Risk.MEDIUM, permission="manage_roles"),
    "remove_reward": action(Risk.MEDIUM, permission="manage_roles"),
    "rewards": action(Risk.SAFE),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "user": {"type": "string"},
        "amount": {"type": "integer", "description": "XP amount"},
        "level": {"type": "integer", "description": "Level for a role reward"},
        "role": {"type": "string"},
        "channel": {"type": "string", "description": "Level-up announcement channel"},
        "message": {"type": "string", "description": "Level-up message template"},
        "enabled": {"type": "boolean"},
        "limit": {"type": "integer"},
    },
    "required": ["action"],
}


@tool(
    "manage_leveling",
    "Show ranks and leaderboards, grant/reset XP, configure announcements, and manage role rewards.",
    SCHEMA,
    category="community",
    actions=ACTIONS,
)
async def manage_leveling(ctx, args):
    operation = str(args["action"]).lower()
    guild_id = ctx.guild_id
    config = ctx.db.get_config(guild_id, "leveling", {}) or {}

    if operation == "rank":
        if args.get("user"):
            member, error = await member_or_error(ctx, args.get("user"))
            if error:
                return error
        else:
            member = ctx.member or ctx.author
        data = ctx.db.level(guild_id, member.id)
        level = ctx.db.level_from_xp(data["xp"])
        current_floor = ctx.db.xp_for_level(level)
        next_goal = ctx.db.xp_for_level(level + 1)
        embed = discord.Embed(
            title=f"📈 {member.display_name}",
            colour=member.colour or discord.Colour.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="XP", value=format_number(data["xp"]), inline=True)
        embed.add_field(name="Messages", value=format_number(data["messages"]), inline=True)
        embed.add_field(
            name="Progress",
            value=f"{progress_bar(data['xp'] - current_floor, max(1, next_goal - current_floor))} "
            f"{format_number(max(0, next_goal - data['xp']))} XP to level {level + 1}",
            inline=False,
        )
        rank = next(
            (
                index
                for index, row in enumerate(ctx.db.level_leaderboard(guild_id, 1000), start=1)
                if int(row["user_id"]) == member.id
            ),
            None,
        )
        if rank:
            embed.set_footer(text=f"Server rank #{rank}")
        await ctx.say(embed=embed)
        return sent("Showed rank.")

    if operation == "leaderboard":
        limit = int_between(args.get("limit"), 1, 50, 10)
        rows = ctx.db.level_leaderboard(guild_id, limit)
        if not rows:
            await ctx.say("Nobody has earned XP yet, sir.")
            return sent("Leaderboard empty.")
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for index, row in enumerate(rows):
            medal = medals[index] if index < 3 else f"`#{index + 1}`"
            level = ctx.db.level_from_xp(int(row["xp"]))
            lines.append(
                f"{medal} <@{row['user_id']}> — level **{level}**, "
                f"{format_number(int(row['xp']))} XP"
            )
        await ctx.say("\n".join(lines))
        return sent("Showed the leaderboard.")

    if operation == "config":
        if "enabled" in args:
            config["enabled"] = bool(args["enabled"])
        if args.get("channel"):
            channel = ctx.resolve_channel(args.get("channel"))
            if channel is None:
                return fail("I couldn't find that channel, sir.")
            config["channel_id"] = channel.id
        if args.get("message"):
            config["message"] = truncate(args["message"], 1000)
        config.setdefault("enabled", True)
        ctx.db.set_config(guild_id, "leveling", config)
        channel = ctx.guild.get_channel(int(config.get("channel_id") or 0))
        await ctx.say(
            f"📈 Levelling is **{'enabled' if config.get('enabled') else 'disabled'}**"
            + (f", announcements in {channel.mention}" if channel else "")
        )
        return sent("Levelling configured.")

    if operation in {"set_xp", "add_xp", "reset"}:
        if args.get("user"):
            target, error = await member_or_error(ctx, args.get("user"))
            if error:
                return error
        else:
            target = ctx.member or ctx.author
        if operation == "reset":
            ctx.db.reset_level(guild_id, target.id)
            await ctx.say(f"🧹 Reset the level data for {target.mention}.")
            return sent("XP reset.")
        amount = int_between(args.get("amount"), 0, 100_000_000, 0)
        if operation == "set_xp":
            ctx.db.set_xp(guild_id, target.id, amount)
            await ctx.say(f"📈 Set {target.mention}'s XP to **{format_number(amount)}**.")
            return sent("XP set.")
        data = ctx.db.level(guild_id, target.id)
        ctx.db.set_xp(guild_id, target.id, data["xp"] + amount)
        await ctx.say(f"📈 Gave {target.mention} **{format_number(amount)}** XP.")
        return sent("XP granted.")

    if operation in {"add_reward", "remove_reward"}:
        level = int_between(args.get("level"), 1, 1000, 0)
        if not level:
            return fail("Tell me which level the reward is for, sir.")
        rewards = config.get("rewards", {})
        if operation == "remove_reward":
            rewards.pop(str(level), None)
            config["rewards"] = rewards
            ctx.db.set_config(guild_id, "leveling", config)
            await ctx.say(f"➖ Removed the level **{level}** reward.")
            return sent("Reward removed.")
        role, error = role_or_error(ctx, args.get("role"))
        if error:
            return error
        problem = role_hierarchy_error(ctx, role)
        if problem:
            return problem
        rewards[str(level)] = role.id
        config["rewards"] = rewards
        ctx.db.set_config(guild_id, "leveling", config)
        await ctx.say(f"🎁 Reaching level **{level}** now grants **{role.name}**.")
        return sent("Reward added.")

    if operation == "rewards":
        rewards = config.get("rewards", {})
        if not rewards:
            await ctx.say("There are no level rewards, sir.")
            return sent("No rewards.")
        lines = [
            f"🎁 Level **{level}** → <@&{role_id}>"
            for level, role_id in sorted(rewards.items(), key=lambda item: int(item[0]))
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(rewards)} rewards.")

    return fail(f"I don't know how to `{operation}` levels, sir.")
