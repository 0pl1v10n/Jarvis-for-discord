"""Minigames as AI-callable tools."""

from __future__ import annotations

import discord

from ..ai.tools import Risk, action, tool
from ..minigames import game_count, game_help, launch
from ..util import format_number
from .helpers import fail, int_between, sent

PLAY_SCHEMA = {
    "type": "object",
    "properties": {
        "game": {
            "type": "string",
            "description": "Game name: rps, coinflip, 8ball, dice, slots, trivia, hangman, wordle, "
            "tictactoe, connect4, blackjack, higherlower, memory, reaction, or random",
        },
        "question": {"type": "string", "description": "Question for the 8-ball"},
        "sides": {"type": "integer", "description": "Dice sides (default 6)"},
    },
    "required": ["game"],
}


@tool(
    "play_game",
    f"Start one of {game_count()} interactive minigames against Jarvis: "
    + ", ".join(["rps", "coinflip", "8ball", "dice", "slots", "trivia", "hangman", "wordle",
                "tictactoe", "connect4", "blackjack", "higherlower", "memory", "reaction"]),
    PLAY_SCHEMA,
    category="games",
    default_meta=action(Risk.SAFE, cooldown=2),
    requires_guild=True,
)
async def play_game(ctx, args):
    title = await launch(
        ctx,
        args.get("game"),
        question=args.get("question"),
        sides=int(args.get("sides") or 6),
    )
    if title is None:
        await ctx.say("🎮 I don't have that game, sir. Here's what I have:\n" + game_help())
        return fail("Unknown game.")
    return sent(f"Started {title}.")


STATS_ACTIONS = {
    "stats": action(Risk.SAFE),
    "leaderboard": action(Risk.SAFE),
    "list": action(Risk.SAFE),
}

STATS_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(STATS_ACTIONS)},
        "game": {"type": "string", "description": "Game key"},
        "user": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "required": ["action"],
}


@tool(
    "game_stats",
    "Show a member's minigame record, a game leaderboard, or the list of available games.",
    STATS_SCHEMA,
    category="games",
    actions=STATS_ACTIONS,
)
async def game_stats(ctx, args):
    operation = str(args["action"]).lower()

    if operation == "list":
        await ctx.say("🎮 **Minigames**\n" + game_help())
        return sent("Listed games.")

    if operation == "stats":
        if args.get("user"):
            member = await ctx.resolve_member_async(args.get("user"))
            if member is None:
                return fail("I couldn't find that member, sir.")
        else:
            member = ctx.member or ctx.author
        rows = ctx.db.game_stats(ctx.guild_id, member.id)
        if not rows:
            await ctx.say(f"🎮 {member.mention} hasn't played any games yet.")
            return sent("No stats.")
        embed = discord.Embed(
            title=f"🎮 {member.display_name}'s game record",
            colour=member.colour or discord.Colour.blurple(),
        )
        for row in rows[:20]:
            embed.add_field(
                name=row["game"].title(),
                value=f"🏆 {row['wins']} · 💀 {row['losses']} · 🤝 {row['draws']} · "
                f"played {format_number(int(row['plays']))}",
                inline=True,
            )
        await ctx.say(embed=embed)
        return sent("Showed game stats.")

    game = str(args.get("game") or "").strip().lower()
    if not game:
        return fail("Tell me which game's leaderboard, sir.")
    limit = int_between(args.get("limit"), 1, 25, 10)
    rows = ctx.db.game_leaderboard(ctx.guild_id, game, limit)
    if not rows:
        await ctx.say(f"No one has played **{game}** yet, sir.")
        return sent("Empty leaderboard.")
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for index, row in enumerate(rows):
        marker = medals[index] if index < 3 else f"`#{index + 1}`"
        lines.append(
            f"{marker} <@{row['user_id']}> — **{row['wins']}** wins in {row['plays']} plays"
        )
    await ctx.say(f"🏆 **{game.title()} leaderboard**\n" + "\n".join(lines))
    return sent("Showed the leaderboard.")


__all__ = ["play_game", "game_stats"]
