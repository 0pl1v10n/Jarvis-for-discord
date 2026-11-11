"""Discord channel-backed persistence for Jarvis v2.

Jarvis no longer writes a database file.  Instead it keeps its data in a
private category inside each guild.  Every data domain (levels, economy,
warnings, ...) gets its own text channel, and the current snapshot of that
domain is stored as a JSON file attachment on the newest message in the
channel.

Updates follow the rule the server owner asked for:

1. the previous snapshot is still present in the channel,
2. Jarvis uploads a complete new snapshot with the single change applied,
3. only after the upload succeeds does Jarvis delete the old snapshot.

That means a crash or a Discord error can never leave a channel with no valid
copy of the data.  Reads are served from an in-memory index which is hydrated
from those channels on startup, so checking a user's level never depends on the
AI and never needs a slow channel scan during normal use.

The public methods intentionally mirror the old SQLite helper.  Callers keep
using ``ctx.db.level(...)``, ``ctx.db.add_xp(...)`` and so on; they do not need
to know that Discord is now the storage layer.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import random
import sqlite3
import threading
from pathlib import Path
from typing import Any

import discord

from .util import iso, utcnow

log = logging.getLogger(__name__)

CATEGORY_NAME = "Jarvis Data"
SNAPSHOT_MARKER = "JARVIS-DATA"

#: Domain name -> Discord channel name.  The order also controls migration
#: order for the legacy SQLite importer.
DOMAIN_CHANNELS: dict[str, str] = {
    "config": "jarvis-config",
    "levels": "jarvis-levels",
    "economy": "jarvis-economy",
    "warnings": "jarvis-warnings",
    "notes": "jarvis-notes",
    "autoresponses": "jarvis-autoresponses",
    "reaction_roles": "jarvis-reaction-roles",
    "reminders": "jarvis-reminders",
    "giveaways": "jarvis-giveaways",
    "giveaway_entries": "jarvis-giveaway-entries",
    "tickets": "jarvis-tickets",
    "scheduled_actions": "jarvis-scheduled-actions",
    "game_stats": "jarvis-game-stats",
    "audit": "jarvis-audit",
    "starboard": "jarvis-starboard",
}

#: Domains whose records have an auto-incrementing ``id`` field.
ID_DOMAINS = (
    "warnings",
    "notes",
    "autoresponses",
    "reaction_roles",
    "reminders",
    "giveaways",
    "tickets",
    "scheduled_actions",
    "audit",
)

DICT_DOMAINS = {"config", "levels", "economy", "game_stats"}


class Database:
    """A write-behind, Discord-channel-backed data store."""

    def __init__(
        self,
        bot,
        *,
        category_name: str = CATEGORY_NAME,
        flush_seconds: float = 2.0,
        legacy_path: str | Path | None = None,
    ):
        self.bot = bot
        self.category_name = (category_name or CATEGORY_NAME).strip() or CATEGORY_NAME
        self.flush_seconds = max(0.25, float(flush_seconds or 2.0))
        self.legacy_path = Path(legacy_path).expanduser() if legacy_path else None

        self._stores: dict[int, dict[str, Any]] = {}
        self._loaded_guilds: set[int] = set()
        self._dirty: set[tuple[int, str]] = set()
        self._ids: dict[str, int] = {domain: 1 for domain in ID_DOMAINS}
        self._categories: dict[int, discord.CategoryChannel] = {}
        self._channels: dict[tuple[int, str], discord.TextChannel] = {}
        self._snapshots: dict[tuple[int, str], list[int]] = {}
        self._warned: set[tuple[int, str]] = set()
        self._home_guild_id: int | None = None
        self._lock = threading.RLock()
        self._flush_lock: asyncio.Lock | None = None
        self._flush_event: asyncio.Event | None = None
        self._flush_task: asyncio.Task | None = None
        self._closed = False
        self._initialized = False

    # -- lifecycle ---------------------------------------------------------
    @property
    def initialized(self) -> bool:
        return self._initialized

    async def initialize(self) -> None:
        """Hydrate the in-memory index from Discord and start the flusher."""
        if self._initialized:
            return
        self._flush_lock = asyncio.Lock()
        self._flush_event = asyncio.Event()
        self._home_guild_id = self._choose_home_guild()

        for guild in list(self.bot.guilds):
            await self._load_guild(guild)
        self._refresh_ids()

        # One-time migration: if the old SQLite file exists and there is no
        # channel data yet, import it so existing levels/economy are not lost.
        if not self._has_data():
            await self._maybe_migrate_legacy()

        self._flush_task = asyncio.create_task(self._flush_loop(), name="jarvis-data-flush")
        self._initialized = True
        log.info(
            "Channel storage ready: %d guild(s), %d cached domain(s), category %r",
            len(self._loaded_guilds),
            sum(1 for store in self._stores.values() for value in store.values() if value),
            self.category_name,
        )

    async def close(self) -> None:
        self._closed = True
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        try:
            await self.flush()
        except Exception:
            log.exception("Final channel storage flush failed")

    async def ensure_guild(self, guild: discord.Guild) -> None:
        """Load a guild's data channels (used when Jarvis joins a new server)."""
        if guild.id in self._loaded_guilds:
            return
        await self._load_guild(guild)
        self._refresh_ids()

    async def refresh_guild(self, guild: discord.Guild) -> None:
        """Discard the cache and re-read this guild's channels."""
        await self.flush()
        self._stores[guild.id] = self._empty_store()
        self._loaded_guilds.discard(guild.id)
        for key in [key for key in self._channels if key[0] == guild.id]:
            self._channels.pop(key, None)
        for key in [key for key in self._snapshots if key[0] == guild.id]:
            self._snapshots.pop(key, None)
        await self._load_guild(guild)
        self._refresh_ids()

    # -- guild / channel plumbing -----------------------------------------
    def _choose_home_guild(self) -> int | None:
        settings = getattr(self.bot, "settings", None)
        wanted = int(getattr(settings, "guild_id", 0) or 0)
        if wanted:
            guild = self.bot.get_guild(wanted)
            if guild is not None:
                return guild.id
        guilds = list(getattr(self.bot, "guilds", []) or [])
        return guilds[0].id if guilds else None

    def _storage_guild_id(self, guild_id: int | None) -> int:
        """Map DMs/global rows to the home guild's storage channels."""
        if not guild_id:
            return self._home_guild_id or 0
        return int(guild_id)

    @staticmethod
    def _empty_store() -> dict[str, Any]:
        return {
            "config": {},
            "levels": {},
            "economy": {},
            "warnings": [],
            "notes": [],
            "autoresponses": [],
            "reaction_roles": [],
            "reminders": [],
            "giveaways": [],
            "giveaway_entries": [],
            "tickets": [],
            "scheduled_actions": [],
            "game_stats": {},
            "audit": [],
            "starboard": [],
        }

    def _store(self, guild_id: int | None) -> dict[str, Any]:
        gid = self._storage_guild_id(guild_id)
        return self._stores.setdefault(gid, self._empty_store())

    def _find_category(self, guild: discord.Guild) -> discord.CategoryChannel | None:
        cached = self._categories.get(guild.id)
        if cached is not None and guild.get_channel(cached.id) is not None:
            return cached
        return discord.utils.find(
            lambda category: category.name.lower() == self.category_name.lower(),
            guild.categories,
        )

    def _category_overwrites(self, guild: discord.Guild) -> dict:
        me = guild.me
        if me is None and self.bot.user is not None:
            me = guild.get_member(self.bot.user.id)
        overwrites: dict[Any, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False)
        }
        if me is not None:
            overwrites[me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True,
                manage_channels=True,
            )
        return overwrites

    async def _ensure_category(self, guild: discord.Guild) -> discord.CategoryChannel | None:
        category = self._find_category(guild)
        if category is not None:
            self._categories[guild.id] = category
            return category
        try:
            category = await guild.create_category(
                self.category_name,
                overwrites=self._category_overwrites(guild),
                reason="Jarvis data storage",
            )
        except discord.HTTPException:
            key = (guild.id, "__category__")
            if key not in self._warned:
                self._warned.add(key)
                log.exception("Could not create the Jarvis data category in guild %s", guild.id)
            return None
        self._categories[guild.id] = category
        log.info("Created private data category %r in guild %s", self.category_name, guild.id)
        return category

    async def _ensure_channel(
        self, guild: discord.Guild, domain: str
    ) -> discord.TextChannel | None:
        key = (guild.id, domain)
        cached = self._channels.get(key)
        if cached is not None and guild.get_channel(cached.id) is not None:
            return cached

        category = await self._ensure_category(guild)
        if category is None:
            return None

        name = DOMAIN_CHANNELS.get(domain, f"jarvis-{domain}")
        channel = discord.utils.find(
            lambda item: item.name == name and isinstance(item, discord.TextChannel),
            category.channels,
        )
        if channel is None:
            try:
                channel = await guild.create_text_channel(
                    name=name,
                    category=category,
                    topic=f"Jarvis data store: {domain}. Managed automatically — do not edit.",
                    reason="Jarvis data storage",
                )
            except discord.HTTPException:
                if key not in self._warned:
                    self._warned.add(key)
                    log.exception(
                        "Could not create data channel #%s in guild %s", name, guild.id
                    )
                return None
            log.info("Created data channel #%s in guild %s", name, guild.id)

        self._channels[key] = channel
        return channel

    async def ensure_all_channels(self, guild: discord.Guild) -> None:
        for domain in DOMAIN_CHANNELS:
            await self._ensure_channel(guild, domain)

    def channel_for(self, guild_id: int, domain: str) -> discord.TextChannel | None:
        return self._channels.get((self._storage_guild_id(guild_id), domain))

    def summary(self, guild_id: int) -> list[dict[str, Any]]:
        store = self._store(guild_id)
        gid = self._storage_guild_id(guild_id)
        result: list[dict[str, Any]] = []
        for domain in DOMAIN_CHANNELS:
            value = store.get(domain)
            channel = self._channels.get((gid, domain))
            result.append(
                {
                    "domain": domain,
                    "records": len(value) if hasattr(value, "__len__") else 0,
                    "channel_id": channel.id if channel is not None else 0,
                }
            )
        return result

    # -- hydration ---------------------------------------------------------
    async def _load_guild(self, guild: discord.Guild) -> None:
        if guild.id in self._loaded_guilds:
            return
        self._loaded_guilds.add(guild.id)
        self._stores.setdefault(guild.id, self._empty_store())

        category = self._find_category(guild)
        if category is None:
            return
        self._categories[guild.id] = category

        loaded = 0
        for domain, channel_name in DOMAIN_CHANNELS.items():
            channel = discord.utils.find(
                lambda item, wanted=channel_name: item.name == wanted
                and isinstance(item, discord.TextChannel),
                category.channels,
            )
            if channel is None:
                continue
            self._channels[(guild.id, domain)] = channel
            payload = await self._read_snapshot(channel, domain)
            if payload is not None:
                self._stores[guild.id][domain] = self._deserialize(domain, payload)
                loaded += 1
        if loaded:
            log.info("Loaded %d data channel(s) from guild %s", loaded, guild.id)

    async def _read_snapshot(
        self, channel: discord.TextChannel, domain: str
    ) -> dict[str, Any] | None:
        newest: dict[str, Any] | None = None
        stale: list[discord.Message] = []
        try:
            async for message in channel.history(limit=100):
                if self.bot.user is not None and message.author.id != self.bot.user.id:
                    continue
                payload = await self._decode_snapshot(message, domain)
                if payload is None:
                    continue
                if newest is None:
                    newest = payload
                    self._snapshots[(channel.guild.id, domain)] = [message.id]
                else:
                    stale.append(message)
        except discord.HTTPException:
            log.exception("Could not read data channel #%s", channel.name)
            return None

        for message in stale:
            try:
                await message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        return newest

    async def _decode_snapshot(
        self, message: discord.Message, domain: str
    ) -> dict[str, Any] | None:
        for attachment in message.attachments:
            if not attachment.filename.lower().endswith(".json"):
                continue
            try:
                raw = await attachment.read()
                payload = json.loads(raw.decode("utf-8"))
            except (discord.HTTPException, ValueError, UnicodeDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("domain") == domain:
                return payload

        content = message.content or ""
        if SNAPSHOT_MARKER in content:
            start = content.find("{")
            if start >= 0:
                try:
                    payload = json.loads(content[start:])
                except ValueError:
                    payload = None
                if isinstance(payload, dict) and payload.get("domain") == domain:
                    return payload
        return None

    def _has_data(self) -> bool:
        for store in self._stores.values():
            for value in store.values():
                if value:
                    return True
        return False

    # -- serialization -----------------------------------------------------
    def _serialize(self, domain: str, store: dict[str, Any]) -> dict[str, Any]:
        if domain == "config":
            rows = [{"key": key, "value": value} for key, value in store["config"].items()]
        elif domain == "levels":
            rows = [{"user_id": user_id, **record} for user_id, record in store["levels"].items()]
        elif domain == "economy":
            rows = [{"user_id": user_id, **record} for user_id, record in store["economy"].items()]
        elif domain == "game_stats":
            rows = [
                {"user_id": user_id, "game": game, **record}
                for (user_id, game), record in store["game_stats"].items()
            ]
        else:
            rows = [dict(row) for row in store[domain]]
        return {"domain": domain, "updated_at": iso(), "rows": rows}

    def _deserialize(self, domain: str, payload: dict[str, Any]) -> Any:
        rows = payload.get("rows") or []
        if domain == "config":
            result: dict[str, Any] = {}
            for row in rows:
                if isinstance(row, dict) and "key" in row:
                    result[str(row["key"])] = row.get("value")
            return result
        if domain == "levels":
            result = {}
            for row in rows:
                try:
                    result[int(row["user_id"])] = {
                        "xp": int(row.get("xp", 0)),
                        "messages": int(row.get("messages", 0)),
                        "last_xp": float(row.get("last_xp", 0.0)),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
            return result
        if domain == "economy":
            result = {}
            for row in rows:
                try:
                    result[int(row["user_id"])] = {
                        "balance": int(row.get("balance", 0)),
                        "last_daily": float(row.get("last_daily", 0.0)),
                        "last_work": float(row.get("last_work", 0.0)),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
            return result
        if domain == "game_stats":
            result = {}
            for row in rows:
                try:
                    result[(int(row["user_id"]), str(row["game"]))] = {
                        "wins": int(row.get("wins", 0)),
                        "losses": int(row.get("losses", 0)),
                        "draws": int(row.get("draws", 0)),
                        "plays": int(row.get("plays", 0)),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
            return result
        return [dict(row) for row in rows if isinstance(row, dict)]

    # -- flushing ----------------------------------------------------------
    def _mark_dirty(self, guild_id: int | None, domain: str) -> None:
        gid = self._storage_guild_id(guild_id)
        if gid <= 0:
            return
        self._dirty.add((gid, domain))
        event = self._flush_event
        if event is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_soon_threadsafe(event.set)

    async def _flush_loop(self) -> None:
        assert self._flush_event is not None
        while not self._closed:
            await self._flush_event.wait()
            self._flush_event.clear()
            if self._closed:
                break
            await asyncio.sleep(self.flush_seconds)
            if self._closed:
                break
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Channel storage flush failed")
            if self._dirty and not self._closed:
                self._flush_event.set()

    async def flush(self) -> None:
        if self._flush_lock is None:
            return
        async with self._flush_lock:
            dirty = sorted(self._dirty)
            self._dirty.clear()
            try:
                for guild_id, domain in dirty:
                    await self._flush_domain(guild_id, domain)
            except asyncio.CancelledError:
                # Re-queue everything; a duplicate upload is harmless and is
                # much safer than losing a write.
                self._dirty.update(dirty)
                raise

    async def _flush_domain(self, guild_id: int, domain: str) -> None:
        if guild_id <= 0:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            log.warning("Cannot persist %s: guild %s is not available", domain, guild_id)
            return
        channel = await self._ensure_channel(guild, domain)
        if channel is None:
            raise RuntimeError(f"data channel for {domain} is unavailable")

        payload = self._serialize(domain, self._store(guild_id))
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        file = discord.File(io.BytesIO(raw), filename=f"jarvis-{domain}.json")
        try:
            message = await channel.send(
                content=f"`{SNAPSHOT_MARKER}:{domain}` · {iso()}",
                file=file,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            key = (guild_id, domain)
            if key not in self._warned:
                self._warned.add(key)
                log.exception("Could not upload the %s snapshot in guild %s", domain, guild_id)
            raise

        old_ids = self._snapshots.get((guild_id, domain), [])
        self._snapshots[(guild_id, domain)] = [message.id]
        # Only delete the previous copy after the new copy is safely uploaded.
        for old_id in old_ids:
            if old_id == message.id:
                continue
            try:
                await channel.get_partial_message(old_id).delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        log.debug("Persisted %s for guild %s (%d bytes)", domain, guild_id, len(raw))

    # -- ids ---------------------------------------------------------------
    def _refresh_ids(self) -> None:
        for domain in ID_DOMAINS:
            highest = 0
            for store in self._stores.values():
                for row in store.get(domain, []):
                    try:
                        highest = max(highest, int(row.get("id") or 0))
                    except (TypeError, ValueError):
                        continue
            self._ids[domain] = highest + 1

    def _next_id(self, domain: str) -> int:
        with self._lock:
            value = self._ids.get(domain, 1)
            self._ids[domain] = value + 1
            return value

    def _find_row(self, domain: str, predicate) -> tuple[int, dict[str, Any]] | None:
        for guild_id, store in self._stores.items():
            for row in store.get(domain, []):
                if predicate(row):
                    return guild_id, row
        return None

    # -- legacy SQLite migration ------------------------------------------
    async def _maybe_migrate_legacy(self) -> None:
        path = self.legacy_path
        if path is None or not path.exists() or path.stat().st_size == 0:
            return
        try:
            imported = self._read_legacy(path)
        except Exception:
            log.exception("Could not read legacy SQLite data from %s", path)
            return
        if not imported:
            return
        for guild_id, store in self._stores.items():
            for domain, value in store.items():
                if value:
                    self._mark_dirty(guild_id, domain)
        await self.flush()
        log.info(
            "Imported %d legacy row(s) from %s into Discord channels; the old "
            "SQLite file is no longer used and can be deleted.",
            imported,
            path,
        )

    def _read_legacy(self, path: Path) -> int:
        connection = sqlite3.connect(str(path))
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            imported = 0
            for domain in DOMAIN_CHANNELS:
                if domain not in tables:
                    continue
                try:
                    rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{domain}"')]
                except sqlite3.DatabaseError:
                    continue
                for row in rows:
                    guild_id = int(row.get("guild_id") or 0)
                    self._merge_legacy_row(self._store(guild_id), domain, row)
                    imported += 1
            if imported:
                self._refresh_ids()
            return imported
        finally:
            connection.close()

    def _merge_legacy_row(
        self, store: dict[str, Any], domain: str, row: dict[str, Any]
    ) -> None:
        if domain == "config":
            value = row.get("value")
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                pass
            store["config"][str(row.get("key"))] = value
        elif domain == "levels":
            store["levels"][int(row["user_id"])] = {
                "xp": int(row.get("xp", 0)),
                "messages": int(row.get("messages", 0)),
                "last_xp": float(row.get("last_xp", 0.0)),
            }
        elif domain == "economy":
            store["economy"][int(row["user_id"])] = {
                "balance": int(row.get("balance", 0)),
                "last_daily": float(row.get("last_daily", 0.0)),
                "last_work": float(row.get("last_work", 0.0)),
            }
        elif domain == "game_stats":
            store["game_stats"][(int(row["user_id"]), str(row["game"]))] = {
                "wins": int(row.get("wins", 0)),
                "losses": int(row.get("losses", 0)),
                "draws": int(row.get("draws", 0)),
                "plays": int(row.get("plays", 0)),
            }
        else:
            store[domain].append(dict(row))

    # -- config ------------------------------------------------------------
    def get_config(self, guild_id: int, key: str, default: Any = None) -> Any:
        return self._store(guild_id)["config"].get(str(key), default)

    def set_config(self, guild_id: int, key: str, value: Any) -> None:
        self._store(guild_id)["config"][str(key)] = value
        self._mark_dirty(guild_id, "config")

    def delete_config(self, guild_id: int, key: str) -> None:
        store = self._store(guild_id)
        if str(key) in store["config"]:
            del store["config"][str(key)]
            self._mark_dirty(guild_id, "config")

    def all_config(self, guild_id: int) -> dict[str, Any]:
        return dict(self._store(guild_id)["config"])

    # -- warnings / notes --------------------------------------------------
    def add_warning(self, guild_id: int, user_id: int, moderator_id: int, reason: str) -> int:
        row = {
            "id": self._next_id("warnings"),
            "guild_id": int(guild_id),
            "user_id": int(user_id),
            "moderator_id": int(moderator_id),
            "reason": reason,
            "created_at": iso(),
        }
        self._store(guild_id)["warnings"].append(row)
        self._mark_dirty(guild_id, "warnings")
        return int(row["id"])

    def warnings(self, guild_id: int, user_id: int) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self._store(guild_id)["warnings"]
            if int(row.get("user_id", 0)) == int(user_id)
        ]
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        return rows

    def warning_count(self, guild_id: int, user_id: int) -> int:
        return len(self.warnings(guild_id, user_id))

    def clear_warnings(self, guild_id: int, user_id: int) -> int:
        store = self._store(guild_id)
        before = len(store["warnings"])
        store["warnings"] = [
            row for row in store["warnings"] if int(row.get("user_id", 0)) != int(user_id)
        ]
        removed = before - len(store["warnings"])
        if removed:
            self._mark_dirty(guild_id, "warnings")
        return removed

    def remove_warning(self, guild_id: int, warning_id: int) -> bool:
        store = self._store(guild_id)
        before = len(store["warnings"])
        store["warnings"] = [
            row for row in store["warnings"] if int(row.get("id", 0)) != int(warning_id)
        ]
        removed = before - len(store["warnings"])
        if removed:
            self._mark_dirty(guild_id, "warnings")
        return bool(removed)

    def add_note(self, guild_id: int, user_id: int, author_id: int, note: str) -> int:
        row = {
            "id": self._next_id("notes"),
            "guild_id": int(guild_id),
            "user_id": int(user_id),
            "author_id": int(author_id),
            "note": note,
            "created_at": iso(),
        }
        self._store(guild_id)["notes"].append(row)
        self._mark_dirty(guild_id, "notes")
        return int(row["id"])

    def notes(self, guild_id: int, user_id: int) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self._store(guild_id)["notes"]
            if int(row.get("user_id", 0)) == int(user_id)
        ]
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        return rows

    def clear_notes(self, guild_id: int, user_id: int) -> int:
        store = self._store(guild_id)
        before = len(store["notes"])
        store["notes"] = [
            row for row in store["notes"] if int(row.get("user_id", 0)) != int(user_id)
        ]
        removed = before - len(store["notes"])
        if removed:
            self._mark_dirty(guild_id, "notes")
        return removed

    # -- autoresponses -----------------------------------------------------
    def add_autoresponse(
        self, guild_id: int, trigger: str, response: str, exact: bool = True
    ) -> int:
        row = {
            "id": self._next_id("autoresponses"),
            "guild_id": int(guild_id),
            "trigger": trigger.strip(),
            "response": response.strip(),
            "exact": int(exact),
            "created_at": iso(),
        }
        self._store(guild_id)["autoresponses"].append(row)
        self._mark_dirty(guild_id, "autoresponses")
        return int(row["id"])

    def remove_autoresponse(self, guild_id: int, identifier: str) -> bool:
        store = self._store(guild_id)
        before = len(store["autoresponses"])
        if str(identifier).isdigit():
            store["autoresponses"] = [
                row
                for row in store["autoresponses"]
                if int(row.get("id", 0)) != int(identifier)
            ]
        else:
            wanted = str(identifier).strip().lower()
            store["autoresponses"] = [
                row
                for row in store["autoresponses"]
                if str(row.get("trigger", "")).lower() != wanted
            ]
        removed = before - len(store["autoresponses"])
        if removed:
            self._mark_dirty(guild_id, "autoresponses")
        return bool(removed)

    def autoresponses(self, guild_id: int) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self._store(guild_id)["autoresponses"]]
        rows.sort(key=lambda row: int(row.get("id", 0)))
        return rows

    def match_autoresponse(self, guild_id: int, content: str) -> dict[str, Any] | None:
        text = str(content or "").strip().lower()
        if not text:
            return None
        for row in self.autoresponses(guild_id):
            trigger = str(row.get("trigger", "")).lower().strip()
            if row.get("exact") and text == trigger:
                return row
            if not row.get("exact") and trigger and trigger in text:
                return row
        return None

    # -- reaction roles ----------------------------------------------------
    def add_reaction_role(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        emoji: str,
        role_id: int,
    ) -> int:
        store = self._store(guild_id)
        for row in store["reaction_roles"]:
            if int(row.get("message_id", 0)) == int(message_id) and str(
                row.get("emoji")
            ) == str(emoji):
                row["channel_id"] = int(channel_id)
                row["role_id"] = int(role_id)
                row["guild_id"] = int(guild_id)
                self._mark_dirty(guild_id, "reaction_roles")
                return int(row["id"])
        row = {
            "id": self._next_id("reaction_roles"),
            "guild_id": int(guild_id),
            "channel_id": int(channel_id),
            "message_id": int(message_id),
            "emoji": str(emoji),
            "role_id": int(role_id),
            "created_at": iso(),
        }
        store["reaction_roles"].append(row)
        self._mark_dirty(guild_id, "reaction_roles")
        return int(row["id"])

    def remove_reaction_role(self, guild_id: int, identifier: str) -> bool:
        store = self._store(guild_id)
        before = len(store["reaction_roles"])
        if str(identifier).isdigit():
            store["reaction_roles"] = [
                row
                for row in store["reaction_roles"]
                if int(row.get("id", 0)) != int(identifier)
            ]
        else:
            store["reaction_roles"] = [
                row
                for row in store["reaction_roles"]
                if str(row.get("emoji")) != str(identifier)
            ]
        removed = before - len(store["reaction_roles"])
        if removed:
            self._mark_dirty(guild_id, "reaction_roles")
        return bool(removed)

    def reaction_roles(self, guild_id: int) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self._store(guild_id)["reaction_roles"]]
        rows.sort(key=lambda row: int(row.get("id", 0)))
        return rows

    def reaction_role_for(self, message_id: int, emoji: str) -> dict[str, Any] | None:
        for store in self._stores.values():
            for row in store["reaction_roles"]:
                if int(row.get("message_id", 0)) == int(message_id) and str(
                    row.get("emoji")
                ) == str(emoji):
                    return dict(row)
        return None

    # -- levels ------------------------------------------------------------
    def level(self, guild_id: int, user_id: int) -> dict[str, Any]:
        record = self._store(guild_id)["levels"].get(int(user_id))
        if record is None:
            return {"xp": 0, "messages": 0, "last_xp": 0.0}
        return dict(record)

    @staticmethod
    def level_from_xp(xp: int) -> int:
        # Smooth curve: level 1 at 100 XP, 2 at 300, 3 at 600, ...
        import math

        return int(math.floor((math.sqrt(1 + (8 * max(0, xp)) / 100) - 1) / 2))

    @staticmethod
    def xp_for_level(level: int) -> int:
        return max(0, int(50 * level * (level + 1)))

    def add_xp(
        self,
        guild_id: int,
        user_id: int,
        amount: int,
        now_ts: float,
        cooldown_seconds: float = 45.0,
    ) -> dict[str, Any] | None:
        store = self._store(guild_id)
        current = store["levels"].get(
            int(user_id), {"xp": 0, "messages": 0, "last_xp": 0.0}
        )
        if now_ts - float(current.get("last_xp", 0.0)) < cooldown_seconds:
            return None
        old_level = self.level_from_xp(int(current.get("xp", 0)))
        new_xp = int(current.get("xp", 0)) + max(1, amount)
        messages = int(current.get("messages", 0)) + 1
        store["levels"][int(user_id)] = {
            "xp": new_xp,
            "messages": messages,
            "last_xp": now_ts,
        }
        self._mark_dirty(guild_id, "levels")
        new_level = self.level_from_xp(new_xp)
        return {
            "xp": new_xp,
            "messages": messages,
            "old_level": old_level,
            "level": new_level,
            "leveled": new_level > old_level,
            "next_xp": self.xp_for_level(new_level + 1),
        }

    def set_xp(self, guild_id: int, user_id: int, xp: int) -> None:
        store = self._store(guild_id)
        record = store["levels"].get(int(user_id), {"messages": 0, "last_xp": 0.0})
        record["xp"] = max(0, int(xp))
        store["levels"][int(user_id)] = record
        self._mark_dirty(guild_id, "levels")

    def reset_level(self, guild_id: int, user_id: int) -> None:
        store = self._store(guild_id)
        if int(user_id) in store["levels"]:
            del store["levels"][int(user_id)]
            self._mark_dirty(guild_id, "levels")

    def level_leaderboard(self, guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
        rows = [
            {"user_id": user_id, **record}
            for user_id, record in self._store(guild_id)["levels"].items()
        ]
        rows.sort(key=lambda row: int(row.get("xp", 0)), reverse=True)
        return rows[:limit]

    # -- economy -----------------------------------------------------------
    def economy(self, guild_id: int, user_id: int) -> dict[str, Any]:
        record = self._store(guild_id)["economy"].get(int(user_id))
        if record is None:
            return {"balance": 0, "last_daily": 0.0, "last_work": 0.0}
        return dict(record)

    def set_economy(self, guild_id: int, user_id: int, **values) -> None:
        store = self._store(guild_id)
        current = dict(
            store["economy"].get(
                int(user_id), {"balance": 0, "last_daily": 0.0, "last_work": 0.0}
            )
        )
        current.update(
            {key: value for key, value in values.items() if key in {"balance", "last_daily", "last_work"}}
        )
        store["economy"][int(user_id)] = current
        self._mark_dirty(guild_id, "economy")

    def add_balance(self, guild_id: int, user_id: int, amount: int) -> int:
        current = self.economy(guild_id, user_id)
        value = max(0, int(current["balance"]) + int(amount))
        self.set_economy(guild_id, user_id, balance=value)
        return value

    def economy_leaderboard(self, guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
        rows = [
            {"user_id": user_id, **record}
            for user_id, record in self._store(guild_id)["economy"].items()
        ]
        rows.sort(key=lambda row: int(row.get("balance", 0)), reverse=True)
        return rows[:limit]

    # -- reminders ---------------------------------------------------------
    def add_reminder(
        self,
        user_id: int,
        channel_id: int,
        guild_id: int,
        message: str,
        remind_at: str,
    ) -> int:
        row = {
            "id": self._next_id("reminders"),
            "user_id": int(user_id),
            "channel_id": int(channel_id),
            "guild_id": int(guild_id),
            "message": message,
            "remind_at": remind_at,
            "created_at": iso(),
            "done": 0,
        }
        self._store(guild_id)["reminders"].append(row)
        self._mark_dirty(guild_id, "reminders")
        return int(row["id"])

    def reminders(self, user_id: int, include_done: bool = False) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for store in self._stores.values():
            for row in store["reminders"]:
                if int(row.get("user_id", 0)) != int(user_id):
                    continue
                if not include_done and row.get("done"):
                    continue
                rows.append(dict(row))
        rows.sort(key=lambda row: str(row.get("remind_at") or ""))
        return rows

    def due_reminders(self, now_iso: str, limit: int = 25) -> list[dict[str, Any]]:
        rows: list[tuple[int, dict[str, Any]]] = []
        for guild_id, store in self._stores.items():
            for row in store["reminders"]:
                if row.get("done"):
                    continue
                if str(row.get("remind_at") or "") <= now_iso:
                    rows.append((guild_id, dict(row)))
        rows.sort(key=lambda item: str(item[1].get("remind_at") or ""))
        return [row for _, row in rows[:limit]]

    def complete_reminder(self, reminder_id: int) -> None:
        found = self._find_row(
            "reminders", lambda row: int(row.get("id", 0)) == int(reminder_id)
        )
        if found is None:
            return
        guild_id, row = found
        row["done"] = 1
        self._mark_dirty(guild_id, "reminders")

    def delete_reminder(self, user_id: int, reminder_id: int) -> bool:
        for guild_id, store in self._stores.items():
            before = len(store["reminders"])
            store["reminders"] = [
                row
                for row in store["reminders"]
                if not (
                    int(row.get("id", 0)) == int(reminder_id)
                    and int(row.get("user_id", 0)) == int(user_id)
                )
            ]
            if len(store["reminders"]) != before:
                self._mark_dirty(guild_id, "reminders")
                return True
        return False

    # -- giveaways ---------------------------------------------------------
    def add_giveaway(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int | None,
        prize: str,
        winners: int,
        ends_at: str,
        host_id: int,
        required_role_id: int | None = None,
        required_level: int | None = None,
    ) -> int:
        row = {
            "id": self._next_id("giveaways"),
            "guild_id": int(guild_id),
            "channel_id": int(channel_id),
            "message_id": int(message_id) if message_id else None,
            "prize": prize,
            "winners": int(winners),
            "ends_at": ends_at,
            "host_id": int(host_id),
            "ended": 0,
            "required_role_id": int(required_role_id) if required_role_id else None,
            "required_level": int(required_level) if required_level else None,
            "created_at": iso(),
        }
        self._store(guild_id)["giveaways"].append(row)
        self._mark_dirty(guild_id, "giveaways")
        return int(row["id"])

    def giveaway(self, giveaway_id: int) -> dict[str, Any] | None:
        found = self._find_row(
            "giveaways", lambda row: int(row.get("id", 0)) == int(giveaway_id)
        )
        return dict(found[1]) if found is not None else None

    def giveaway_by_message(self, guild_id: int, message_id: int) -> dict[str, Any] | None:
        for row in self._store(guild_id)["giveaways"]:
            if row.get("message_id") is not None and int(row["message_id"]) == int(message_id):
                return dict(row)
        return None

    def set_giveaway_message(self, giveaway_id: int, message_id: int) -> None:
        found = self._find_row(
            "giveaways", lambda row: int(row.get("id", 0)) == int(giveaway_id)
        )
        if found is None:
            return
        guild_id, row = found
        row["message_id"] = int(message_id)
        self._mark_dirty(guild_id, "giveaways")

    def active_giveaways(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for store in self._stores.values():
            for row in store["giveaways"]:
                if not row.get("ended"):
                    rows.append(dict(row))
        rows.sort(key=lambda row: str(row.get("ends_at") or ""))
        return rows

    def due_giveaways(self, now_iso: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = [
            row
            for row in self.active_giveaways()
            if str(row.get("ends_at") or "") <= now_iso
        ]
        return rows[:limit]

    def end_giveaway(self, giveaway_id: int) -> None:
        found = self._find_row(
            "giveaways", lambda row: int(row.get("id", 0)) == int(giveaway_id)
        )
        if found is None:
            return
        guild_id, row = found
        if not row.get("ended"):
            row["ended"] = 1
            self._mark_dirty(guild_id, "giveaways")

    def enter_giveaway(self, giveaway_id: int, user_id: int) -> bool:
        existing = self._find_row(
            "giveaway_entries",
            lambda row: int(row.get("giveaway_id", 0)) == int(giveaway_id)
            and int(row.get("user_id", 0)) == int(user_id),
        )
        if existing is not None:
            return False
        giveaway = self._find_row(
            "giveaways", lambda row: int(row.get("id", 0)) == int(giveaway_id)
        )
        guild_id = giveaway[0] if giveaway is not None else self._storage_guild_id(None)
        self._stores.setdefault(guild_id, self._empty_store())["giveaway_entries"].append(
            {
                "giveaway_id": int(giveaway_id),
                "user_id": int(user_id),
                "created_at": iso(),
            }
        )
        self._mark_dirty(guild_id, "giveaway_entries")
        return True

    def giveaway_entries(self, giveaway_id: int) -> list[int]:
        entries: list[int] = []
        for store in self._stores.values():
            for row in store["giveaway_entries"]:
                if int(row.get("giveaway_id", 0)) == int(giveaway_id):
                    entries.append(int(row["user_id"]))
        return entries

    def giveaway_entry_count(self, giveaway_id: int) -> int:
        return len(self.giveaway_entries(giveaway_id))

    def pick_giveaway_winners(self, giveaway_id: int, count: int = 1) -> list[int]:
        entries = self.giveaway_entries(giveaway_id)
        if not entries:
            return []
        return random.sample(entries, k=min(count, len(entries)))

    # -- tickets -----------------------------------------------------------
    def add_ticket(self, guild_id: int, channel_id: int, user_id: int) -> int:
        row = {
            "id": self._next_id("tickets"),
            "guild_id": int(guild_id),
            "channel_id": int(channel_id),
            "user_id": int(user_id),
            "status": "open",
            "claimed_by": None,
            "created_at": iso(),
            "closed_at": None,
        }
        self._store(guild_id)["tickets"].append(row)
        self._mark_dirty(guild_id, "tickets")
        return int(row["id"])

    def ticket_by_channel(self, channel_id: int) -> dict[str, Any] | None:
        for store in self._stores.values():
            for row in store["tickets"]:
                if (
                    int(row.get("channel_id", 0)) == int(channel_id)
                    and row.get("status") == "open"
                ):
                    return dict(row)
        return None

    def tickets(self, guild_id: int, status: str = "open") -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self._store(guild_id)["tickets"]
            if row.get("status") == status
        ]
        rows.sort(key=lambda row: int(row.get("id", 0)))
        return rows

    def all_open_tickets(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for store in self._stores.values():
            for row in store["tickets"]:
                if row.get("status") == "open":
                    rows.append(dict(row))
        rows.sort(key=lambda row: int(row.get("id", 0)))
        return rows

    def close_ticket(self, channel_id: int, claimed_by: int | None = None) -> None:
        for guild_id, store in self._stores.items():
            for row in store["tickets"]:
                if (
                    int(row.get("channel_id", 0)) == int(channel_id)
                    and row.get("status") == "open"
                ):
                    row["status"] = "closed"
                    row["closed_at"] = iso()
                    if claimed_by is not None:
                        row["claimed_by"] = int(claimed_by)
                    self._mark_dirty(guild_id, "tickets")
                    return

    def claim_ticket(self, channel_id: int, user_id: int) -> None:
        for guild_id, store in self._stores.items():
            for row in store["tickets"]:
                if (
                    int(row.get("channel_id", 0)) == int(channel_id)
                    and row.get("status") == "open"
                ):
                    row["claimed_by"] = int(user_id)
                    self._mark_dirty(guild_id, "tickets")
                    return

    # -- scheduled actions -------------------------------------------------
    def add_scheduled(
        self,
        guild_id: int,
        kind: str,
        target_id: int,
        run_at: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        row = {
            "id": self._next_id("scheduled_actions"),
            "guild_id": int(guild_id),
            "kind": kind,
            "target_id": int(target_id),
            "payload": json.dumps(payload or {}),
            "run_at": run_at,
            "done": 0,
        }
        self._store(guild_id)["scheduled_actions"].append(row)
        self._mark_dirty(guild_id, "scheduled_actions")
        return int(row["id"])

    def due_scheduled(self, now_iso: str, limit: int = 25) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for store in self._stores.values():
            for row in store["scheduled_actions"]:
                if row.get("done"):
                    continue
                if str(row.get("run_at") or "") <= now_iso:
                    rows.append(dict(row))
        rows.sort(key=lambda row: str(row.get("run_at") or ""))
        return rows[:limit]

    def complete_scheduled(self, action_id: int) -> None:
        found = self._find_row(
            "scheduled_actions", lambda row: int(row.get("id", 0)) == int(action_id)
        )
        if found is None:
            return
        guild_id, row = found
        row["done"] = 1
        self._mark_dirty(guild_id, "scheduled_actions")

    # -- game stats --------------------------------------------------------
    def record_game(
        self,
        guild_id: int,
        user_id: int,
        game: str,
        result: str,
    ) -> None:
        column = {"win": "wins", "loss": "losses", "draw": "draws"}.get(result)
        if column is None:
            return
        store = self._store(guild_id)
        key = (int(user_id), str(game))
        record = store["game_stats"].setdefault(
            key, {"wins": 0, "losses": 0, "draws": 0, "plays": 0}
        )
        record[column] += 1
        record["plays"] += 1
        self._mark_dirty(guild_id, "game_stats")

    def game_stats(
        self, guild_id: int, user_id: int, game: str | None = None
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for (stored_user_id, stored_game), record in self._store(guild_id)[
            "game_stats"
        ].items():
            if int(stored_user_id) != int(user_id):
                continue
            if game is not None and str(stored_game) != str(game):
                continue
            rows.append(
                {
                    "user_id": int(stored_user_id),
                    "game": str(stored_game),
                    **record,
                }
            )
        rows.sort(key=lambda row: str(row.get("game") or ""))
        return rows

    def game_leaderboard(
        self, guild_id: int, game: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        rows = [
            {"user_id": user_id, "game": stored_game, **record}
            for (user_id, stored_game), record in self._store(guild_id)[
                "game_stats"
            ].items()
            if str(stored_game) == str(game)
        ]
        rows.sort(
            key=lambda row: (int(row.get("wins", 0)), int(row.get("plays", 0))),
            reverse=True,
        )
        return rows[:limit]

    # -- audit -------------------------------------------------------------
    def audit(
        self, guild_id: int | None, user_id: int | None, action: str, detail: str
    ) -> None:
        storage_id = self._storage_guild_id(guild_id)
        row = {
            "id": self._next_id("audit"),
            "guild_id": None if guild_id is None else int(guild_id),
            "user_id": None if user_id is None else int(user_id),
            "action": action,
            "detail": str(detail)[:4000],
            "created_at": iso(),
        }
        self._store(storage_id)["audit"].append(row)
        self._mark_dirty(storage_id, "audit")

    def recent_audit(
        self, guild_id: int | None, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if guild_id is None:
            for store in self._stores.values():
                rows.extend(dict(row) for row in store["audit"])
        else:
            rows = [
                dict(row)
                for row in self._store(guild_id)["audit"]
                if row.get("guild_id") == int(guild_id)
            ]
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        return rows[:limit]

    # -- starboard ---------------------------------------------------------
    def starboard_entry(self, source_message_id: int) -> dict[str, Any] | None:
        for store in self._stores.values():
            for row in store["starboard"]:
                if int(row.get("source_message_id", 0)) == int(source_message_id):
                    return dict(row)
        return None

    def add_starboard(
        self,
        source_message_id: int,
        guild_id: int,
        channel_id: int,
        starboard_message_id: int | None,
        author_id: int,
    ) -> None:
        self.remove_starboard(source_message_id)
        self._store(guild_id)["starboard"].append(
            {
                "source_message_id": int(source_message_id),
                "guild_id": int(guild_id),
                "channel_id": int(channel_id),
                "starboard_message_id": int(starboard_message_id)
                if starboard_message_id
                else None,
                "author_id": int(author_id),
                "created_at": iso(),
            }
        )
        self._mark_dirty(guild_id, "starboard")

    def set_starboard_message(
        self, source_message_id: int, starboard_message_id: int
    ) -> None:
        for guild_id, store in self._stores.items():
            for row in store["starboard"]:
                if int(row.get("source_message_id", 0)) == int(source_message_id):
                    row["starboard_message_id"] = int(starboard_message_id)
                    self._mark_dirty(guild_id, "starboard")
                    return

    def remove_starboard(self, source_message_id: int) -> None:
        for guild_id, store in self._stores.items():
            before = len(store["starboard"])
            store["starboard"] = [
                row
                for row in store["starboard"]
                if int(row.get("source_message_id", 0)) != int(source_message_id)
            ]
            if len(store["starboard"]) != before:
                self._mark_dirty(guild_id, "starboard")
                return

    def now(self) -> str:
        return iso(utcnow())


__all__ = ["Database", "CATEGORY_NAME", "DOMAIN_CHANNELS"]
