"""Slash commands.

Slash commands are deliberately thin: they build the same :class:`ActionContext`
the chat agent uses, then either call the agent directly (``/ask``) or render a
small, focused view of data the bot already knows about.  Everything else is
handled through natural language, which is the primary interface.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from . import triggers
from .ai.tools import REGISTRY
from .context import ActionContext
from .minigames import GAME_LIST, game_count, game_help, launch, resolve_game
from .util import format_dt, human_delta, split_message, truncate, utcnow

log = logging.getLogger(__name__)

GAME_CHOICES = [
    app_commands.Choice(name=name, value=key)
    for _emoji, key, name in GAME_LIST
]


def _ctx(bot, interaction: discord.Interaction) -> ActionContext:
    """Build an ActionContext for a slash interaction."""
    author = interaction.user
    member = author if isinstance(author, discord.Member) else None
    return ActionContext(
        bot=bot,
        message=interaction,
        command="",
        guild=interaction.guild,
        channel=interaction.channel,
        author=author,
        member=member,
    )


def _status_embed(bot) -> discord.Embed:
    embed = discord.Embed(
        title="🤵 Jarvis — status",
        colour=discord.Colour.blurple(),
        timestamp=utcnow(),
    )
    started = getattr(bot, "started_at", utcnow())
    embed.add_field(name="Uptime", value=human_delta(utcnow() - started), inline=True)
    embed.add_field(name="Latency", value=f"{bot.latency * 1000:.0f} ms", inline=True)
    embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)

    router = getattr(bot, "router", None)
    if router is None or not router.targets:
        embed.add_field(name="AI", value="No models configured", inline=False)
        return embed

    current = router.current()
    embed.add_field(
        name="Current model",
        value=f"`{current.label}`" if current else "none",
        inline=False,
    )
    lines = []
    for status in router.status()[:12]:
        if status.disabled:
            icon = "❌"
        elif status.cooling:
            icon = f"⏳ {status.cooldown_seconds}s"
        elif status.current:
            icon = "▶️"
        else:
            icon = "✅"
        error = f" — {truncate(status.last_error, 70)}" if status.last_error else ""
        lines.append(f"{icon} `{status.label}`{error}")
    embed.add_field(
        name=f"Model chain ({len(router.targets)})",
        value=truncate("\n".join(lines) or "—", 1024),
        inline=False,
    )
    return embed


def _help_embed() -> discord.Embed:
    categories = REGISTRY.categories()
    embed = discord.Embed(
        title="🤵 Jarvis — at your service",
        description=(
            f"Call me **{triggers.wake_phrase_count():,}** different ways — “Jarvis”, "
            "“hey jarvis”, “oi jarvis”, a mention, or a reply. Talk naturally; I use tools "
            "rather than a fixed menu.\n\n"
            f"🎮 **{game_count()} minigames** · 🛠️ **{len(REGISTRY.all())} tool groups** · "
            f"**{sum(len(s.action_names) or 1 for s in REGISTRY.all())} actions**"
        ),
        colour=discord.Colour.blurple(),
    )
    for category, specs in sorted(categories.items()):
        embed.add_field(
            name=category.title(),
            value=truncate(", ".join(f"`{spec.name}`" for spec in specs), 1024),
            inline=False,
        )
    embed.add_field(
        name="Slash commands",
        value="`/ask` · `/help` · `/status` · `/game` · `/model` · `/ping` · `/phrases`",
        inline=False,
    )
    embed.set_footer(text="Ask in plain language — moderation, setup, chat, games, and more.")
    return embed


async def setup_commands(bot) -> None:
    """Register all slash commands on the bot's command tree."""
    existing = {command.name for command in bot.tree.get_commands()}
    if "ask" in existing:
        return

    @bot.tree.command(name="ask", description="Ask Jarvis anything in natural language.")
    @app_commands.describe(prompt="What you want Jarvis to do or answer.")
    async def ask(interaction: discord.Interaction, prompt: str) -> None:
        await interaction.response.defer(thinking=True)
        ctx = _ctx(bot, interaction)
        try:
            answer = await bot.agent.respond(ctx, prompt) if bot.agent else None
        except Exception:
            log.exception("Slash /ask failed")
            answer = None
        if not answer:
            answer = "I couldn't answer that, sir. Every configured model may be unavailable."
        for index, chunk in enumerate(split_message(answer)):
            try:
                if index == 0:
                    await interaction.followup.send(
                        chunk,
                        allowed_mentions=discord.AllowedMentions(
                            everyone=False, roles=False, users=True, replied_user=False
                        ),
                    )
                else:
                    await interaction.channel.send(
                        chunk, allowed_mentions=discord.AllowedMentions.none()
                    )
            except discord.HTTPException:
                break

    @bot.tree.command(name="help", description="Show everything Jarvis can do.")
    async def help_command(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_help_embed(), ephemeral=True)

    @bot.tree.command(name="status", description="Show Jarvis and AI model health.")
    async def status(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_status_embed(bot), ephemeral=True)

    @bot.tree.command(name="ping", description="Check Jarvis latency.")
    async def ping(interaction: discord.Interaction) -> None:
        started = utcnow()
        await interaction.response.defer(thinking=True)
        elapsed = (utcnow() - started).total_seconds() * 1000
        await interaction.followup.send(
            f"🏓 **Pong!** Gateway: {bot.latency * 1000:.0f} ms · Round-trip: {elapsed:.0f} ms",
            ephemeral=True,
        )

    @bot.tree.command(name="game", description="Start one of Jarvis's minigames.")
    @app_commands.describe(
        game="Which game to play (leave blank for a random one).",
        question="Question for the Magic 8-Ball.",
        sides="Number of sides for dice (default 6).",
    )
    @app_commands.choices(game=GAME_CHOICES)
    async def game(
        interaction: discord.Interaction,
        game: app_commands.Choice[str] | None = None,
        question: str | None = None,
        sides: int | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        ctx = _ctx(bot, interaction)
        key = game.value if game is not None else resolve_game(None)
        title = await launch(
            ctx,
            key,
            question=question,
            sides=max(2, min(1000, int(sides or 6))),
        )
        if title is None:
            await interaction.followup.send(
                "🎮 I don't have that game, sir. Here's the list:\n" + game_help(),
                ephemeral=True,
            )
            return
        await interaction.followup.send(f"🎮 Started **{title}** for you, sir.", ephemeral=True)

    @bot.tree.command(name="model", description="Show or change the active AI model.")
    @app_commands.describe(
        provider="Provider name, e.g. gemini, openrouter, groq, ollama.",
        model="Exact model ID to force for this and future requests.",
    )
    async def model(
        interaction: discord.Interaction,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        router = bot.router
        if router is None or not router.targets:
            await interaction.response.send_message(
                "No AI models are configured, sir.", ephemeral=True
            )
            return

        if provider is None and model is None:
            await interaction.response.send_message(embed=_status_embed(bot), ephemeral=True)
            return

        ctx = _ctx(bot, interaction)
        if not (ctx.is_owner or ctx.can("manage_guild")):
            await interaction.response.send_message(
                "Only a server manager or the owner can switch models, sir.", ephemeral=True
            )
            return

        if not provider or not model:
            await interaction.response.send_message(
                "Give me both a provider and a model, e.g. `/model gemini gemini-2.5-flash`.",
                ephemeral=True,
            )
            return

        try:
            target = router.force(provider.strip().lower(), model.strip())
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"🧠 Switched to `{target.label}`, sir. It will be used from now on.",
            ephemeral=True,
        )

    @bot.tree.command(name="phrases", description="Show how Jarvis can be woken and the minigames.")
    async def phrases(interaction: discord.Interaction) -> None:
        examples = triggers.wake_phrase_examples(limit=30)
        embed = discord.Embed(
            title="🗣️ Wake phrases",
            description=(
                f"I answer to **{triggers.wake_phrase_count():,}** phrases, plus mentions and "
                f"replies. Here are some:\n\n" + " · ".join(f"“{p}”" for p in examples)
            ),
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name=f"🎮 Minigames ({game_count()})",
            value=truncate(game_help(), 1024),
            inline=False,
        )
        embed.set_footer(text="You can also just reply to one of my messages.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    log.info("Registered %d slash commands", len(bot.tree.get_commands()))


__all__ = ["setup_commands"]
