"""Economy: balances, daily rewards, work, transfers, gambling, and admin tools."""

from __future__ import annotations

import random
import time

import discord

from ..ai.tools import Risk, action, tool
from ..util import format_number, human_delta, plural
from .helpers import fail, int_between, member_or_error, sent

ACTIONS = {
    "balance": action(Risk.SAFE),
    "daily": action(Risk.LOW, cooldown=5),
    "work": action(Risk.LOW, cooldown=5),
    "pay": action(Risk.LOW, cooldown=3),
    "leaderboard": action(Risk.SAFE),
    "gamble": action(Risk.LOW, cooldown=3),
    "set": action(Risk.HIGH, permission="manage_guild", confirm=True),
    "add": action(Risk.MEDIUM, permission="manage_guild"),
    "reset": action(Risk.HIGH, permission="manage_guild", confirm=True),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "user": {"type": "string"},
        "amount": {"type": "integer"},
        "limit": {"type": "integer"},
    },
    "required": ["action"],
}

DAILY_MIN, DAILY_MAX = 100, 500
WORK_MIN, WORK_MAX = 20, 120
DAILY_COOLDOWN = 24 * 3600
WORK_COOLDOWN = 3600


@tool(
    "manage_economy",
    "Show balances, claim daily/work rewards, transfer coins, gamble, view the leaderboard, "
    "or adjust balances as an administrator.",
    SCHEMA,
    category="community",
    actions=ACTIONS,
)
async def manage_economy(ctx, args):
    operation = str(args["action"]).lower()
    guild_id = ctx.guild_id
    now = time.time()

    if operation in {"balance", "leaderboard"}:
        if operation == "leaderboard":
            limit = int_between(args.get("limit"), 1, 50, 10)
            rows = ctx.db.economy_leaderboard(guild_id, limit)
            if not rows:
                await ctx.say("The economy is empty, sir.")
                return sent("Economy empty.")
            lines = []
            medals = ["🥇", "🥈", "🥉"]
            for index, row in enumerate(rows):
                marker = medals[index] if index < 3 else f"`#{index + 1}`"
                lines.append(
                    f"{marker} <@{row['user_id']}> — "
                    f"**{format_number(int(row['balance']))}** coins"
                )
            await ctx.say("\n".join(lines))
            return sent("Showed the economy leaderboard.")
        if args.get("user"):
            target, error = await member_or_error(ctx, args.get("user"))
            if error:
                return error
        else:
            target = ctx.member or ctx.author
        data = ctx.db.economy(guild_id, target.id)
        embed = discord.Embed(
            title=f"💰 {target.display_name}",
            description=f"**{format_number(data['balance'])}** coins",
            colour=discord.Colour.gold(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await ctx.say(embed=embed)
        return sent("Showed balance.")

    if operation == "daily":
        data = ctx.db.economy(guild_id, ctx.author_id)
        remaining = DAILY_COOLDOWN - (now - data["last_daily"])
        if remaining > 0:
            return fail(f"Your daily reward is ready in **{human_delta(__import__('datetime').timedelta(seconds=remaining))}**, sir.")
        reward = random.randint(DAILY_MIN, DAILY_MAX)
        ctx.db.set_economy(guild_id, ctx.author_id, balance=data["balance"] + reward, last_daily=now)
        await ctx.say(f"💰 You claimed your daily **{format_number(reward)}** coins.")
        return sent("Daily claimed.")

    if operation == "work":
        data = ctx.db.economy(guild_id, ctx.author_id)
        remaining = WORK_COOLDOWN - (now - data["last_work"])
        if remaining > 0:
            return fail(f"You can work again in **{human_delta(__import__('datetime').timedelta(seconds=remaining))}**, sir.")
        reward = random.randint(WORK_MIN, WORK_MAX)
        jobs = [
            "polished the server trophy cabinet",
            "reorganised the moderation queue",
            "audited the audit log",
            "answered tickets with distinction",
            "calibrated the coffee machine",
            "filed the paperwork in triplicate",
        ]
        ctx.db.set_economy(guild_id, ctx.author_id, balance=data["balance"] + reward, last_work=now)
        await ctx.say(f"💼 You {random.choice(jobs)} and earned **{format_number(reward)}** coins.")
        return sent("Work complete.")

    if operation == "pay":
        target, error = await member_or_error(ctx, args.get("user"))
        if error:
            return error
        if target.id == ctx.author_id:
            return fail("You can't pay yourself, sir.")
        amount = int_between(args.get("amount"), 1, 1_000_000_000, 0)
        if not amount:
            return fail("Tell me how much to transfer, sir.")
        sender = ctx.db.economy(guild_id, ctx.author_id)
        if sender["balance"] < amount:
            return fail(f"You only have **{format_number(sender['balance'])}** coins, sir.")
        ctx.db.add_balance(guild_id, ctx.author_id, -amount)
        ctx.db.add_balance(guild_id, target.id, amount)
        await ctx.say(f"💸 Sent **{format_number(amount)}** coins to {target.mention}.")
        return sent("Transfer complete.")

    if operation == "gamble":
        amount = int_between(args.get("amount"), 1, 1_000_000_000, 0)
        if not amount:
            return fail("Tell me how much to wager, sir.")
        data = ctx.db.economy(guild_id, ctx.author_id)
        if data["balance"] < amount:
            return fail(f"You only have **{format_number(data['balance'])}** coins, sir.")
        roll = random.random()
        if roll < 0.45:
            ctx.db.add_balance(guild_id, ctx.author_id, amount)
            await ctx.say(
                f"🎲 **Win!** You wagered {format_number(amount)} and doubled it. "
                f"Balance: **{format_number(ctx.db.economy(guild_id, ctx.author_id)['balance'])}**."
            )
            return sent("Gambling win.")
        if roll < 0.55:
            await ctx.say("🎲 **Draw.** Your stake is returned.")
            return sent("Gambling draw.")
        ctx.db.add_balance(guild_id, ctx.author_id, -amount)
        await ctx.say(
            f"🎲 **Loss.** You lost {format_number(amount)} coins. "
            f"Balance: **{format_number(ctx.db.economy(guild_id, ctx.author_id)['balance'])}**."
        )
        return sent("Gambling loss.")

    if operation in {"set", "add", "reset"}:
        target, error = await member_or_error(ctx, args.get("user"))
        if error:
            return error
        if operation == "reset":
            ctx.db.set_economy(guild_id, target.id, balance=0)
            await ctx.say(f"🧹 Reset {target.mention}'s balance.")
            return sent("Balance reset.")
        amount = int_between(args.get("amount"), 0, 1_000_000_000, 0)
        if amount <= 0:
            return fail("Tell me a positive coin amount, sir.")
        if operation == "set":
            ctx.db.set_economy(guild_id, target.id, balance=amount)
        else:
            ctx.db.add_balance(guild_id, target.id, amount)
        await ctx.say(
            f"💰 {target.mention} now has **{format_number(ctx.db.economy(guild_id, target.id)['balance'])}** coins."
        )
        return sent("Balance updated.")

    return fail(f"I don't know how to `{operation}` the economy, sir.")
