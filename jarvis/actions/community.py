"""Community configuration: welcomes, logging, autoroles, autoresponders,
reaction roles, starboard, stat counters, and temporary voice hubs."""

from __future__ import annotations

import discord

from ..ai.tools import Risk, action, tool
from ..util import (
    channel_mention,
    parse_bool,
    parse_emoji,
    truncate,
)
from .helpers import (
    audit_reason,
    boolean,
    channel_or_error,
    fail,
    int_between,
    message_or_error,
    role_hierarchy_error,
    role_or_error,
    sent,
)

ACTIONS = {
    "set_welcome": action(Risk.MEDIUM, permission="manage_guild"),
    "set_goodbye": action(Risk.MEDIUM, permission="manage_guild"),
    "disable_welcome": action(Risk.MEDIUM, permission="manage_guild"),
    "disable_goodbye": action(Risk.MEDIUM, permission="manage_guild"),
    "test_welcome": action(Risk.LOW, permission="manage_guild"),
    "set_logging": action(Risk.MEDIUM, permission="manage_guild"),
    "disable_logging": action(Risk.MEDIUM, permission="manage_guild"),
    "add_autorole": action(Risk.MEDIUM, permission="manage_roles"),
    "remove_autorole": action(Risk.MEDIUM, permission="manage_roles"),
    "list_autoroles": action(Risk.SAFE),
    "add_autoresponse": action(Risk.LOW, permission="manage_messages"),
    "remove_autoresponse": action(Risk.MEDIUM, permission="manage_messages"),
    "list_autoresponses": action(Risk.SAFE),
    "add_reaction_role": action(Risk.MEDIUM, permission="manage_roles"),
    "remove_reaction_role": action(Risk.MEDIUM, permission="manage_roles"),
    "list_reaction_roles": action(Risk.SAFE),
    "starboard": action(Risk.MEDIUM, permission="manage_guild"),
    "disable_starboard": action(Risk.MEDIUM, permission="manage_guild"),
    "add_counter": action(Risk.MEDIUM, permission="manage_channels"),
    "remove_counter": action(Risk.MEDIUM, permission="manage_channels"),
    "list_counters": action(Risk.SAFE),
    "set_tempvoice": action(Risk.MEDIUM, permission="manage_channels"),
    "disable_tempvoice": action(Risk.MEDIUM, permission="manage_channels"),
    "settings": action(Risk.SAFE, permission="manage_guild"),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "channel": {"type": "string", "description": "Target channel"},
        "message": {
            "type": "string",
            "description": "Welcome/goodbye text, or message link for reaction roles",
        },
        "text": {"type": "string", "description": "Autoresponse reply text"},
        "trigger": {"type": "string", "description": "Autoresponse trigger phrase"},
        "exact": {"type": "boolean", "description": "Autoresponse requires an exact match"},
        "role": {"type": "string"},
        "emoji": {"type": "string"},
        "threshold": {"type": "integer", "description": "Stars required for the starboard"},
        "kind": {
            "type": "string",
            "enum": ["members", "humans", "bots", "online", "boosts"],
            "description": "Stat counter type",
        },
        "name": {"type": "string", "description": "Counter channel name template"},
        "category": {"type": "string", "description": "Category for the temp voice hub"},
        "identifier": {"type": "string", "description": "Autoresponse ID, reaction role ID, or channel"},
        "enabled": {"type": "boolean"},
    },
    "required": ["action"],
}


@tool(
    "manage_community",
    "Configure welcomes, goodbyes, logging, autoroles, autoresponders, reaction roles, "
    "starboard, member-count channels, and temporary voice channels.",
    SCHEMA,
    category="community",
    actions=ACTIONS,
)
async def manage_community(ctx, args):
    operation = str(args["action"]).lower()
    guild_id = ctx.guild_id

    if operation in {"set_welcome", "set_goodbye"}:
        channel, error = channel_or_error(ctx, args.get("channel"), default_current=True)
        if error:
            return error
        if not isinstance(channel, discord.TextChannel):
            return fail("That needs to be a text channel, sir.")
        key = "welcome" if operation == "set_welcome" else "goodbye"
        message = str(args.get("message") or "").strip()
        if not message:
            message = (
                "Welcome to **{server}**, {user}! You are member #{count}."
                if key == "welcome"
                else "**{user}** has left the server. We're now {count} members."
            )
        ctx.db.set_config(
            guild_id,
            key,
            {"channel_id": channel.id, "message": truncate(message, 1500)},
        )
        await ctx.say(f"✅ {key.title()} messages will be posted in {channel.mention}.")
        return sent(f"{key.title()} configured.")

    if operation in {"disable_welcome", "disable_goodbye"}:
        key = "welcome" if operation == "disable_welcome" else "goodbye"
        ctx.db.delete_config(guild_id, key)
        await ctx.say(f"✅ {key.title()} messages disabled.")
        return sent(f"{key.title()} disabled.")

    if operation == "test_welcome":
        config = ctx.db.get_config(guild_id, "welcome", {})
        channel = ctx.guild.get_channel(int(config.get("channel_id") or 0))
        if channel is None:
            return fail("Welcome messages aren't configured, sir.")
        await channel.send(
            _render(config.get("message", "Welcome, {user}!"), ctx.author, ctx.guild)
        )
        return sent("Sent a test welcome message.")

    if operation == "set_logging":
        channel, error = channel_or_error(ctx, args.get("channel"), default_current=True)
        if error:
            return error
        ctx.db.set_config(guild_id, "logging", {"channel_id": channel.id})
        await ctx.say(f"📋 Server logs will be posted in {channel.mention}.")
        return sent("Logging configured.")

    if operation == "disable_logging":
        ctx.db.delete_config(guild_id, "logging")
        await ctx.say("📋 Server logging disabled.")
        return sent("Logging disabled.")

    if operation in {"add_autorole", "remove_autorole"}:
        role, error = role_or_error(ctx, args.get("role"))
        if error:
            return error
        problem = role_hierarchy_error(ctx, role)
        if problem:
            return problem
        roles = [int(r) for r in ctx.db.get_config(guild_id, "autoroles", [])]
        if operation == "add_autorole":
            if role.id not in roles:
                roles.append(role.id)
            ctx.db.set_config(guild_id, "autoroles", roles)
            await ctx.say(f"✅ New members will receive **{role.name}**.")
            return sent("Autorole added.")
        if role.id in roles:
            roles.remove(role.id)
        ctx.db.set_config(guild_id, "autoroles", roles)
        await ctx.say(f"➖ New members will no longer receive **{role.name}**.")
        return sent("Autorole removed.")

    if operation == "list_autoroles":
        roles = [ctx.guild.get_role(int(r)) for r in ctx.db.get_config(guild_id, "autoroles", [])]
        roles = [r for r in roles if r is not None]
        await ctx.say(
            "**Autoroles:** " + (", ".join(r.mention for r in roles) if roles else "none")
        )
        return sent(f"{len(roles)} autoroles.")

    if operation == "add_autoresponse":
        trigger = str(args.get("trigger") or "").strip()
        text = str(args.get("text") or args.get("message") or "").strip()
        if not trigger or not text:
            return fail("Give me both a trigger and a reply, sir.")
        response_id = ctx.db.add_autoresponse(
            guild_id, trigger, text, boolean(args.get("exact"), True)
        )
        await ctx.say(f"✅ Autoresponse `{response_id}` added for “{truncate(trigger, 80)}”.")
        return sent("Autoresponse added.")

    if operation == "remove_autoresponse":
        identifier = str(args.get("identifier") or args.get("trigger") or "").strip()
        if not identifier:
            return fail("Tell me the autoresponse ID or trigger, sir.")
        if not ctx.db.remove_autoresponse(guild_id, identifier):
            return fail("I couldn't find that autoresponse, sir.")
        return sent("Autoresponse removed.")

    if operation == "list_autoresponses":
        rows = ctx.db.autoresponses(guild_id)
        if not rows:
            await ctx.say("There are no autoresponses, sir.")
            return sent("No autoresponses.")
        lines = [
            f"`{row['id']}` · **{truncate(row['trigger'], 60)}** → {truncate(row['response'], 120)}"
            for row in rows[:30]
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(rows)} autoresponses.")

    if operation == "add_reaction_role":
        message, error = await message_or_error(ctx, args.get("message"))
        if error:
            return error
        role, error = role_or_error(ctx, args.get("role"))
        if error:
            return error
        problem = role_hierarchy_error(ctx, role)
        if problem:
            return problem
        emoji = parse_emoji(args.get("emoji"))
        if not emoji:
            return fail("Tell me which emoji to use, sir.")
        await message.add_reaction(emoji)
        ctx.db.add_reaction_role(
            guild_id, message.channel.id, message.id, emoji, role.id
        )
        await ctx.say(
            f"✅ Reacting with {emoji} on [that message]({message.jump_url}) grants **{role.name}**."
        )
        return sent("Reaction role added.")

    if operation == "remove_reaction_role":
        identifier = str(args.get("identifier") or args.get("emoji") or "").strip()
        if not identifier:
            return fail("Tell me the reaction role ID or emoji, sir.")
        if not ctx.db.remove_reaction_role(guild_id, identifier):
            return fail("I couldn't find that reaction role, sir.")
        return sent("Reaction role removed.")

    if operation == "list_reaction_roles":
        rows = ctx.db.reaction_roles(guild_id)
        if not rows:
            await ctx.say("There are no reaction roles, sir.")
            return sent("No reaction roles.")
        lines = [
            f"`{row['id']}` · {row['emoji']} → <@&{row['role_id']}> on message `{row['message_id']}`"
            for row in rows[:30]
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(rows)} reaction roles.")

    if operation == "starboard":
        channel, error = channel_or_error(ctx, args.get("channel"))
        if error:
            return error
        threshold = int_between(args.get("threshold"), 1, 100, 3)
        emoji = parse_emoji(args.get("emoji")) or "⭐"
        ctx.db.set_config(
            guild_id,
            "starboard",
            {"channel_id": channel.id, "threshold": threshold, "emoji": emoji},
        )
        await ctx.say(
            f"⭐ Starboard set to {channel.mention} with a threshold of **{threshold}** {emoji}."
        )
        return sent("Starboard configured.")

    if operation == "disable_starboard":
        ctx.db.delete_config(guild_id, "starboard")
        await ctx.say("⭐ Starboard disabled.")
        return sent("Starboard disabled.")

    if operation in {"add_counter", "remove_counter"}:
        if operation == "remove_counter":
            channel, error = channel_or_error(ctx, args.get("channel") or args.get("name"))
            if error:
                return error
            counters = [
                c
                for c in ctx.db.get_config(guild_id, "counters", [])
                if int(c.get("channel_id", 0)) != channel.id
            ]
            ctx.db.set_config(guild_id, "counters", counters)
            await ctx.say(f"➖ Removed the counter channel {channel.mention}.")
            return sent("Counter removed.")
        kind = str(args.get("kind") or "members").lower()
        if kind not in {"members", "humans", "bots", "online", "boosts"}:
            return fail("Pick a counter type: members, humans, bots, online, boosts.")
        name_template = str(args.get("name") or "").strip()
        if not name_template:
            name_template = f"{kind.title()}: {{count}}"
        elif "{count}" not in name_template:
            name_template = f"{name_template} {{count}}"
        channel = await ctx.guild.create_voice_channel(
            name=truncate(name_template, 100),
            reason=audit_reason(ctx, "Stat counter created"),
        )
        counters = ctx.db.get_config(guild_id, "counters", [])
        counters.append({"channel_id": channel.id, "kind": kind, "template": name_template})
        ctx.db.set_config(guild_id, "counters", counters)
        await ctx.say(f"📊 Created counter {channel.mention} for **{kind}**.")
        return sent("Counter created.")

    if operation == "list_counters":
        counters = ctx.db.get_config(guild_id, "counters", [])
        if not counters:
            await ctx.say("There are no stat counters, sir.")
            return sent("No counters.")
        lines = [
            f"📊 <#{c['channel_id']}> — {c.get('kind', 'members')} · `{c.get('template', '')}`"
            for c in counters
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(counters)} counters.")

    if operation == "set_tempvoice":
        hub, error = channel_or_error(ctx, args.get("channel"))
        if error:
            return error
        if not isinstance(hub, discord.VoiceChannel):
            return fail("The temp voice hub must be a voice channel, sir.")
        category = None
        if args.get("category"):
            category = next(
                (c for c in ctx.guild.categories if c.name.lower() == str(args["category"]).lower()),
                None,
            )
        ctx.db.set_config(
            guild_id,
            "tempvoice",
            {
                "hub_id": hub.id,
                "category_id": category.id if category else (hub.category.id if hub.category else 0),
                "template": str(args.get("name") or "{user}'s channel")[:90],
            },
        )
        await ctx.say(f"🔊 Temporary voice hub set to {hub.mention}.")
        return sent("Temp voice configured.")

    if operation == "disable_tempvoice":
        ctx.db.delete_config(guild_id, "tempvoice")
        await ctx.say("🔊 Temporary voice channels disabled.")
        return sent("Temp voice disabled.")

    if operation == "settings":
        config = ctx.db.all_config(guild_id)
        if not config:
            await ctx.say("There are no community settings yet, sir.")
            return sent("No settings.")
        lines = []
        for key, value in sorted(config.items()):
            if isinstance(value, dict):
                value = ", ".join(f"{k}={v}" for k, v in value.items())
            elif isinstance(value, list):
                value = ", ".join(map(str, value[:10]))
            lines.append(f"**{key}**: {truncate(value, 500)}")
        await ctx.say("\n".join(lines[:25]))
        return sent("Showed community settings.")

    return fail(f"I don't know how to `{operation}` community settings, sir.")


def _counter_value(guild: discord.Guild, kind: str) -> int:
    members = guild.members
    if kind == "members":
        return guild.member_count or len(members)
    if kind == "humans":
        return sum(1 for m in members if not m.bot)
    if kind == "bots":
        return sum(1 for m in members if m.bot)
    if kind == "online":
        return sum(1 for m in members if m.status != discord.Status.offline)
    if kind == "boosts":
        return guild.premium_subscription_count or 0
    return 0


def _render(template: str, member: discord.Member, guild: discord.Guild) -> str:
    values = {
        "{user}": member.mention,
        "{name}": member.display_name,
        "{server}": guild.name,
        "{count}": str(guild.member_count or len(guild.members)),
        "{created}": discord.utils.format_dt(member.created_at, "R"),
    }
    text = str(template)
    for key, value in values.items():
        text = text.replace(key, value)
    return truncate(text, 2000)


__all__ = ["manage_community", "_counter_value", "_render"]
