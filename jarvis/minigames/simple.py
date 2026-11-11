"""Quick single-player arcade games."""

from __future__ import annotations

import asyncio
import random

import discord

from .base import GameView

# ---------------------------------------------------------------------------
# Rock, paper, scissors
# ---------------------------------------------------------------------------


class RPSView(GameView):
    game_name = "rock paper scissors"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx)
        self.embed.description = "Choose your weapon, sir. First to reveal wins the round."

    async def _play(self, interaction: discord.Interaction, choice: str) -> None:
        bot_choice = random.choice(["rock", "paper", "scissors"])
        wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
        if choice == bot_choice:
            result = "draw"
            note = f"We both chose **{choice}**."
        elif wins[choice] == bot_choice:
            result = "win"
            note = f"You chose **{choice}**; I chose **{bot_choice}**. You win."
        else:
            result = "loss"
            note = f"You chose **{choice}**; I chose **{bot_choice}**. I win."
        await interaction.response.defer()
        await self.conclude(result, note)

    @discord.ui.button(label="Rock", emoji="🪨", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction, _button):
        await self._play(interaction, "rock")

    @discord.ui.button(label="Paper", emoji="📄", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction, _button):
        await self._play(interaction, "paper")

    @discord.ui.button(label="Scissors", emoji="✂️", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction, _button):
        await self._play(interaction, "scissors")


# ---------------------------------------------------------------------------
# Coin flip
# ---------------------------------------------------------------------------


class CoinflipView(GameView):
    game_name = "coin flip"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx)
        self.embed.description = "Call it in the air, sir. Heads or tails?"

    async def _flip(self, interaction, choice: str) -> None:
        result_side = random.choice(["heads", "tails"])
        result = "win" if choice == result_side else "loss"
        await interaction.response.defer()
        await self.conclude(
            result,
            f"The coin landed on **{result_side}**. You called **{choice}**.",
        )

    @discord.ui.button(label="Heads", emoji="🪙", style=discord.ButtonStyle.primary)
    async def heads(self, interaction, _button):
        await self._flip(interaction, "heads")

    @discord.ui.button(label="Tails", emoji="🪙", style=discord.ButtonStyle.primary)
    async def tails(self, interaction, _button):
        await self._flip(interaction, "tails")


# ---------------------------------------------------------------------------
# Magic 8-ball
# ---------------------------------------------------------------------------

EIGHT_BALL = [
    "It is certain.",
    "Without a doubt.",
    "You may rely on it.",
    "Yes, definitely.",
    "Most likely.",
    "Outlook good.",
    "Signs point to yes.",
    "Reply hazy, try again.",
    "Ask again later.",
    "Better not tell you now.",
    "Cannot predict now.",
    "Concentrate and ask again.",
    "Don't count on it.",
    "My reply is no.",
    "My sources say no.",
    "Outlook not so good.",
    "Very doubtful.",
]


class EightBallView(GameView):
    game_name = "magic 8-ball"

    def __init__(self, bot, ctx, question: str | None = None):
        super().__init__(bot, ctx)
        self.question = question
        self.embed.description = (
            f"**Question:** {question}\n\nAsk the orb if you dare." if question else "Ask a yes/no question, then consult the orb."
        )

    @discord.ui.button(label="Shake the orb", emoji="🎱", style=discord.ButtonStyle.primary)
    async def shake(self, interaction, _button):
        await interaction.response.defer()
        await self.conclude("draw", f"🎱 {random.choice(EIGHT_BALL)}")


# ---------------------------------------------------------------------------
# Dice
# ---------------------------------------------------------------------------


class DiceView(GameView):
    game_name = "dice"

    def __init__(self, bot, ctx, sides: int = 6):
        super().__init__(bot, ctx)
        self.sides = max(2, min(100, sides))
        self.embed.description = (
            f"Roll two **d{sides}**. Doubles win, a total of 7 is a draw, anything else loses."
        )

    @discord.ui.button(label="Roll", emoji="🎲", style=discord.ButtonStyle.primary)
    async def roll(self, interaction, _button):
        a = random.randint(1, self.sides)
        b = random.randint(1, self.sides)
        if a == b:
            result = "win"
        elif a + b == 7:
            result = "draw"
        else:
            result = "loss"
        await interaction.response.defer()
        await self.conclude(result, f"🎲 You rolled **{a}** and **{b}** (total {a + b}).")


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------

SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "⭐", "💎"]


class SlotsView(GameView):
    game_name = "slots"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx)
        self.embed.description = "Three reels. Three of a kind wins, two of a kind is a draw."

    @discord.ui.button(label="Spin", emoji="🎰", style=discord.ButtonStyle.primary)
    async def spin(self, interaction, _button):
        reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        unique = len(set(reels))
        result = "win" if unique == 1 else "draw" if unique == 2 else "loss"
        payout = "Jackpot!" if unique == 1 else "Close." if unique == 2 else "No luck."
        await interaction.response.defer()
        await self.conclude(result, f"🎰 {' | '.join(reels)}\n\n{payout}")


# ---------------------------------------------------------------------------
# Trivia
# ---------------------------------------------------------------------------

TRIVIA = [
    ("Which planet is known as the Red Planet?", ["Mars", "Venus", "Jupiter", "Mercury"], 0),
    ("What is the capital of Japan?", ["Tokyo", "Kyoto", "Osaka", "Seoul"], 0),
    ("How many sides does a hexagon have?", ["6", "5", "7", "8"], 0),
    ("Who wrote “Hamlet”?", ["Shakespeare", "Dickens", "Tolkien", "Austen"], 0),
    ("What is H2O commonly called?", ["Water", "Salt", "Hydrogen", "Oxygen"], 0),
    ("Which is the largest ocean?", ["Pacific", "Atlantic", "Indian", "Arctic"], 0),
    ("What year did the first Moon landing happen?", ["1969", "1959", "1979", "1965"], 0),
    ("Which element has the symbol Au?", ["Gold", "Silver", "Copper", "Aluminium"], 0),
    ("What is the fastest land animal?", ["Cheetah", "Lion", "Horse", "Greyhound"], 0),
    ("How many continents are there?", ["7", "5", "6", "8"], 0),
    ("Which country invented tea?", ["China", "India", "England", "Japan"], 0),
    ("What is the square root of 144?", ["12", "14", "10", "16"], 0),
    ("Which gas do plants absorb?", ["Carbon dioxide", "Oxygen", "Nitrogen", "Helium"], 0),
    ("Who painted the Mona Lisa?", ["Leonardo da Vinci", "Michelangelo", "Raphael", "Van Gogh"], 0),
    ("What is the largest mammal?", ["Blue whale", "Elephant", "Giraffe", "Orca"], 0),
    ("In which sport is a shuttlecock used?", ["Badminton", "Tennis", "Squash", "Cricket"], 0),
    ("What is the currency of Japan?", ["Yen", "Won", "Yuan", "Ringgit"], 0),
    ("Which planet has the most moons?", ["Saturn", "Jupiter", "Mars", "Neptune"], 0),
    ("What does CPU stand for?", ["Central Processing Unit", "Computer Personal Unit", "Central Program Utility", "Core Process Unit"], 0),
    ("How many colours are in a rainbow?", ["7", "5", "6", "8"], 0),
]


class TriviaView(GameView):
    game_name = "trivia"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx)
        question, answers, correct = random.choice(TRIVIA)
        self.correct = correct
        self.answers = answers
        self.embed.description = f"**{question}**"
        order = list(range(len(answers)))
        random.shuffle(order)
        self.order = order
        self.correct_index = order.index(correct)
        for position, index in enumerate(order):
            button = discord.ui.Button(
                label=answers[index][:80], style=discord.ButtonStyle.secondary, row=0
            )
            button.callback = self._make_callback(position)
            self.add_item(button)

    def _make_callback(self, position: int):
        async def callback(interaction: discord.Interaction):
            chosen = self.answers[self.order[position]]
            result = "win" if position == self.correct_index else "loss"
            await interaction.response.defer()
            await self.conclude(
                result,
                f"You answered **{chosen}**. The correct answer was "
                f"**{self.answers[self.correct]}**.",
            )

        return callback


# ---------------------------------------------------------------------------
# Hangman
# ---------------------------------------------------------------------------

HANGMAN_WORDS = [
    ("butler", "A loyal household servant"),
    ("discord", "Where this conversation lives"),
    ("planet", "A body orbiting a star"),
    ("garden", "Somewhere flowers grow"),
    ("volcano", "A mountain that erupts"),
    ("penguin", "A flightless Antarctic bird"),
    ("library", "A home for books"),
    ("mystery", "Something unexplained"),
    ("journey", "A trip from here to there"),
    ("lantern", "A portable light"),
    ("puzzle", "A problem to solve"),
    ("octopus", "An eight-armed sea creature"),
]


class HangmanView(GameView):
    game_name = "hangman"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx, timeout=240)
        self.word, self.hint = random.choice(HANGMAN_WORDS)
        self.guessed: set[str] = set()
        self.wrong = 0
        self.max_wrong = 6
        for start in range(0, 26, 7):
            row = (start // 7) + 1
            for offset in range(7):
                index = start + offset
                if index >= 26:
                    break
                letter = chr(65 + index)
                button = discord.ui.Button(
                    label=letter, style=discord.ButtonStyle.secondary, row=row
                )
                button.callback = self._make_callback(letter.lower())
                self.add_item(button)
        self._render()

    def _render(self) -> None:
        display = " ".join(
            letter.upper() if letter in self.guessed else "_" for letter in self.word
        )
        lives = "❤️" * (self.max_wrong - self.wrong) + "🖤" * self.wrong
        self.embed.description = (
            f"**Hint:** {self.hint}\n\n`{display}`\n\nLives: {lives}\n"
            f"Guessed: {', '.join(sorted(self.guessed)) or '—'}"
        )

    def _make_callback(self, letter: str):
        async def callback(interaction: discord.Interaction):
            if self.finished:
                await interaction.response.defer()
                return
            if letter in self.guessed:
                await interaction.response.send_message("Already guessed, sir.", ephemeral=True)
                return
            self.guessed.add(letter)
            if letter not in self.word:
                self.wrong += 1
            for child in self.children:
                if child.label and child.label.lower() == letter:
                    child.disabled = True
                    child.style = (
                        discord.ButtonStyle.success
                        if letter in self.word
                        else discord.ButtonStyle.danger
                    )
            await interaction.response.defer()
            if all(letter in self.guessed for letter in self.word):
                self.lock()
                await self.conclude("win", f"The word was **{self.word.upper()}**. Well played.")
            elif self.wrong >= self.max_wrong:
                self.lock()
                await self.conclude("loss", f"Out of lives. The word was **{self.word.upper()}**.")
            else:
                self._render()
                await self.refresh()

        return callback


# ---------------------------------------------------------------------------
# Wordle
# ---------------------------------------------------------------------------

WORDLE_WORDS = [
    "about", "above", "actor", "adore", "after", "again", "agent", "album",
    "alert", "alike", "alive", "allow", "alone", "along", "alter", "amber",
    "angel", "anger", "angle", "apple", "arena", "argue", "arise", "armor",
    "aroma", "array", "aside", "asset", "atlas", "audio", "audit", "avoid",
    "awake", "award", "bacon", "badge", "baker", "basic", "basin", "batch",
    "beach", "beard", "beast", "begin", "being", "belly", "below", "bench",
    "berry", "birth", "black", "blade", "blame", "blank", "blast", "blaze",
    "bleed", "blend", "bless", "blind", "block", "bloom", "blown", "blues",
    "board", "boast", "bonus", "boost", "booth", "bound", "brain", "brand",
    "brave", "bread", "break", "breed", "brick", "bride", "brief", "bring",
    "broad", "broke", "brown", "brush", "build", "built", "burst", "cabin",
    "cable", "camel", "candy", "canoe", "carry", "carve", "catch", "cause",
    "cease", "chain", "chair", "chalk", "charm", "chart", "chase", "cheap",
    "check", "cheek", "cheer", "chess", "chest", "chief", "child", "chill",
    "china", "choir", "chose", "civil", "claim", "class", "clean", "clear",
    "clerk", "click", "cliff", "climb", "clock", "close", "cloth", "cloud",
    "coach", "coast", "color", "coral", "couch", "cough", "could", "count",
    "court", "cover", "crack", "craft", " crane".strip(), "crash", "crazy",
    "cream", "creek", "crest", "crime", "crisp", "cross", "crowd", "crown",
    "crumb", "curve", "cycle", "daily", "dairy", "dance", "dated", "debut",
    "decay", "delay", "delta", "dense", "depth", "derby", "diary", "digit",
    "dirty", "doubt", "dozen", "draft", "drain", "drama", "drank", "dream",
    "dress", "dried", "drift", "drink", "drive", "drove", "dying", "eager",
    "early", "earth", "eight", "elbow", "elder", "elect", "elite", "empty",
    "enemy", "enjoy", "enter", "entry", "equal", "error", "essay", "event",
    "every", "exact", "exist", "extra", "fable", "faith", "false", "fancy",
    "fatal", "fault", "favor", "feast", "fence", "ferry", "fetch", "fever",
    "fiber", "field", "fifth", "fifty", "fight", "final", "first", "fixed",
    "flame", "flash", "fleet", "flesh", "float", "flock", "flood", "floor",
    "flour", "fluid", "flush", "focus", "force", "forge", "forth", "forty",
    "forum", "found", "frame", "fresh", "front", "frost", "fruit", "fully",
    "funny", "gauge", "ghost", "giant", "given", "glass", "globe", "glory",
    "glove", "going", "grace", "grade", "grain", "grand", "grant", "grape",
    "graph", "grasp", "grass", "grave", "great", "green", "greet", "grief",
    "grill", "grind", "groan", "groom", "gross", "group", "grove", "grown",
    "guard", "guess", "guest", "guide", "happy", "harsh", "haste", "haven",
    "heart", "heavy", "hedge", "hello", "hence", "hobby", "holly", "honey",
    "honor", "horse", "hotel", "house", "human", "humble", "humor", "hurry",
    "ideal", "image", "imply", "index", "inner", "input", " irony".strip(),
    "issue", "ivory", "jelly", "joint", "jolly", "judge", "juice", "jumbo",
    "knife", "knock", "known", "label", "labor", "large", "laser", "later",
    "laugh", "layer", "learn", "lease", "least", "leave", "legal", "lemon",
    "level", "light", "limit", "linen", "liver", "lobby", "local", "lodge",
    "logic", "loose", "lover", "lower", "loyal", "lucky", "lunar", "lunch",
    "lying", "magic", "major", "maker", "maple", "march", "match", "maybe",
    "mayor", "meant", "medal", "media", "mercy", "merge", "merit", "merry",
    "metal", "meter", "midst", "might", "minor", "minus", "mixed", "model",
    "moist", "money", "month", "moral", "motor", "mount", "mouse", "mouth",
    "movie", "music", "naked", "nasty", "naval", "nerve", "never", "newly",
    "night", "noble", "noise", "north", "noted", "novel", "nurse", "ocean",
    "offer", "often", "olive", "onion", "opera", "orbit", "order", "organ",
    "other", "ought", "outer", "owner", "oxide", "paint", "panel", "paper",
    "party", "pasta", "patch", "pause", "peace", "pearl", "penny", "phase",
    "phone", "photo", "piano", "piece", "pilot", "pitch", "place", "plain",
    "plane", "plant", "plate", "plaza", "point", "polar", "porch", "pound",
    "power", "press", "price", "pride", "prime", "print", "prior", "prize",
    "proof", "proud", "prove", "quick", "quiet", "quite", "quote", "radar",
    "radio", "raise", "rally", "range", "rapid", "ratio", "reach", "react",
    "ready", "realm", "rebel", "refer", "relax", "reply", "rider", "ridge",
    "rifle", "right", "rigid", "rinse", "risen", "rival", "river", "roast",
    "robin", "robot", "rocky", "roman", "rough", "round", "route", "royal",
    "rural", "saint", "salad", "sauce", "scale", "scare", "scene", "scent",
    "scope", "score", "scout", "scrap", "screw", "sense", "serve", "seven",
    "shade", "shake", "shall", "shame", "shape", "share", "shark", "sharp",
    "sheep", "sheet", "shelf", "shell", "shift", "shine", "shirt", "shock",
    "shore", "short", "shown", "sight", "silly", "since", "sixth", "skill",
    "sleep", "slice", "slide", "slope", "small", "smart", "smile", "smoke",
    "solid", "solve", "sorry", "sound", "south", "space", "spare", "spark",
    "speak", "speed", "spend", "spent", "spice", "spine", "spite", "split",
    "spoke", "sport", "spray", "squad", "staff", "stage", "stain", "stair",
    "stake", "stamp", "stand", "stare", "start", "state", "stays", "steak",
    "steal", "steam", "steel", "steep", "steer", "stern", "stick", "still",
    "stock", "stone", "stood", "store", "storm", "story", "stove", "strap",
    "straw", "strip", "study", "stuff", "style", "sugar", "suite", "sunny",
    "super", "sweet", "swift", "swing", "sword", "table", "taste", "teach",
    "teeth", "thank", "theft", "their", "theme", "there", "these", "thick",
    "thief", "thing", "think", "third", "those", "three", "threw", "throw",
    "thumb", "tiger", "tight", "timer", "title", "toast", "today", "token",
    "tooth", "topic", "torch", "total", "touch", "tough", "tower", "trace",
    "track", "trade", "trail", "train", "trait", "trash", "treat", "trend",
    "trial", "tribe", "trick", "tried", "truck", "truly", "trunk", "trust",
    "truth", "twice", "uncle", "under", "union", "unity", "until", "upper",
    "urban", "usage", "usual", "valid", "value", "vapor", "vault", "venue",
    "video", "virus", "visit", "vital", "vivid", "vocal", "voice", "voter",
    "wagon", "waste", "watch", "water", "weary", "weave", "wedge", "weight",
    "weird", "whale", "wheat", "wheel", "where", "which", "while", "white",
    "whole", "whose", "widow", "width", "witch", "woman", "world", "worry",
    "worse", "worst", "worth", "would", "wound", "wrath", "write", "wrong",
    "wrote", "yacht", "yield", "young", "youth", "zebra",
]

GREEN = "🟩"
YELLOW = "🟨"
GREY = "⬛"


def _wordle_row(guess: str, answer: str) -> list[str]:
    result = [GREY] * 5
    remaining = list(answer)
    for index, letter in enumerate(guess):
        if letter == answer[index]:
            result[index] = GREEN
            remaining[index] = None  # type: ignore[assignment]
    for index, letter in enumerate(guess):
        if result[index] == GREEN:
            continue
        if letter in remaining:
            result[index] = YELLOW
            remaining[remaining.index(letter)] = None  # type: ignore[assignment]
    return result


class _GuessModal(discord.ui.Modal, title="Wordle guess"):
    guess = discord.ui.TextInput(
        label="Five-letter word",
        min_length=5,
        max_length=5,
        placeholder="e.g. crane",
    )

    def __init__(self, view: "WordleView"):
        super().__init__()
        self.view_ref = view

    async def on_submit(self, interaction: discord.Interaction):
        await self.view_ref.submit_guess(interaction, str(self.guess.value).lower())


class WordleView(GameView):
    game_name = "wordle"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx, timeout=360)
        self.answer = random.choice(WORDLE_WORDS)
        self.guesses: list[tuple[str, list[str]]] = []
        self._render()

    def _render(self) -> None:
        rows = []
        for guess, pattern in self.guesses:
            rows.append(" ".join(f"{letter.upper()}" for letter in guess) + "\n" + " ".join(pattern))
        while len(rows) < 6:
            rows.append("⬜ ⬜ ⬜ ⬜ ⬜")
        self.embed.description = "\n\n".join(rows) + (
            f"\n\nGuess **{len(self.guesses)}/6**. Click below to enter a word."
        )

    async def submit_guess(self, interaction: discord.Interaction, guess: str) -> None:
        if not guess.isalpha() or len(guess) != 5:
            await interaction.response.send_message("Five letters only, sir.", ephemeral=True)
            return
        if len(self.guesses) >= 6 or self.finished:
            await interaction.response.send_message("The game is over, sir.", ephemeral=True)
            return
        pattern = _wordle_row(guess, self.answer)
        self.guesses.append((guess, pattern))
        if guess == self.answer:
            self.lock()
            await interaction.response.defer()
            await self.conclude("win", f"You found **{self.answer.upper()}** in {len(self.guesses)} guess(es).")
            return
        if len(self.guesses) >= 6:
            self.lock()
            await interaction.response.defer()
            await self.conclude("loss", f"Out of guesses. The word was **{self.answer.upper()}**.")
            return
        self._render()
        await interaction.response.defer()
        await self.refresh()

    @discord.ui.button(label="Enter guess", emoji="⌨️", style=discord.ButtonStyle.primary)
    async def guess_button(self, interaction, _button):
        await interaction.response.send_modal(_GuessModal(self))


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

MEMORY_EMOJIS = ["🍎", "🍇", "🍋", "🍒", "🥝", "🍑", "🍊", "🫐"]


class MemoryView(GameView):
    game_name = "memory"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx, timeout=300)
        self.values = MEMORY_EMOJIS * 2
        random.shuffle(self.values)
        self.revealed: dict[int, str] = {}
        self.matched: set[int] = set()
        self.first: int | None = None
        self.busy = False
        for index in range(16):
            button = discord.ui.Button(
                label="❓",
                style=discord.ButtonStyle.secondary,
                row=index // 4,
            )
            button.callback = self._make_callback(index)
            self.add_item(button)
        self._render()

    def _render(self) -> None:
        pairs = len(self.matched) // 2
        self.embed.description = (
            f"Find all **8** pairs. Matched: **{pairs}/8**.\n"
            "Click two tiles; matching tiles stay revealed."
        )

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            if self.busy or index in self.matched or index in self.revealed:
                await interaction.response.send_message("That tile isn't available, sir.", ephemeral=True)
                return
            self.revealed[index] = self.values[index]
            button = self.children[index]
            button.label = self.values[index]
            button.style = discord.ButtonStyle.primary
            await interaction.response.defer()
            await self.refresh()

            if self.first is None:
                self.first = index
                return
            first = self.first
            self.first = None
            if self.values[first] == self.values[index]:
                self.matched.update({first, index})
                for matched in (first, index):
                    self.children[matched].disabled = True
                    self.children[matched].style = discord.ButtonStyle.success
                self.revealed.pop(first, None)
                self.revealed.pop(index, None)
                self._render()
                if len(self.matched) == 16:
                    self.lock()
                    await self.conclude("win", "Every pair found. Flawless memory, sir.")
                else:
                    await self.refresh()
                return

            self.busy = True
            await asyncio.sleep(1.0)
            for tile in (first, index):
                if tile not in self.matched:
                    self.revealed.pop(tile, None)
                    self.children[tile].label = "❓"
                    self.children[tile].style = discord.ButtonStyle.secondary
            self.busy = False
            await self.refresh()

        return callback


# ---------------------------------------------------------------------------
# Reaction speed
# ---------------------------------------------------------------------------


class ReactionView(GameView):
    game_name = "reaction test"

    def __init__(self, bot, ctx):
        super().__init__(bot, ctx, timeout=30)
        self.embed.description = "Wait for the button to light up, then click it as fast as you can."
        self.timer_button = self.children[0]
        self.timer_button.disabled = True
        self.ready_at: float | None = None
        self.task: asyncio.Task | None = None

    async def start(self) -> None:
        await super().start()
        self.task = asyncio.create_task(self._arm())

    async def _arm(self) -> None:
        await asyncio.sleep(random.uniform(1.5, 4.0))
        import time

        self.ready_at = time.perf_counter()
        self.timer_button.disabled = False
        self.timer_button.label = "CLICK NOW!"
        self.timer_button.style = discord.ButtonStyle.danger
        self.embed.description = "**Now!** Click the button."
        await self.refresh()

    @discord.ui.button(label="Wait…", emoji="⏳", style=discord.ButtonStyle.secondary)
    async def button(self, interaction, _button):
        import time

        if self.ready_at is None:
            await interaction.response.send_message("Not yet, sir.", ephemeral=True)
            return
        elapsed = time.perf_counter() - self.ready_at
        if self.task is not None:
            self.task.cancel()
        self.lock()
        if elapsed < 0.55:
            result, note = "win", f"**{elapsed * 1000:.0f} ms** — lightning fast."
        elif elapsed < 1.2:
            result, note = "draw", f"**{elapsed * 1000:.0f} ms** — respectable."
        else:
            result, note = "loss", f"**{elapsed * 1000:.0f} ms** — a little slow, sir."
        await interaction.response.defer()
        await self.conclude(result, note)

    async def on_timeout(self) -> None:
        if self.task is not None:
            self.task.cancel()
        await super().on_timeout()


__all__ = [
    "RPSView",
    "CoinflipView",
    "EightBallView",
    "DiceView",
    "SlotsView",
    "TriviaView",
    "HangmanView",
    "WordleView",
    "MemoryView",
    "ReactionView",
]
