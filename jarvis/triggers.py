"""Wake-word detection — Jarvis answers to well over a hundred names.

The matcher is built from three composable groups (greeting + name + suffix),
plus titles and a handful of special phrases.  That gives >1,000 literal ways to
call Jarvis without maintaining a giant hand-written list, and ``WAKE_PHRASES``
still exposes the full set for documentation and tests.
"""

from __future__ import annotations

import random
import re

# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

PREFIXES: tuple[str, ...] = (
    "",
    "hey",
    "hi",
    "hello",
    "yo",
    "oi",
    "oy",
    "sup",
    "what's up",
    "whats up",
    "wassup",
    "aye",
    "ay",
    "ayo",
    "ey",
    "eh",
    "ok",
    "okay",
    "alright",
    "right",
    "listen",
    "listen up",
    "wake up",
    "wake",
    "you there",
    "are you there",
    "you awake",
    "come in",
    "attention",
    "excuse me",
    "pardon me",
    "psst",
    "ahem",
    "greetings",
    "howdy",
    "hola",
    "hallo",
    "good morning",
    "good afternoon",
    "good evening",
    "good night",
    "morning",
    "afternoon",
    "evening",
    "hey there",
    "hello there",
    "hi there",
    "oi oi",
    "quick",
    "now",
    "so",
    "um",
    "uh",
    "well",
)

NAMES: tuple[str, ...] = (
    "jarvis",
    "jarv",
    "jarvi",
    "jarvy",
    "jervis",
    "jeeves",
    "alfred",
    "butler",
    "computer",
    "assistant",
    "ai",
    "bot",
    "jay",
    "j-man",
    "jbot",
    "helper",
    "jarvis ai",
    "jarvis bot",
    "j.a.r.v.i.s",
    "j.a.r.v.i.s.",
)

TITLES: tuple[str, ...] = (
    "mr",
    "mister",
    "sir",
    "boss",
    "chief",
    "captain",
    "commander",
    "agent",
    "doctor",
    "dr",
)

SUFFIXES: tuple[str, ...] = ("ai", "bot", "assistant", "please")

SPECIALS: tuple[str, ...] = (
    "at your service",
    "jarvis online",
    "system online",
    "system ready",
    "hey buddy",
    "hey mate",
    "hey friend",
    "hello friend",
    "anyone there",
    "is anyone there",
    "jarvis are you there",
    "hey jarvis are you there",
    "jarvis you there",
    "wake up jarvis",
    "wake up jarv",
    "listen here jarvis",
    "look alive jarvis",
    "report jarvis",
    "status report jarvis",
    "jarvis report",
    "good  morning jarvis",
    "top of the morning jarvis",
    "you up jarvis",
    "you there jarv",
    "hey there jarv",
    "hello jarvis",
    "hi jarvis",
    "hey jarvis",
    "yo jarvis",
)


def _build_phrases() -> frozenset[str]:
    phrases: set[str] = set(SPECIALS)
    for name in NAMES:
        phrases.add(name)
        for suffix in SUFFIXES:
            phrases.add(f"{name} {suffix}")
        for title in TITLES:
            phrases.add(f"{title} {name}")
        for prefix in PREFIXES:
            if prefix:
                phrases.add(f"{prefix} {name}")
    return frozenset(p.strip() for p in phrases if p.strip())


WAKE_PHRASES: frozenset[str] = _build_phrases()


def wake_phrase_count() -> int:
    return len(WAKE_PHRASES)


def wake_phrase_examples(limit: int = 24) -> list[str]:
    preferred = [
        "jarvis",
        "hey jarvis",
        "hi jarvis",
        "hello jarvis",
        "yo jarvis",
        "oi jarvis",
        "ok jarvis",
        "listen up jarvis",
        "wake up jarvis",
        "good morning jarvis",
        "mr jarvis",
        "sir jarvis",
        "jarv",
        "jarvi",
        "jarvy",
        "jeeves",
        "alfred",
        "computer",
        "assistant",
        "jarvis bot",
        "j.a.r.v.i.s",
        "hey there jarvis",
        "psst jarvis",
        "attention jarvis",
    ]
    remaining = [p for p in sorted(WAKE_PHRASES) if p not in preferred]
    return (preferred + remaining)[:limit]


# ---------------------------------------------------------------------------
# Regex construction
# ---------------------------------------------------------------------------


def _alternation(values) -> str:
    return "|".join(re.escape(v) for v in sorted({v for v in values if v}, key=len, reverse=True))


_PREFIX_ALT = _alternation(PREFIXES)
_NAME_ALT = _alternation(NAMES)
_TITLE_ALT = _alternation(TITLES)
_SPECIAL_ALT = _alternation(SPECIALS)

# A wake phrase at the very start of a message.  The optional greeting may be
# separated by spaces or punctuation; the name may be followed by a suffix.
WAKE_RE = re.compile(
    rf"^\s*(?:"
    rf"(?:(?:{_PREFIX_ALT})[\s,]+)?(?:{_NAME_ALT})(?:[\s,]+(?:ai|bot|assistant|please))?"
    rf"|(?:{_TITLE_ALT})\.?[\s,]+(?:{_NAME_ALT})"
    rf"|(?:{_SPECIAL_ALT})"
    rf")\b[\s,.:;!?\-–—]*",
    re.IGNORECASE,
)

# A second, looser form for things like "hey, jarvis!" where punctuation after
# the greeting is unusual.  The main regex already covers most of these.
LOOSE_WAKE_RE = re.compile(
    rf"^\s*(?:{_PREFIX_ALT})[\s,.:;!?\-–—]+(?:{_NAME_ALT})\b[\s,.:;!?\-–—]*",
    re.IGNORECASE,
)

# Clear moderation imperatives can be treated as requests without a wake word,
# but only when the author has the matching permission (checked by bot.py).
AUTO_ACTION_RE = re.compile(
    r"^\s*(?:"
    r"ban|unban|kick|mute|unmute|timeout|warn|purge|clear|delete|lock|unlock|slowmode|"
    r"nuke|rename|move|pin|unpin|create|give|remove|add"
    r")\s+(?!(?:the|a|an|this|that|it|me|him|her|them|my|your)\b)\S+",
    re.IGNORECASE,
)

EMPTY_REPLIES = (
    "At your service, sir. What do you need?",
    "Jarvis here. How may I help?",
    "Yes, sir? I'm listening.",
    "Standing by, sir. What can I do for you?",
    "At your service. What's the assignment?",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def match_wake(text: str) -> tuple[str, str] | None:
    """Return ``(matched_phrase, remainder)`` when text starts with a wake phrase."""
    value = str(text or "")
    match = WAKE_RE.match(value) or LOOSE_WAKE_RE.match(value)
    if not match:
        return None
    phrase = match.group(0).strip(" \t,.:;!?-–—\n")
    remainder = value[match.end() :].strip(" \t,.:;!?-–—\n")
    return phrase, remainder


def has_wake_phrase(text: str) -> bool:
    return match_wake(text) is not None


def strip_trigger(text: str, bot_user=None) -> str:
    """Remove a bot mention and any wake phrase from the start of a message."""
    value = str(text or "")
    if bot_user is not None:
        value = re.sub(rf"<@!?{bot_user.id}>", "", value)
    match = WAKE_RE.match(value) or LOOSE_WAKE_RE.match(value)
    if match:
        value = value[match.end() :]
    return value.strip(" \t,.:;!?-–—\n")


def looks_like_auto_action(text: str) -> bool:
    return AUTO_ACTION_RE.match(str(text or "")) is not None


def is_reply_to_bot(message, bot_user) -> bool:
    reference = getattr(message, "reference", None)
    if reference is None or bot_user is None:
        return False
    resolved = getattr(reference, "resolved", None)
    author = getattr(resolved, "author", None)
    return bool(author is not None and getattr(author, "id", None) == bot_user.id)


def should_handle(message, bot_user, *, auto_action: bool = True) -> bool:
    """True when a guild message is clearly addressed to Jarvis."""
    if getattr(message.author, "bot", False):
        return False

    content = str(getattr(message, "content", "") or "").strip()
    if getattr(message, "guild", None) is None:
        return True  # DMs are always a conversation with Jarvis

    me = getattr(message.guild, "me", None)
    if me is not None and me in getattr(message, "mentions", []):
        return True
    if has_wake_phrase(content):
        return True
    if is_reply_to_bot(message, bot_user):
        return True

    if auto_action and looks_like_auto_action(content):
        author = getattr(message, "author", None)
        perms = getattr(author, "guild_permissions", None)
        if perms is not None and (
            perms.administrator
            or perms.moderate_members
            or perms.kick_members
            or perms.ban_members
            or perms.manage_messages
            or perms.manage_channels
            or perms.manage_roles
        ):
            return True
    return False


def empty_prompt_reply() -> str:
    return random.choice(EMPTY_REPLIES)
