"""Shared helpers used across Jarvis.

Nothing in this module talks to the AI.  It is all Discord/text plumbing:
fuzzy name resolution, duration parsing, permission parsing, safe maths and
message chunking.
"""

from __future__ import annotations

import ast
import base64
import binascii
import difflib
import math
import operator
import random
import re
import secrets
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

import discord

# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).astimezone(UTC).isoformat()


def format_dt(dt: datetime | None, style: str = "R") -> str:
    if dt is None:
        return "—"
    return discord.utils.format_dt(dt.astimezone(UTC), style)


_DURATION_RE = re.compile(
    r"(?i)(\d+(?:\.\d+)?)\s*(weeks?|w|days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)"
)
_UNIT_SECONDS = {
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
}


def parse_duration(value: str | int | float | None, default_unit: str = "minutes") -> timedelta | None:
    """Parse ``10m``, ``2 hours``, ``1d 3h`` or a bare number.

    Bare numbers use ``default_unit``.  Returns ``None`` for nonsense.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return timedelta(seconds=float(value) * _UNIT_SECONDS.get(default_unit[:1], 60))

    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"forever", "permanent", "perm", "infinite", "inf"}:
        return timedelta(days=36500)

    matches = _DURATION_RE.findall(text)
    if matches:
        total = 0.0
        for amount, unit in matches:
            key = unit.lower().rstrip(".")
            total += float(amount) * _UNIT_SECONDS.get(key, 60)
        return timedelta(seconds=total) if total > 0 else None

    # "1:30" style (hours:minutes)
    if re.fullmatch(r"\d{1,3}:\d{2}", text):
        hours, minutes = (int(x) for x in text.split(":"))
        return timedelta(hours=hours, minutes=minutes)

    try:
        amount = float(text)
    except ValueError:
        return None
    if amount <= 0:
        return None
    return timedelta(seconds=amount * _UNIT_SECONDS.get(default_unit[:1], 60))


def human_delta(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "now"
    parts: list[str] = []
    for label, size in (
        ("week", 604800),
        ("day", 86400),
        ("hour", 3600),
        ("minute", 60),
        ("second", 1),
    ):
        if seconds >= size:
            amount, seconds = divmod(seconds, size)
            parts.append(f"{amount} {label}{'s' if amount != 1 else ''}")
        if len(parts) == 2:
            break
    return " ".join(parts)


def parse_time(value: str | None) -> datetime | None:
    """Parse a wide range of human time strings into an aware UTC datetime."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    lower = text.lower()
    if lower.startswith("in "):
        delta = parse_duration(lower[3:], default_unit="minutes")
        return utcnow() + delta if delta else None
    if lower in {"now", "today"}:
        return utcnow()

    # ISO-ish
    cleaned = text.replace("Z", "+00:00")
    for fmt in (
        None,  # fromisoformat
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ):
        try:
            dt = datetime.fromisoformat(cleaned) if fmt is None else datetime.strptime(cleaned, fmt)
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        except ValueError:
            continue

    # "tomorrow 5pm", "today at 18:30", "17:00"
    match = re.fullmatch(
        r"(?:(today|tomorrow)\s*(?:at\s*)?)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        lower,
    )
    if match:
        day, hour, minute, meridiem = match.groups()
        hour_i = int(hour)
        minute_i = int(minute or 0)
        if meridiem == "pm" and hour_i < 12:
            hour_i += 12
        if meridiem == "am" and hour_i == 12:
            hour_i = 0
        if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
            return None
        now = utcnow()
        target = now.replace(hour=hour_i, minute=minute_i, second=0, microsecond=0)
        if day == "tomorrow" or (day is None and target <= now):
            target += timedelta(days=1)
        return target

    delta = parse_duration(text)
    if delta:
        return utcnow() + delta
    return None


# ---------------------------------------------------------------------------
# Snowflakes / links
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"\d{15,25}")


def snowflake(text: Any) -> int | None:
    match = _ID_RE.search(str(text or ""))
    return int(match.group(0)) if match else None


def parse_message_link(text: str) -> tuple[int, int, int] | None:
    match = re.search(
        r"https?://(?:\w+\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)",
        str(text or ""),
    )
    if not match:
        return None
    guild_id, channel_id, message_id = (int(x) for x in match.groups())
    return guild_id, channel_id, message_id


def parse_channel_link(text: str) -> int | None:
    match = re.search(r"<#(\d+)>", str(text or ""))
    if match:
        return int(match.group(1))
    link = re.search(r"/channels/\d+/(\d+)", str(text or ""))
    return int(link.group(1)) if link else None


def parse_role_mention(text: str) -> int | None:
    match = re.search(r"<@&(\d+)>", str(text or ""))
    return int(match.group(1)) if match else None


def parse_user_mention(text: str) -> int | None:
    match = re.search(r"<@!?(\d+)>", str(text or ""))
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Fuzzy resolution
# ---------------------------------------------------------------------------


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def best_match(query: Any, items: Sequence[Any], names) -> Any | None:
    """Pick the best item for a fuzzy name query.

    Exact match, then prefix, then substring, then difflib similarity.
    """
    q = _norm(query).lstrip("@").lstrip("#")
    if not q or not items:
        return None

    def item_names(item) -> list[str]:
        try:
            raw = names(item)
        except Exception:
            return []
        return [str(n) for n in raw if n]

    for item in items:
        if any(_norm(n) == q for n in item_names(item)):
            return item
    for item in items:
        if any(_norm(n).startswith(q) for n in item_names(item)):
            return item
    for item in items:
        if any(q in _norm(n) for n in item_names(item)):
            return item

    scored: list[tuple[float, Any]] = []
    for item in items:
        score = max(
            (difflib.SequenceMatcher(None, q, _norm(n)).ratio() for n in item_names(item)),
            default=0.0,
        )
        if score >= 0.62:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1] if scored else None


def member_names(member: discord.abc.User) -> list[str]:
    values = [
        getattr(member, "name", ""),
        getattr(member, "display_name", ""),
        getattr(member, "nick", ""),
        str(member),
    ]
    return [v for v in values if v]


def resolve_member(guild: discord.Guild | None, value: Any) -> discord.Member | None:
    if guild is None or value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    mention = parse_user_mention(text)
    if mention:
        return guild.get_member(mention)
    if text.isdigit():
        return guild.get_member(int(text))

    # "username#1234" or plain name
    query = text.lstrip("@").strip()
    if "#" in query:
        query = query.rsplit("#", 1)[0]

    member = best_match(query, list(guild.members), member_names)
    if member is not None:
        return member

    # Also try users not in the cache by exact name via query_members (async
    # callers should use resolve_member_async when accuracy matters).
    return None


async def resolve_member_async(guild: discord.Guild | None, value: Any) -> discord.Member | None:
    member = resolve_member(guild, value)
    if member is not None or guild is None:
        return member
    query = str(value or "").lstrip("@").strip()
    if not query:
        return None
    try:
        results = await guild.query_members(query=query, limit=10)
    except (discord.HTTPException, AttributeError):
        return None
    return best_match(query, results, member_names)


def resolve_role(guild: discord.Guild | None, value: Any) -> discord.Role | None:
    if guild is None or value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    mention = parse_role_mention(text)
    if mention:
        return guild.get_role(mention)
    if text.isdigit():
        return guild.get_role(int(text))
    return best_match(text.lstrip("@").strip(), list(guild.roles), lambda r: [r.name])


def resolve_channel(
    guild: discord.Guild | None,
    value: Any,
    *,
    include_voice: bool = True,
    include_threads: bool = True,
) -> Any | None:
    if guild is None or value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    channel_id = parse_channel_link(text)
    if channel_id:
        return guild.get_channel_or_thread(channel_id)
    if text.isdigit():
        return guild.get_channel_or_thread(int(text))

    channels: list[Any] = list(guild.categories) + list(guild.text_channels)
    if include_voice:
        channels += list(guild.voice_channels) + list(guild.stage_channels)
    try:
        channels += list(guild.forums)
    except AttributeError:
        pass
    if include_threads:
        channels += list(guild.threads)
    return best_match(text.lstrip("#").strip(), channels, lambda c: [c.name])


def resolve_guild_channel(guild: discord.Guild | None, value: Any):
    return resolve_channel(guild, value)


def role_problem(guild: discord.Guild | None, role: discord.Role | None, actor: discord.Member | None = None) -> str | None:
    if guild is None:
        return "That only works inside a server, sir."
    if role is None:
        return "I can't find that role, sir."
    if role.is_default() or role.managed:
        return "I can't modify that role, sir."
    me = guild.me
    if me is not None and role >= me.top_role:
        return "That role is above mine, sir."
    if actor is not None and actor.id != guild.owner_id and role >= actor.top_role:
        return "That role is above your own, sir."
    return None


def hierarchy_problem(guild: discord.Guild | None, actor: Any, target: Any) -> str | None:
    """Return a reason the actor may not act on target, else None."""
    if guild is None:
        return "That only works inside a server, sir."
    me = guild.me
    if target is None:
        return "I couldn't find that user, sir."
    if target.id == guild.owner_id:
        return "I can't act on the server owner, sir."
    if me is not None and target.id == me.id:
        return "I can't act on myself, sir."
    if me is not None and getattr(target, "top_role", None) is not None and target.top_role >= me.top_role:
        return "That user outranks me, sir."
    if (
        actor is not None
        and getattr(actor, "id", None) != guild.owner_id
        and getattr(actor, "top_role", None) is not None
        and getattr(target, "top_role", None) is not None
        and target.top_role >= actor.top_role
        and getattr(actor, "id", None) != getattr(target, "id", None)
    ):
        return "That user outranks you, sir."
    return None


def can_moderate(actor: discord.Member | None, target: discord.Member | None) -> bool:
    return hierarchy_problem(actor.guild if actor else None, actor, target) is None


# ---------------------------------------------------------------------------
# Permissions / colours
# ---------------------------------------------------------------------------

PERMISSION_FLAGS: dict[str, str] = dict(discord.Permissions.VALID_FLAGS)
PERMISSION_ALIASES = {
    "admin": "administrator",
    "administrator": "administrator",
    "manage_server": "manage_guild",
    "manage_channels": "manage_channels",
    "manage_roles": "manage_roles",
    "manage_messages": "manage_messages",
    "moderate": "moderate_members",
    "timeout": "moderate_members",
    "kick": "kick_members",
    "ban": "ban_members",
    "nicknames": "manage_nicknames",
    "emoji": "manage_expressions",
    "expressions": "manage_expressions",
    "webhooks": "manage_webhooks",
    "threads": "manage_threads",
    "events": "manage_events",
    "voice": "move_members",
    "deafen": "deafen_members",
    "mute": "mute_members",
}


def parse_permissions(values: Any) -> discord.Permissions:
    """Build a Permissions object from a comma list of flag names."""
    perms = discord.Permissions.none()
    if values is None:
        return perms
    if isinstance(values, dict):
        for name, enabled in values.items():
            key = PERMISSION_ALIASES.get(str(name).lower(), str(name).lower())
            if key in PERMISSION_FLAGS and enabled:
                setattr(perms, key, True)
        return perms
    if isinstance(values, (list, tuple, set)):
        names = [str(v) for v in values]
    else:
        names = re.split(r"[,\s]+", str(values))

    for raw in names:
        name = raw.strip().lower().replace("-", "_")
        if not name:
            continue
        key = PERMISSION_ALIASES.get(name, name)
        if key in PERMISSION_FLAGS:
            setattr(perms, key, True)
    return perms


def permissions_to_names(perms: discord.Permissions, limit: int = 40) -> list[str]:
    names = [name for name, enabled in perms if enabled]
    names.sort()
    return names[:limit]


_COLOURS = {
    "red": discord.Colour.red,
    "green": discord.Colour.green,
    "blue": discord.Colour.blue,
    "blurple": discord.Colour.blurple,
    "purple": discord.Colour.purple,
    "magenta": discord.Colour.magenta,
    "orange": discord.Colour.orange,
    "gold": discord.Colour.gold,
    "yellow": discord.Colour.yellow,
    "teal": discord.Colour.teal,
    "dark_teal": discord.Colour.dark_teal,
    "dark_red": discord.Colour.dark_red,
    "dark_blue": discord.Colour.dark_blue,
    "dark_purple": discord.Colour.dark_purple,
    "dark_green": discord.Colour.dark_green,
    "dark_gold": discord.Colour.dark_gold,
    "dark_orange": discord.Colour.dark_orange,
    "grey": discord.Colour.greyple,
    "gray": discord.Colour.greyple,
    "black": discord.Colour(0x010101),
    "white": discord.Colour(0xFFFFFF),
    "random": lambda: discord.Colour.random(),
}


def parse_colour(value: Any, default: discord.Colour | None = None) -> discord.Colour:
    default = default or discord.Colour.blurple()
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, discord.Colour):
        return value
    text = str(value).strip().lower().lstrip("#")
    if text in _COLOURS:
        result = _COLOURS[text]
        return result() if callable(result) else result
    try:
        if text.startswith("0x"):
            text = text[2:]
        return discord.Colour(int(text, 16))
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def truncate(text: Any, length: int = 1024, suffix: str = "…") -> str:
    value = str(text or "")
    if len(value) <= length:
        return value
    return value[: max(0, length - len(suffix))] + suffix


def escape(text: Any) -> str:
    return discord.utils.escape_markdown(str(text or ""))


def split_message(text: str, limit: int = 1990) -> list[str]:
    """Split long text into Discord-sized chunks, preferring newlines."""
    text = str(text or "")
    if len(text) <= limit:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit * 0.5:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit * 0.5:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def progress_bar(value: int, maximum: int, width: int = 12, filled: str = "🟩", empty: str = "⬛") -> str:
    if maximum <= 0:
        return empty * width
    ratio = max(0.0, min(1.0, value / maximum))
    count = round(ratio * width)
    return filled * count + empty * (width - count)


def format_number(value: int | float) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def chunked(iterable: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]


def sanitize_channel_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\-_\s]", "", str(name).lower().replace(" ", "-"))
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    return cleaned[:95] or "channel"


def sanitize_role_name(name: str) -> str:
    return str(name).strip()[:100] or "role"


def sanitize_emoji_name(name: str) -> str:
    cleaned = re.sub(r"\W", "", str(name).lower())[:32]
    return cleaned if len(cleaned) >= 2 else (cleaned + "_").ljust(2, "_")


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "enable", "enabled"}


def parse_emoji(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"<a?:(\w+):(\d+)>", text)
    if match:
        return match.group(0)
    if len(text) <= 4:  # unicode emoji (roughly)
        return text
    return None


# ---------------------------------------------------------------------------
# Safe maths
# ---------------------------------------------------------------------------

_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_ALLOWED_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}

_ALLOWED_CALLS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "gcd": math.gcd,
    "hypot": math.hypot,
    "degrees": math.degrees,
    "radians": math.radians,
}


def safe_eval(expression: str) -> float | int:
    """Evaluate a small arithmetic expression without using eval()."""
    expression = str(expression).strip().replace("^", "**")
    if not expression:
        raise ValueError("Empty expression")
    if len(expression) > 300:
        raise ValueError("Expression too long")

    tree = ast.parse(expression, mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(node.op, ast.Pow) and (abs(left) > 1e6 or abs(right) > 1000):
                raise ValueError("Exponent too large")
            return _ALLOWED_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(node.op)](visit(node.operand))
        if isinstance(node, ast.Name) and node.id in _ALLOWED_NAMES:
            return _ALLOWED_NAMES[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_CALLS:
            if node.keywords:
                raise ValueError("Keyword arguments are not allowed")
            return _ALLOWED_CALLS[node.func.id](*(visit(arg) for arg in node.args))
        raise ValueError("Unsupported expression")

    return visit(tree)


# ---------------------------------------------------------------------------
# Misc encoders
# ---------------------------------------------------------------------------


def b64_encode(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def b64_decode(text: str) -> str:
    return base64.b64decode(text, validate=True).decode(errors="replace")


def url_encode(text: str) -> str:
    return urllib.parse.quote(text)


def url_decode(text: str) -> str:
    return urllib.parse.unquote(text)


def hex_encode(text: str) -> str:
    return text.encode().hex()


def hex_decode(text: str) -> str:
    return bytes.fromhex(text).decode(errors="replace")


def password(length: int = 20) -> str:
    length = max(8, min(int(length), 128))
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*()-_=+[]{}"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def new_uuid() -> str:
    return str(uuid.uuid4())


def random_choice(items: Sequence[str], count: int = 1) -> list[str]:
    return random.sample(list(items), k=min(count, len(items)))


def snowflake_timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(((value >> 22) + 1420070400000) / 1000, tz=UTC)


def plural(amount: int, singular: str, plural_form: str | None = None) -> str:
    return singular if abs(amount) == 1 else (plural_form or singular + "s")


# ---------------------------------------------------------------------------
# Discord helpers
# ---------------------------------------------------------------------------


async def safe_send(destination, *, content: str | None = None, embed: discord.Embed | None = None, **kwargs) -> Any:
    """Send without raising; returns the message or None."""
    try:
        kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
        return await destination.send(content=content, embed=embed, **kwargs)
    except (discord.HTTPException, AttributeError, TypeError):
        return None


def empty_embed(description: str = "", colour: discord.Colour | None = None) -> discord.Embed:
    return discord.Embed(description=description or None, colour=colour or discord.Colour.blurple())


def field_value(value: Any, limit: int = 1024) -> str:
    return truncate(value if str(value).strip() else "—", limit)


def yes_no(value: bool) -> str:
    return "✅ Yes" if value else "❌ No"


def channel_mention(channel: Any) -> str:
    return getattr(channel, "mention", f"`{getattr(channel, 'name', channel)}`")


def user_mention(user: Any) -> str:
    return getattr(user, "mention", f"`{user}`")


def fmt_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


async def fetch_image_bytes(
    session,
    url: str,
    *,
    max_bytes: int = 8 * 1024 * 1024,
) -> tuple[bytes, str] | None:
    """Download an image URL and return (bytes, mime)."""
    if not re.match(r"https?://", str(url or "")):
        return None
    try:
        async with session.get(url, timeout=15) as response:
            if response.status != 200:
                return None
            length = int(response.headers.get("Content-Length", 0) or 0)
            if length and length > max_bytes:
                return None
            data = await response.content.read(max_bytes + 1)
            if len(data) > max_bytes:
                return None
            mime = response.headers.get("Content-Type", "image/png").split(";")[0]
            return data, mime
    except Exception:
        return None


def b64url(data: bytes) -> str:
    return base64.b64encode(data).decode()


def from_b64url(text: str) -> bytes:
    return base64.b64decode(text)


def try_json(text: str) -> Any:
    import json

    try:
        return json.loads(text)
    except Exception:
        return None


def strip_quotes(text: str) -> str:
    value = str(text or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


# Handy re-exports for action modules.
__all__ = [
    "UTC",
    "utcnow",
    "iso",
    "format_dt",
    "parse_duration",
    "human_delta",
    "parse_time",
    "snowflake",
    "parse_message_link",
    "parse_channel_link",
    "parse_role_mention",
    "parse_user_mention",
    "best_match",
    "member_names",
    "resolve_member",
    "resolve_member_async",
    "resolve_role",
    "resolve_channel",
    "role_problem",
    "hierarchy_problem",
    "can_moderate",
    "PERMISSION_FLAGS",
    "parse_permissions",
    "permissions_to_names",
    "parse_colour",
    "truncate",
    "escape",
    "split_message",
    "progress_bar",
    "format_number",
    "chunked",
    "sanitize_channel_name",
    "sanitize_role_name",
    "sanitize_emoji_name",
    "parse_bool",
    "parse_emoji",
    "safe_eval",
    "b64_encode",
    "b64_decode",
    "url_encode",
    "url_decode",
    "hex_encode",
    "hex_decode",
    "password",
    "new_uuid",
    "random_choice",
    "snowflake_timestamp",
    "plural",
    "safe_send",
    "empty_embed",
    "field_value",
    "yes_no",
    "channel_mention",
    "user_mention",
    "fmt_bytes",
    "fetch_image_bytes",
    "b64url",
    "from_b64url",
    "try_json",
    "strip_quotes",
]
