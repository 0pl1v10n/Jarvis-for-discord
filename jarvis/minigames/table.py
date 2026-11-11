"""Two-player-style table games against Jarvis."""

from __future__ import annotations

import random

import discord

from .base import GameView

# ---------------------------------------------------------------------------
# Tic-tac-toe
# ---------------------------------------------------------------------------

WIN_LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
)


def _winner(board: list[str]) -> str | None:
    for a, b, c in WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


def _full(board: list[str]) -> bool:
    return all(board)


def _minimax(board: list[str], player: str) -> tuple[int, int | None]:
    winner = _winner(board)
    if winner == "O":
        return 1, None
    if winner == "X":
        return -1, None
    if _full(board):
        return 0, None
    best_score = -2 if player == "O" else 2
    best_move = None
    for index in range(9):
        if board[index]:
            continue
        board[index] = player
        score, _ = _minimax(board, "X" if player == "O" else "O")
        board[index] = ""
        if (player == "O" and score > best_score) or (player == "X" and score < best_score):
            best_score = score
            best_move = index
    return best_score, best_move


class TicTacToeView(GameView):
    game_name = "tic-tac-toe"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx, timeout=240)
        self.board = [""] * 9
        self.busy = False
        for index in range(9):
            button = discord.ui.Button(
                label="·", style=discord.ButtonStyle.secondary, row=index // 3
            )
            button.callback = self._make_callback(index)
            self.add_item(button)
        self._render()

    def _render(self) -> None:
        for index, value in enumerate(self.board):
            button = self.children[index]
            button.label = value or "·"
            button.style = (
                discord.ButtonStyle.success
                if value == "X"
                else discord.ButtonStyle.danger
                if value == "O"
                else discord.ButtonStyle.secondary
            )
        self.embed.description = "You are **X**, I am **O**. Your move, sir."

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            if self.busy or self.finished or self.board[index]:
                await interaction.response.send_message("That square is unavailable, sir.", ephemeral=True)
                return
            self.busy = True
            self.board[index] = "X"
            await interaction.response.defer()
            await self._after_player()
            self.busy = False

        return callback

    async def _after_player(self) -> None:
        self._render()
        if _winner(self.board) == "X":
            self.lock()
            await self.conclude("win", "Three in a row — you win, sir.")
            return
        if _full(self.board):
            self.lock()
            await self.conclude("draw", "A draw. Well matched.")
            return
        _, move = _minimax(self.board, "O")
        if move is not None:
            self.board[move] = "O"
        self._render()
        if _winner(self.board) == "O":
            self.lock()
            await self.conclude("loss", "I have three in a row. Better luck next time.")
            return
        if _full(self.board):
            self.lock()
            await self.conclude("draw", "A draw. Well matched.")
            return
        await self.refresh()


# ---------------------------------------------------------------------------
# Connect Four
# ---------------------------------------------------------------------------

CONNECT_ROWS = 6
CONNECT_COLS = 7


def _connect_winner(board: list[list[str]]) -> str | None:
    for row in range(CONNECT_ROWS):
        for col in range(CONNECT_COLS):
            player = board[row][col]
            if not player:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                cells = []
                for step in range(4):
                    r = row + dr * step
                    c = col + dc * step
                    if 0 <= r < CONNECT_ROWS and 0 <= c < CONNECT_COLS:
                        cells.append(board[r][c])
                    else:
                        break
                if len(cells) == 4 and all(cell == player for cell in cells):
                    return player
    return None


class ConnectFourView(GameView):
    game_name = "connect four"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx, timeout=300)
        self.board = [["" for _ in range(CONNECT_COLS)] for _ in range(CONNECT_ROWS)]
        self.busy = False
        for col in range(CONNECT_COLS):
            button = discord.ui.Button(label=str(col + 1), style=discord.ButtonStyle.secondary)
            button.callback = self._make_callback(col)
            self.add_item(button)
        self._render()

    def _render(self) -> None:
        rows = []
        for row in self.board:
            rows.append("".join("🔴" if v == "R" else "🟡" if v == "Y" else "⚫" for v in row))
        self.embed.description = "\n".join(rows) + "\n\nYou are 🔴, I am 🟡. Pick a column."

    def _drop(self, col: int, player: str) -> int | None:
        for row in range(CONNECT_ROWS - 1, -1, -1):
            if not self.board[row][col]:
                self.board[row][col] = player
                return row
        return None

    def _make_callback(self, col: int):
        async def callback(interaction: discord.Interaction):
            if self.busy or self.finished:
                await interaction.response.send_message("Please wait, sir.", ephemeral=True)
                return
            if self.board[0][col] and self.board[0][col] != "":
                await interaction.response.send_message("That column is full, sir.", ephemeral=True)
                return
            self.busy = True
            self._drop(col, "R")
            await interaction.response.defer()
            await self._after_player()
            self.busy = False

        return callback

    async def _after_player(self) -> None:
        self._render()
        if _connect_winner(self.board) == "R":
            self.lock()
            await self.conclude("win", "Four in a row — you win!")
            return
        if all(self.board[0][col] for col in range(CONNECT_COLS)):
            self.lock()
            await self.conclude("draw", "The board is full. A draw.")
            return
        move = self._bot_move()
        if move is not None:
            self._drop(move, "Y")
        self._render()
        if _connect_winner(self.board) == "Y":
            self.lock()
            await self.conclude("loss", "I connected four. Good game, sir.")
            return
        if all(self.board[0][col] for col in range(CONNECT_COLS)):
            self.lock()
            await self.conclude("draw", "The board is full. A draw.")
            return
        await self.refresh()

    def _bot_move(self) -> int | None:
        available = [c for c in range(CONNECT_COLS) if not self.board[0][c]]
        if not available:
            return None
        for player in ("Y", "R"):
            for col in available:
                row = next(r for r in range(CONNECT_ROWS - 1, -1, -1) if not self.board[r][col])
                self.board[row][col] = player
                win = _connect_winner(self.board) == player
                self.board[row][col] = ""
                if win:
                    return col
        return random.choice(available)


# ---------------------------------------------------------------------------
# Blackjack
# ---------------------------------------------------------------------------

SUITS = ["♠️", "♥️", "♦️", "♣️"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def _new_deck() -> list[tuple[str, str]]:
    deck = [(rank, suit) for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck


def _card_text(card: tuple[str, str]) -> str:
    return f"{card[0]}{card[1]}"


def _hand_value(hand: list[tuple[str, str]]) -> int:
    total = 0
    aces = 0
    for rank, _suit in hand:
        if rank == "A":
            aces += 1
            total += 11
        elif rank in {"J", "Q", "K"}:
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


class BlackjackView(GameView):
    game_name = "blackjack"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx, timeout=240)
        self.deck = _new_deck()
        self.player = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]
        self.revealed = False
        self._render()

    def _render(self) -> None:
        dealer_hand = (
            " ".join(_card_text(card) for card in self.dealer)
            if self.revealed
            else f"{_card_text(self.dealer[0])} 🂠"
        )
        dealer_value = _hand_value(self.dealer) if self.revealed else "?"
        self.embed.description = (
            f"**Your hand:** {' '.join(_card_text(c) for c in self.player)} "
            f"= **{_hand_value(self.player)}**\n"
            f"**Dealer:** {dealer_hand} = **{dealer_value}**\n\n"
            "Hit or stand?"
        )

    async def _finish(self, interaction: discord.Interaction) -> None:
        self.revealed = True
        while _hand_value(self.dealer) < 17:
            self.dealer.append(self.deck.pop())
        player = _hand_value(self.player)
        dealer = _hand_value(self.dealer)
        self._render()
        if player > 21:
            result, note = "loss", "You bust. The house thanks you."
        elif dealer > 21:
            result, note = "win", "The dealer busts. You win!"
        elif player > dealer:
            result, note = "win", f"You **{player}** vs dealer **{dealer}**. You win!"
        elif player == dealer:
            result, note = "draw", f"Both on **{player}**. Push."
        else:
            result, note = "loss", f"You **{player}** vs dealer **{dealer}**. The house wins."
        self.lock()
        await interaction.response.defer()
        await self.conclude(result, note)

    @discord.ui.button(label="Hit", emoji="🃏", style=discord.ButtonStyle.primary)
    async def hit(self, interaction, _button):
        if self.finished:
            return
        self.player.append(self.deck.pop())
        if _hand_value(self.player) > 21:
            self.revealed = True
            self._render()
            self.lock()
            await interaction.response.defer()
            await self.conclude("loss", f"You drew to **{_hand_value(self.player)}** and bust.")
            return
        self._render()
        await interaction.response.defer()
        await self.refresh()

    @discord.ui.button(label="Stand", emoji="✋", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction, _button):
        if self.finished:
            return
        await self._finish(interaction)


# ---------------------------------------------------------------------------
# Higher or lower
# ---------------------------------------------------------------------------


class HigherLowerView(GameView):
    game_name = "higher or lower"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx, timeout=240)
        self.rounds = 5
        self.played = 0
        self.score = 0
        self.deck = _new_deck()
        self.current = self.deck.pop()
        self._render()

    def _value(self, card) -> int:
        rank = card[0]
        if rank == "A":
            return 14
        if rank in {"J", "Q", "K"}:
            return {"J": 11, "Q": 12, "K": 13}[rank]
        return int(rank)

    def _render(self) -> None:
        self.embed.description = (
            f"**Current card:** {_card_text(self.current)}\n\n"
            f"Guess whether the next card is higher or lower.\n"
            f"Round **{self.played + 1}/{self.rounds}** · Score **{self.score}**"
        )

    async def _guess(self, interaction: discord.Interaction, higher: bool) -> None:
        if self.finished:
            return
        nxt = self.deck.pop()
        current_value = self._value(self.current)
        next_value = self._value(nxt)
        if next_value == current_value:
            outcome = "Tie — no point."
        elif (next_value > current_value) == higher:
            self.score += 1
            outcome = "Correct!"
        else:
            outcome = "Wrong."
        self.played += 1
        self.current = nxt
        await interaction.response.defer()
        if self.played >= self.rounds:
            result = "win" if self.score >= 3 else "draw" if self.score == 2 else "loss"
            self.lock()
            await self.conclude(
                result,
                f"The next card was **{_card_text(nxt)}** — {outcome}\n\n"
                f"Final score: **{self.score}/{self.rounds}**.",
            )
            return
        self._render()
        self.embed.description += f"\n\nLast: **{_card_text(nxt)}** — {outcome}"
        await self.refresh()

    @discord.ui.button(label="Higher", emoji="⬆️", style=discord.ButtonStyle.success)
    async def higher(self, interaction, _button):
        await self._guess(interaction, True)

    @discord.ui.button(label="Lower", emoji="⬇️", style=discord.ButtonStyle.danger)
    async def lower(self, interaction, _button):
        await self._guess(interaction, False)


__all__ = [
    "TicTacToeView",
    "ConnectFourView",
    "BlackjackView",
    "HigherLowerView",
]
