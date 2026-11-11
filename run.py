#!/usr/bin/env python3
"""Start Jarvis v2.

Configuration lives in ``.env`` (see ``.env.example``).  Environment variables
always override the file.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import discord

from jarvis.bot import JarvisBot
from jarvis.config import load_settings, require_runtime_settings
from jarvis.logging_setup import setup_logging

log = logging.getLogger("jarvis.run")


async def main() -> int:
    settings = load_settings()
    setup_logging(settings.log_level)

    try:
        require_runtime_settings(settings)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    log.info(
        "Starting Jarvis v2 with %d configured provider(s); data store: Discord "
        "channels (legacy SQLite migration source: %s)",
        len(settings.enabled_providers),
        settings.database_path,
    )
    bot = JarvisBot(settings)
    try:
        await bot.start(settings.token)
    except discord.LoginFailure:
        log.error("Discord rejected the token. Check DISCORD_TOKEN / token.txt.")
        return 1
    except discord.PrivilegedIntentsRequired:
        log.error(
            "Privileged intents are disabled. In the Discord developer portal, enable "
            "SERVER MEMBERS INTENT (and PRESENCE INTENT only if ENABLE_PRESENCES=true)."
        )
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if not bot.is_closed():
            await bot.close()
    return 0


def run() -> None:
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        log.info("Interrupted; shutting down.")


if __name__ == "__main__":
    run()
