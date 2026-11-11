"""Minigame registry and launcher."""

from __future__ import annotations

import random
from typing import Callable

from .simple import (
    CoinflipView,
    DiceView,
    EightBallView,
    HangmanView,
    MemoryView,
    ReactionView,
    RPSView,
    SlotsView,
    TriviaView,
    WordleView,
)
from .table import (
    BlackjackView,
    ConnectFourView,
    HigherLowerView,
    TicTacToeView,
)

GAMES: dict[str, tuple[str, Callable]] = {
    "rps": ("Rock Paper Scissors", RPSView),
    "rockpaperscissors": ("Rock Paper Scissors", RPSView),
    "coinflip": ("Coin Flip", CoinflipView),
    "8ball": ("Magic 8-Ball", EightBallView),
    "eightball": ("Magic 8-Ball", EightBallView),
    "dice": ("Dice", DiceView),
    "slots": ("Slots", SlotsView),
    "trivia": ("Trivia", TriviaView),
    "hangman": ("Hangman", HangmanView),
    "wordle": ("Wordle", WordleView),
    "tictactoe": ("Tic-Tac-Toe", TicTacToeView),
    "ttt": ("Tic-Tac-Toe", TicTacToeView),
    "connect4": ("Connect Four", ConnectFourView),
    "connectfour": ("Connect Four", ConnectFourView),
    "blackjack": ("Blackjack", BlackjackView),
    "21": ("Blackjack", BlackjackView),
    "higherlower": ("Higher or Lower", HigherLowerView),
    "higher": ("Higher or Lower", HigherLowerView),
    "memory": ("Memory", MemoryView),
    "reaction": ("Reaction Test", ReactionView),
    "reactiontest": ("Reaction Test", ReactionView),
}

GAME_LIST = [
    ("🪨", "rps", "Rock Paper Scissors"),
    ("🪙", "coinflip", "Coin Flip"),
    ("🎱", "8ball", "Magic 8-Ball"),
    ("🎲", "dice", "Dice"),
    ("🎰", "slots", "Slots"),
    ("❓", "trivia", "Trivia"),
    ("🪢", "hangman", "Hangman"),
    ("🟩", "wordle", "Wordle"),
    ("⭕", "tictactoe", "Tic-Tac-Toe"),
    ("🔴", "connect4", "Connect Four"),
    ("🃏", "blackjack", "Blackjack"),
    ("⬆️", "higherlower", "Higher or Lower"),
    ("🧠", "memory", "Memory"),
    ("⚡", "reaction", "Reaction Test"),
]


def resolve_game(value: str | None) -> str:
    key = str(value or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    if not key or key in {"random", "any", "surprise"}:
        return random.choice([game for _emoji, game, _name in GAME_LIST])
    aliases = {
        "rockpaperscissors": "rps",
        "magic8ball": "8ball",
        "eightball": "8ball",
        "tic-tac-toe": "tictactoe",
        "connectfour": "connect4",
        "higherorlower": "higherlower",
        "reactiontest": "reaction",
        "twentyone": "blackjack",
    }
    key = aliases.get(key, key)
    return key if key in GAMES else key


async def launch(ctx, game: str | None = None, *, question: str | None = None, sides: int = 6):
    key = resolve_game(game)
    if key not in GAMES:
        return None
    title, view_class = GAMES[key]
    if view_class is EightBallView:
        view = view_class(ctx.bot, ctx, question)
    elif view_class is DiceView:
        view = view_class(ctx.bot, ctx, sides)
    else:
        view = view_class(ctx.bot, ctx)
    await view.start()
    return title


def game_help() -> str:
    return "\n".join(f"{emoji} **{name}** — `{key}`" for emoji, key, name in GAME_LIST)


def game_count() -> int:
    return len({key for _emoji, key, _name in GAME_LIST})


__all__ = ["GAMES", "GAME_LIST", "resolve_game", "launch", "game_help", "game_count"]
