"""Short-term conversation memory.

Jarvis keeps a small per-user/per-channel transcript so follow-up questions work
without storing private conversations forever.  Everything is in memory only
and is trimmed by both message count and character budget.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from .config import Settings, load_settings


@dataclass(slots=True)
class Turn:
    role: str
    content: str
    at: float


class ConversationMemory:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self._turns: dict[tuple[int, int, int], deque[Turn]] = defaultdict(deque)
        self._channels: dict[tuple[int, int], deque[Turn]] = defaultdict(deque)
        self._last_activity: dict[tuple[int, int, int], float] = {}

    # -- conversation ------------------------------------------------------
    def history(self, guild_id: int | None, channel_id: int, user_id: int) -> list[Turn]:
        key = (guild_id or 0, channel_id, user_id)
        turns = self._turns.get(key)
        if not turns:
            return []
        cutoff = time.monotonic() - 3600
        fresh = [t for t in turns if t.at >= cutoff]
        if len(fresh) != len(turns):
            self._turns[key] = deque(fresh)
        return list(fresh)

    def add_user(self, guild_id: int | None, channel_id: int, user_id: int, content: str) -> None:
        self._append(self._turns[(guild_id or 0, channel_id, user_id)], "user", content)

    def add_assistant(self, guild_id: int | None, channel_id: int, user_id: int, content: str) -> None:
        self._append(self._turns[(guild_id or 0, channel_id, user_id)], "assistant", content)

    def touch(self, guild_id: int | None, channel_id: int, user_id: int) -> None:
        self._last_activity[(guild_id or 0, channel_id, user_id)] = time.monotonic()

    def seconds_since(self, guild_id: int | None, channel_id: int, user_id: int) -> float:
        last = self._last_activity.get((guild_id or 0, channel_id, user_id), 0.0)
        return time.monotonic() - last if last else 1e9

    def clear(self, guild_id: int | None, channel_id: int, user_id: int) -> None:
        key = (guild_id or 0, channel_id, user_id)
        self._turns.pop(key, None)
        self._last_activity.pop(key, None)

    def clear_channel(self, guild_id: int | None, channel_id: int) -> None:
        prefix = (guild_id or 0, channel_id)
        for key in [k for k in self._turns if k[:2] == prefix]:
            self._turns.pop(key, None)
            self._last_activity.pop(key, None)

    # -- channel context ---------------------------------------------------
    def note_channel_message(
        self, guild_id: int | None, channel_id: int, author: str, content: str
    ) -> None:
        text = str(content or "").strip()
        if not text:
            return
        self._append(self._channels[(guild_id or 0, channel_id)], "context", f"{author}: {text}", limit=40)

    def recent_channel(
        self, guild_id: int | None, channel_id: int, limit: int = 8, max_chars: int = 2400
    ) -> list[str]:
        turns = self._channels.get((guild_id or 0, channel_id))
        if not turns:
            return []
        result: list[str] = []
        used = 0
        for turn in reversed(turns):
            if len(result) >= limit or used + len(turn.content) > max_chars:
                break
            result.append(turn.content)
            used += len(turn.content)
        result.reverse()
        return result

    # -- internals ---------------------------------------------------------
    def _append(self, bucket: deque[Turn], role: str, content: str, limit: int | None = None) -> None:
        text = str(content or "").strip()
        if not text:
            return
        bucket.append(Turn(role=role, content=text[:8000], at=time.monotonic()))
        max_messages = limit or self.settings.ai_history_messages
        while len(bucket) > max_messages:
            bucket.popleft()
        total = 0
        for turn in reversed(bucket):
            total += len(turn.content)
            if total > self.settings.ai_history_chars:
                bucket.popleft()
                break
