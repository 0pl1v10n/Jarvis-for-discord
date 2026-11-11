"""Information lookups, AI helpers, web search, weather, and small utilities."""

from __future__ import annotations

import json
import random
import secrets
import time
import uuid as uuidlib
from datetime import datetime, timedelta

import discord

from ..ai.tools import REGISTRY, Risk, action, tool
from ..util import (
    b64_decode,
    b64_encode,
    format_dt,
    format_number,
    hex_decode,
    hex_encode,
    human_delta,
    parse_duration,
    parse_time,
    permissions_to_names,
    safe_eval,
    snowflake_timestamp,
    truncate,
    url_decode,
    url_encode,
    utcnow,
)
from .helpers import (
    channel_embed,
    fail,
    member_embed,
    message_embed,
    ok,
    role_embed,
    sent,
    server_embed,
)

INFO_ACTIONS = {
    "member": action(Risk.SAFE),
    "channel": action(Risk.SAFE),
    "role": action(Risk.SAFE),
    "server": action(Risk.SAFE),
    "avatar": action(Risk.SAFE),
    "banner": action(Risk.SAFE),
    "roles": action(Risk.SAFE),
    "emojis": action(Risk.SAFE),
    "boosters": action(Risk.SAFE),
    "bans": action(Risk.LOW, permission="ban_members"),
    "invites": action(Risk.LOW, permission="manage_guild"),
    "audit": action(Risk.MEDIUM, permission="view_audit_log"),
    "permissions": action(Risk.SAFE),
    "message": action(Risk.SAFE),
    "id": action(Risk.SAFE),
}

INFO_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(INFO_ACTIONS)},
        "user": {"type": "string"},
        "channel": {"type": "string"},
        "role": {"type": "string"},
        "message": {"type": "string"},
        "limit": {"type": "integer"},
        "query": {"type": "string"},
    },
    "required": ["action"],
}


@tool(
    "get_info",
    "Look up members, channels, roles, the server, avatars, banners, roles, emojis, boosters, "
    "bans, invites, audit-log entries, permissions, or message details.",
    INFO_SCHEMA,
    category="utility",
    actions=INFO_ACTIONS,
)
async def get_info(ctx, args):
    operation = str(args["action"]).lower()

    if operation == "server":
        await ctx.say(embed=server_embed(ctx.guild))
        return sent("Showed the server.")

    if operation in {"member", "avatar", "banner", "permissions"}:
        if args.get("user"):
            member, error = await _member(ctx, args.get("user"))
            if error:
                return error
        else:
            member = ctx.member or ctx.author
        if not isinstance(member, discord.Member):
            return fail("That only works inside a server, sir.")
        if operation == "member":
            await ctx.say(embed=member_embed(member))
            return sent("Showed the member.")
        if operation == "avatar":
            embed = discord.Embed(title=f"🖼️ {member.display_name}", colour=member.colour)
            embed.set_image(url=member.display_avatar.url)
            if member.display_banner:
                embed.set_footer(text="Banner available too")
            await ctx.say(embed=embed)
            return sent("Showed the avatar.")
        if operation == "banner":
            banner = member.display_banner
            if banner is None:
                return fail(f"{member.display_name} has no banner, sir.")
            embed = discord.Embed(title=f"🎏 {member.display_name}", colour=member.colour)
            embed.set_image(url=banner.url)
            await ctx.say(embed=embed)
            return sent("Showed the banner.")
        names = permissions_to_names(member.guild_permissions, limit=60)
        await ctx.say(
            f"🛡️ **{member.display_name}** — {'administrator' if member.guild_permissions.administrator else 'no administrator'}\n"
            + (", ".join(names) if names else "No explicit permissions.")
        )
        return sent("Showed permissions.")

    if operation == "channel":
        channel = ctx.resolve_channel(args.get("channel")) if args.get("channel") else ctx.channel
        if channel is None:
            return fail("I couldn't find that channel, sir.")
        await ctx.say(embed=channel_embed(channel))
        return sent("Showed the channel.")

    if operation == "role":
        role = ctx.resolve_role(args.get("role"))
        if role is None:
            return fail("I couldn't find that role, sir.")
        await ctx.say(embed=role_embed(role))
        return sent("Showed the role.")

    if operation == "roles":
        roles = [r for r in reversed(ctx.guild.roles) if not r.is_default()]
        lines = [f"🎭 {role.mention} — {len(role.members)} member(s), position {role.position}" for role in roles[:60]]
        await ctx.say("\n".join(lines) or "No roles, sir.")
        return sent(f"Listed {len(roles)} roles.")

    if operation == "emojis":
        emojis = [f"{e} `:{e.name}:`" for e in ctx.guild.emojis[:80]]
        stickers = [f"🏷️ {s.name}" for s in ctx.guild.stickers[:40]]
        await ctx.say(
            ("**Emojis:** " + " ".join(emojis) if emojis else "No custom emojis.")
            + ("\n**Stickers:** " + ", ".join(stickers) if stickers else "")
        )
        return sent("Listed expressions.")

    if operation == "boosters":
        boosters = ctx.guild.premium_subscribers
        if not boosters:
            await ctx.say("Nobody is boosting the server, sir.")
            return sent("No boosters.")
        lines = [
            f"💎 {member.mention} — since {format_dt(member.premium_since)}"
            for member in boosters[:50]
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(boosters)} boosters.")

    if operation == "bans":
        limit = max(1, min(1000, int(args.get("limit") or 50)))
        bans = [entry async for entry in ctx.guild.bans(limit=limit)]
        if not bans:
            await ctx.say("There are no bans, sir.")
            return sent("No bans.")
        lines = [
            f"🔨 **{entry.user}** (`{entry.user.id}`) — {truncate(entry.reason or 'No reason', 100)}"
            for entry in bans[:50]
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(bans)} bans.")

    if operation == "invites":
        invites = await ctx.guild.invites()
        if not invites:
            await ctx.say("There are no invites, sir.")
            return sent("No invites.")
        lines = [
            f"🔗 `{i.code}` — {i.uses or 0} uses, {i.inviter}"
            for i in invites[:50]
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(invites)} invites.")

    if operation == "audit":
        limit = max(1, min(100, int(args.get("limit") or 15)))
        entries = [entry async for entry in ctx.guild.audit_logs(limit=limit)]
        if not entries:
            await ctx.say("The audit log is empty, sir.")
            return sent("No audit entries.")
        lines = [
            f"`{entry.id}` · **{entry.action.name.replace('_', ' ')}** by {entry.user} "
            f"on {entry.target or '—'} — {truncate(entry.reason or 'no reason', 80)}"
            for entry in entries
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(entries)} audit entries.")

    if operation == "message":
        from .helpers import message_or_error

        message, error = await message_or_error(ctx, args.get("message"))
        if error:
            return error
        await ctx.say(embed=message_embed(message))
        return sent("Showed the message.")

    if operation == "id":
        value = str(args.get("query") or args.get("message") or "").strip()
        target_id = snowflake_timestamp(int(value)) if value.isdigit() and len(value) >= 17 else None
        if target_id is None:
            return fail("Give me a Discord snowflake ID, sir.")
        embed = discord.Embed(title="🆔 Snowflake", colour=discord.Colour.blurple())
        embed.add_field(name="ID", value=f"`{value}`", inline=True)
        embed.add_field(name="Created", value=format_dt(target_id), inline=True)
        embed.add_field(name="Timestamp", value=target_id.isoformat(), inline=False)
        await ctx.say(embed=embed)
        return sent("Decoded the ID.")

    return fail(f"I don't know how to look up `{operation}`, sir.")


async def _member(ctx, value):
    member = await ctx.resolve_member_async(value)
    if member is None:
        return None, fail(f"I couldn't find a member matching “{value}”, sir.")
    return member, None


UTIL_ACTIONS = {
    "ping": action(Risk.SAFE),
    "uptime": action(Risk.SAFE),
    "time": action(Risk.SAFE),
    "timestamp": action(Risk.SAFE),
    "math": action(Risk.SAFE),
    "base64": action(Risk.SAFE),
    "unbase64": action(Risk.SAFE),
    "hex": action(Risk.SAFE),
    "unhex": action(Risk.SAFE),
    "urlencode": action(Risk.SAFE),
    "urldecode": action(Risk.SAFE),
    "password": action(Risk.SAFE),
    "uuid": action(Risk.SAFE),
    "choose": action(Risk.SAFE),
    "random": action(Risk.SAFE),
    "weather": action(Risk.SAFE),
    "define": action(Risk.SAFE),
    "search": action(Risk.SAFE),
    "translate": action(Risk.SAFE),
    "summarize": action(Risk.SAFE),
    "rewrite": action(Risk.SAFE),
    "explain": action(Risk.SAFE),
    "ask": action(Risk.SAFE),
    "help": action(Risk.SAFE),
    "status": action(Risk.SAFE),
    "channels": action(Risk.SAFE),
}

UTIL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(UTIL_ACTIONS)},
        "text": {"type": "string", "description": "Input text / expression / query"},
        "query": {"type": "string", "description": "Search query"},
        "city": {"type": "string", "description": "City for weather"},
        "language": {"type": "string", "description": "Target language"},
        "count": {"type": "integer", "description": "Number of results, choices, or password length"},
        "minimum": {"type": "integer"},
        "maximum": {"type": "integer"},
        "options": {"type": "array", "items": {"type": "string"}},
        "channel": {"type": "string", "description": "Channel to summarize"},
        "minutes": {"type": "integer", "description": "Time offset for timestamps"},
        "encoding": {"type": "string", "description": "Encoding hint"},
    },
    "required": ["action"],
}


@tool(
    "utility",
    "Ping/uptime, time stamps, maths, encoders, passwords, UUIDs, random choices, weather, "
    "dictionary, web search, translation, summaries, rewriting/explaining, free-form AI, help, "
    "model status, and channel lists.",
    UTIL_SCHEMA,
    category="utility",
    actions=UTIL_ACTIONS,
)
async def utility(ctx, args):
    operation = str(args["action"]).lower()

    if operation == "ping":
        start = time.perf_counter()
        message = await ctx.say("🏓 Pinging…")
        elapsed = (time.perf_counter() - start) * 1000
        if message is not None:
            try:
                await message.edit(
                    content=f"🏓 **Pong!** Gateway: {ctx.bot.latency * 1000:.0f} ms · Round-trip: {elapsed:.0f} ms"
                )
            except discord.HTTPException:
                pass
        return sent("Pong.")

    if operation == "uptime":
        started = getattr(ctx.bot, "started_at", utcnow())
        delta = utcnow() - started
        await ctx.say(f"⏱️ I've been online for **{human_delta(delta)}**.")
        return sent("Showed uptime.")

    if operation == "time":
        now = utcnow()
        embed = discord.Embed(title="🕰️ Time", colour=discord.Colour.blurple())
        embed.add_field(name="UTC", value=now.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        embed.add_field(name="Unix", value=f"`{int(now.timestamp())}`", inline=True)
        embed.add_field(name="Local (your client)", value=format_dt(now), inline=False)
        await ctx.say(embed=embed)
        return sent("Showed the time.")

    if operation == "timestamp":
        when = parse_time(args.get("text") or args.get("query"))
        if when is None:
            minutes = max(-525600, min(525600, int(args.get("minutes") or 0)))
            when = utcnow() + timedelta(minutes=minutes)
        await ctx.say(
            f"🕰️ <t:{int(when.timestamp())}:F> — relative <t:{int(when.timestamp())}:R> "
            f"(`{when.isoformat()}`)"
        )
        return sent("Created a timestamp.")

    if operation == "math":
        expression = str(args.get("text") or args.get("query") or "").strip()
        if not expression:
            return fail("Give me an expression to evaluate, sir.")
        try:
            result = safe_eval(expression)
        except Exception as exc:
            return fail(f"I couldn't evaluate that: {exc}")
        await ctx.say(f"🧮 `{truncate(expression, 400)}` = **{result}**")
        return sent("Calculated.")

    if operation in {"base64", "unbase64", "hex", "unhex", "urlencode", "urldecode"}:
        text = str(args.get("text") or args.get("query") or "")
        if not text:
            return fail("Give me some text, sir.")
        try:
            if operation == "base64":
                output = b64_encode(text)
            elif operation == "unbase64":
                output = b64_decode(text)
            elif operation == "hex":
                output = hex_encode(text)
            elif operation == "unhex":
                output = hex_decode(text)
            elif operation == "urlencode":
                output = url_encode(text)
            else:
                output = url_decode(text)
        except Exception as exc:
            return fail(f"That conversion failed: {exc}")
        await ctx.say(f"```\n{truncate(output, 1900)}\n```")
        return sent("Converted.")

    if operation == "password":
        length = max(6, min(128, int(args.get("count") or 20)))
        alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*-_=+"
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        await ctx.say(f"🔐 ||`{value}`||")
        return sent("Generated a password.")

    if operation == "uuid":
        await ctx.say(f"🆔 `{uuidlib.uuid4()}`")
        return sent("Generated a UUID.")

    if operation == "choose":
        options = args.get("options") or []
        if isinstance(options, str):
            options = [part.strip() for part in options.split(",") if part.strip()]
        if not options:
            return fail("Give me at least one option, sir.")
        await ctx.say(f"🎯 I choose: **{random.choice(list(options))}**")
        return sent("Chose an option.")

    if operation == "random":
        minimum = int(args.get("minimum") if args.get("minimum") is not None else 1)
        maximum = int(args.get("maximum") if args.get("maximum") is not None else 100)
        if maximum < minimum:
            minimum, maximum = maximum, minimum
        await ctx.say(f"🎲 **{random.randint(minimum, maximum)}** (range {minimum}–{maximum})")
        return sent("Rolled a number.")

    if operation == "weather":
        city = str(args.get("city") or args.get("query") or args.get("text") or "").strip()
        if not city:
            return fail("Tell me a city, sir.")
        try:
            async with ctx.bot.session.get(
                f"https://wttr.in/{city}?format=j1", timeout=15
            ) as response:
                data = await response.json() if response.status == 200 else None
        except Exception:
            data = None
        if not data:
            return fail("I couldn't fetch the weather, sir.")
        current = (data.get("current_condition") or [{}])[0]
        area = (data.get("nearest_area") or [{}])[0]
        area_name = ", ".join(
            part[0]["value"] for part in [area.get("areaName", []), area.get("country", [])] if part
        )
        embed = discord.Embed(
            title=f"🌤️ Weather in {area_name or city.title()}",
            colour=discord.Colour.blue(),
        )
        embed.add_field(name="Condition", value=(current.get("weatherDesc") or [{"value": "—"}])[0]["value"], inline=True)
        embed.add_field(name="Temperature", value=f"{current.get('temp_C', '?')}°C / {current.get('temp_F', '?')}°F", inline=True)
        embed.add_field(name="Feels like", value=f"{current.get('FeelsLikeC', '?')}°C", inline=True)
        embed.add_field(name="Humidity", value=f"{current.get('humidity', '?')}%", inline=True)
        embed.add_field(name="Wind", value=f"{current.get('windspeedKmph', '?')} km/h", inline=True)
        embed.add_field(name="Visibility", value=f"{current.get('visibility', '?')} km", inline=True)
        await ctx.say(embed=embed)
        return sent("Showed the weather.")

    if operation == "define":
        word = str(args.get("text") or args.get("query") or "").strip()
        if not word:
            return fail("Tell me which word to define, sir.")
        try:
            async with ctx.bot.session.get(
                f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}", timeout=15
            ) as response:
                data = await response.json() if response.status == 200 else None
        except Exception:
            data = None
        if not isinstance(data, list) or not data:
            return fail(f"I couldn't find a definition for “{word}”, sir.")
        entry = data[0]
        meanings = entry.get("meanings") or []
        embed = discord.Embed(title=f"📖 {entry.get('word', word)}", colour=discord.Colour.teal())
        if entry.get("phonetic"):
            embed.description = entry["phonetic"]
        for meaning in meanings[:4]:
            definitions = meaning.get("definitions") or []
            if definitions:
                embed.add_field(
                    name=meaning.get("partOfSpeech", "definition").title(),
                    value=truncate(definitions[0].get("definition", "—"), 1024),
                    inline=False,
                )
        await ctx.say(embed=embed)
        return sent("Showed the definition.")

    if operation == "search":
        query = str(args.get("query") or args.get("text") or "").strip()
        if not query:
            return fail("Tell me what to search for, sir.")
        if not ctx.settings.web_search_enabled:
            return fail("Web search is disabled, sir.")
        results = await _web_search(ctx, query, max(1, min(10, int(args.get("count") or 5))))
        if not results:
            return fail("My search returned nothing useful, sir.")
        embed = discord.Embed(title=f"🔎 {truncate(query, 200)}", colour=discord.Colour.blurple())
        for title, url, snippet in results:
            embed.add_field(
                name=truncate(title or url, 256),
                value=f"{truncate(snippet, 300)}\n{url}",
                inline=False,
            )
        await ctx.say(embed=embed)
        return sent("Showed search results.")

    if operation in {"translate", "summarize", "rewrite", "explain", "ask"}:
        text = str(args.get("text") or args.get("query") or "").strip()
        if operation == "summarize" and args.get("channel"):
            channel = ctx.resolve_channel(args.get("channel")) or ctx.channel
            lines = []
            async for message in channel.history(limit=80):
                if message.author.bot and message.author.id == ctx.bot.user.id:
                    continue
                lines.append(f"{message.author.display_name}: {message.content}")
            text = "\n".join(reversed(lines))
        if not text:
            return fail("Give me some text to work with, sir.")
        prompts = {
            "translate": f"Translate the following text into {args.get('language') or 'English'}. "
            f"Return only the translation:\n\n{text}",
            "summarize": "Summarise the following conversation or text into concise bullet points. "
            f"Do not invent facts:\n\n{text}",
            "rewrite": f"Rewrite the following text to be clearer and more polished:\n\n{text}",
            "explain": f"Explain the following clearly and concisely:\n\n{text}",
            "ask": text,
        }
        try:
            answer = await ctx.agent.simple(prompts[operation], max_tokens=1200)
        except Exception as exc:
            return fail(f"The AI could not answer that: {truncate(exc, 200)}")
        await ctx.send_long(answer, reply=False)
        return sent("Answered.")

    if operation == "help":
        categories = REGISTRY.categories()
        embed = discord.Embed(
            title="🤵 Jarvis — command catalogue",
            description=(
                "Talk to me naturally. Wake me with “Jarvis”, “hey jarvis”, or any of "
                f"{__import__('jarvis.triggers', fromlist=['wake_phrase_count']).wake_phrase_count():,} phrases. "
                "I can also be mentioned or replied to."
            ),
            colour=discord.Colour.blurple(),
        )
        for category, specs in sorted(categories.items()):
            embed.add_field(
                name=category.title(),
                value=truncate(", ".join(f"`{spec.name}`" for spec in specs), 1024),
                inline=False,
            )
        embed.set_footer(text=f"{len(REGISTRY.all())} tools · {sum(len(s.action_names) or 1 for s in REGISTRY.all())} actions")
        await ctx.say(embed=embed)
        return sent("Showed help.")

    if operation == "status":
        statuses = ctx.router.status()
        if not statuses:
            return fail("No AI models are configured, sir.")
        embed = discord.Embed(title="🧠 AI model status", colour=discord.Colour.blurple())
        for status in statuses[:20]:
            state = (
                "❌ disabled"
                if status.disabled
                else f"⏳ cooling {status.cooldown_seconds}s"
                if status.cooling
                else "✅ active"
            )
            embed.add_field(
                name=("▶️ " if status.current else "") + status.label,
                value=f"{state} · failures: {status.failures}"
                + (f"\n{truncate(status.last_error, 120)}" if status.last_error else ""),
                inline=False,
            )
        await ctx.say(embed=embed)
        return sent("Showed model status.")

    if operation == "channels":
        embed = discord.Embed(title=f"📚 {ctx.guild.name}", colour=discord.Colour.blurple())
        for category in ctx.guild.categories:
            embed.add_field(
                name=category.name,
                value=truncate(
                    ", ".join(channel.name for channel in category.channels) or "—", 1024
                ),
                inline=False,
            )
        await ctx.say(embed=embed)
        return sent("Listed channels.")

    return fail(f"I don't know how to `{operation}`, sir.")


async def _web_search(ctx, query: str, limit: int) -> list[tuple[str, str, str]]:
    url = "https://html.duckduckgo.com/html/"
    try:
        async with ctx.bot.session.post(
            url,
            data={"q": query, "kl": "wt-wt"},
            headers={"User-Agent": "Mozilla/5.0 (compatible; Jarvis/2.0)"},
            timeout=20,
        ) as response:
            html = await response.text()
    except Exception:
        return []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    results: list[tuple[str, str, str]] = []
    for result in soup.select(".result"):
        link = result.select_one(".result__a")
        snippet = result.select_one(".result__snippet")
        if link is None:
            continue
        href = link.get("href", "")
        if href.startswith("//duckduckgo.com/l/?uddg="):
            from urllib.parse import parse_qs, unquote, urlparse

            parsed = parse_qs(urlparse(href).query).get("uddg")
            href = unquote(parsed[0]) if parsed else href
        results.append(
            (
                link.get_text(" ", strip=True),
                href,
                snippet.get_text(" ", strip=True) if snippet else "",
            )
        )
        if len(results) >= limit:
            break
    return results


__all__ = ["get_info", "utility"]
