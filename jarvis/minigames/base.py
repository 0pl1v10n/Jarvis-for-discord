"""Shared game infrastructure."""

from __future__ import annotations

import random

import discord

from ..util import truncate

GAME_TIMEOUT = 180


class GameView(discord.ui.View):
    game_name = "game"

    def __init__(self, bot, ctx, *, timeout: float = GAME_TIMEOUT):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.ctx = ctx
        self.message: discord.Message | None = None
        self.finished = False
        self.embed = discord.Embed(
            title=f"🎮 {self.game_name.title()}",
            colour=discord.Colour.blurple(),
            description="Preparing the table…",
        )
        self.embed.set_footer(text=f"Requested by {ctx.display_name}")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author_id:
            await interaction.response.send_message(
                "This isn't your game, sir. Start your own with “Jarvis, play …”.",
                ephemeral=True,
            )
            return False
        return True

    async def start(self) -> None:
        try:
            self.message = await self.ctx.channel.send(embed=self.embed, view=self)
        except discord.HTTPException:
            self.message = None

    async def refresh(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(embed=self.embed, view=self)
        except discord.HTTPException:
            pass

    def lock(self) -> None:
        for child in self.children:
            child.disabled = True

    async def conclude(self, result: str, note: str = "") -> None:
        if self.finished:
            return
        self.finished = True
        self.lock()
        self.stop()
        if result in {"win", "loss", "draw"}:
            try:
                if self.ctx.guild_id:
                    self.bot.db.record_game(self.ctx.guild_id, self.ctx.author_id, self.game_name, result)
            except Exception:
                pass
        icons = {"win": "🏆", "loss": "💀", "draw": "🤝"}
        self.embed.colour = {
            "win": discord.Colour.green(),
            "loss": discord.Colour.red(),
            "draw": discord.Colour.gold(),
        }.get(result, discord.Colour.blurple())
        self.embed.title = f"{icons.get(result, '🎮')} {self.game_name.title()} — {result.title()}"
        if note:
            self.embed.description = truncate(note, 3900)
        await self.refresh()
        self.bot.dispatch("jarvis_game_finished", self)

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.lock()
        self.embed.colour = discord.Colour.dark_grey()
        self.embed.title = f"⌛ {self.game_name.title()} — timed out"
        self.embed.description = "The game was abandoned. Start a new one whenever you like."
        await self.refresh()


def buttons_grid(labels, *, style=discord.ButtonStyle.secondary, rows: int | None = None):
    """Helper that yields button row sizes for label lists."""
    if rows is None:
        return [5] * ((len(labels) + 4) // 5)
    base = len(labels) // rows
    extra = len(labels) % rows
    return [base + (1 if index < extra else 0) for index in range(rows)]


def sample_without_replacement(pool: list, count: int) -> list:
    count = min(count, len(pool))
    return random.sample(pool, count)


class ReplayView(discord.ui.View):
    """Offers a rematch after a game ends."""

    def __init__(self, bot, ctx, launcher, game: str):
        super().__init__(timeout=120)
        self.bot = bot
        self.ctx = ctx
        self.launcher = launcher
        self.game = game

    @discord.ui.button(label="Play again", emoji="🔁", style=discord.ButtonStyle.primary)
    async def again(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.ctx.author_id:
            await interaction.response.send_message("Start your own game, sir.", ephemeral=True)
            return
        await interaction.response.defer()
        await self.launcher(self.ctx, self.game)
