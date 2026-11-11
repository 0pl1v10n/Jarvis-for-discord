"""Server settings, invites, webhooks, expressions, events, and AutoMod."""

from __future__ import annotations

from datetime import timedelta

import discord

from ..ai.tools import Risk, action, tool
from ..util import (
    fetch_image_bytes,
    format_dt,
    parse_bool,
    parse_time,
    sanitize_emoji_name,
    truncate,
    utcnow,
)
from .helpers import (
    audit_reason,
    boolean,
    channel_or_error,
    fail,
    int_between,
    server_embed,
    sent,
)

SERVER_ACTIONS = {
    "info": action(Risk.SAFE),
    "rename": action(Risk.HIGH, permission="manage_guild", confirm=True),
    "icon": action(Risk.HIGH, permission="manage_guild", confirm=True),
    "banner": action(Risk.HIGH, permission="manage_guild", confirm=True),
    "splash": action(Risk.HIGH, permission="manage_guild", confirm=True),
    "verification": action(Risk.HIGH, permission="manage_guild", confirm=True),
    "afk": action(Risk.MEDIUM, permission="manage_guild"),
    "system_channel": action(Risk.MEDIUM, permission="manage_guild"),
    "rules_channel": action(Risk.MEDIUM, permission="manage_guild"),
    "updates_channel": action(Risk.MEDIUM, permission="manage_guild"),
    "notifications": action(Risk.MEDIUM, permission="manage_guild"),
    "locale": action(Risk.MEDIUM, permission="manage_guild"),
    "widget": action(Risk.MEDIUM, permission="manage_guild"),
}

SERVER_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(SERVER_ACTIONS)},
        "name": {"type": "string"},
        "url": {"type": "string", "description": "Image URL for icon/banner/splash"},
        "channel": {"type": "string", "description": "Channel for afk/system/rules/updates"},
        "seconds": {"type": "integer", "description": "AFK timeout seconds"},
        "level": {
            "type": "string",
            "enum": ["none", "low", "medium", "high", "highest"],
        },
        "enabled": {"type": "boolean", "description": "Enable/disable notifications or widget"},
        "locale": {"type": "string", "description": "Preferred locale, e.g. en-US"},
    },
    "required": ["action"],
}

VERIFICATION = {
    "none": discord.VerificationLevel.none,
    "low": discord.VerificationLevel.low,
    "medium": discord.VerificationLevel.medium,
    "high": discord.VerificationLevel.high,
    "highest": discord.VerificationLevel.highest,
}


async def _set_image(ctx, kind: str, url: str) -> str | None:
    data = await fetch_image_bytes(ctx.bot.session, url, max_bytes=ctx.settings.max_image_bytes)
    if data is None:
        return "I couldn't download that image, sir."
    payload, _mime = data
    try:
        if kind == "icon":
            await ctx.guild.edit(icon=payload, reason=audit_reason(ctx, "Server icon changed"))
        elif kind == "banner":
            await ctx.guild.edit(banner=payload, reason=audit_reason(ctx, "Server banner changed"))
        else:
            await ctx.guild.edit(splash=payload, reason=audit_reason(ctx, "Server splash changed"))
    except discord.HTTPException as exc:
        return f"Discord refused that image: {truncate(exc, 180)}"
    return None


@tool(
    "manage_server",
    "Inspect or change server-wide settings: name, icon, banner, splash, verification level, "
    "AFK channel/timeout, system/rules/updates channels, notifications, locale, and widget.",
    SERVER_SCHEMA,
    category="server",
    actions=SERVER_ACTIONS,
)
async def manage_server(ctx, args):
    operation = str(args["action"]).lower()
    guild = ctx.guild

    if operation == "info":
        await ctx.say(embed=server_embed(guild))
        return sent("Showed server information.")

    if operation == "rename":
        name = str(args.get("name") or "").strip()[:100]
        if len(name) < 2:
            return fail("Give me a server name of at least two characters, sir.")
        old = guild.name
        await guild.edit(name=name, reason=audit_reason(ctx, "Server renamed"))
        await ctx.say(f"🏰 Renamed **{old}** to **{name}**.")
        return sent("Server renamed.")

    if operation in {"icon", "banner", "splash"}:
        url = str(args.get("url") or "").strip()
        if not url:
            return fail("Give me an image URL, sir.")
        error = await _set_image(ctx, operation, url)
        if error:
            return fail(error)
        await ctx.say(f"🖼️ Updated the server {operation}.")
        return sent(f"Server {operation} updated.")

    if operation == "verification":
        level = str(args.get("level") or "").lower()
        if level not in VERIFICATION:
            return fail("Pick one of: none, low, medium, high, highest.")
        await guild.edit(
            verification_level=VERIFICATION[level],
            reason=audit_reason(ctx, "Verification level changed"),
        )
        await ctx.say(f"🛡️ Verification level is now **{level}**.")
        return sent("Verification level updated.")

    if operation == "afk":
        channel, error = channel_or_error(ctx, args.get("channel"))
        if error:
            return error
        if not isinstance(channel, discord.VoiceChannel):
            return fail("The AFK channel must be a voice channel, sir.")
        seconds = int_between(args.get("seconds"), 60, 21600, 300)
        await guild.edit(
            afk_channel=channel,
            afk_timeout=seconds,
            reason=audit_reason(ctx, "AFK settings changed"),
        )
        await ctx.say(f"💤 AFK channel set to {channel.mention} ({seconds}s).")
        return sent("AFK settings updated.")

    if operation in {"system_channel", "rules_channel", "updates_channel"}:
        channel, error = channel_or_error(ctx, args.get("channel"))
        if error:
            return error
        field = {
            "system_channel": "system_channel",
            "rules_channel": "rules_channel",
            "updates_channel": "public_updates_channel",
        }[operation]
        await guild.edit(**{field: channel}, reason=audit_reason(ctx, "Server channel changed"))
        await ctx.say(f"📌 Set the {operation.replace('_', ' ')} to {channel.mention}.")
        return sent("Server channel updated.")

    if operation == "notifications":
        value = boolean(args.get("enabled"), True)
        await guild.edit(
            default_notifications=discord.NotificationLevel.all_messages
            if value
            else discord.NotificationLevel.only_mentions,
            reason=audit_reason(ctx, "Default notifications changed"),
        )
        await ctx.say(
            f"🔔 Default notifications: **{'all messages' if value else 'mentions only'}**."
        )
        return sent("Notification setting updated.")

    if operation == "locale":
        locale = str(args.get("locale") or "").strip()
        if not locale:
            return fail("Tell me the locale, e.g. en-US, sir.")
        await guild.edit(preferred_locale=locale, reason=audit_reason(ctx, "Locale changed"))
        await ctx.say(f"🌐 Preferred locale is now **{locale}**.")
        return sent("Locale updated.")

    if operation == "widget":
        value = boolean(args.get("enabled"), True)
        await guild.edit(widget_enabled=value, reason=audit_reason(ctx, "Widget changed"))
        await ctx.say(f"🧩 Server widget **{'enabled' if value else 'disabled'}**.")
        return sent("Widget updated.")

    return fail(f"I don't know how to `{operation}` the server, sir.")


INVITE_ACTIONS = {
    "create": action(Risk.LOW, permission="create_instant_invite"),
    "list": action(Risk.LOW, permission="manage_guild"),
    "delete": action(Risk.MEDIUM, permission="manage_guild"),
    "delete_all": action(Risk.HIGH, permission="manage_guild", confirm=True),
}

INVITE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(INVITE_ACTIONS)},
        "channel": {"type": "string"},
        "code": {"type": "string", "description": "Invite code or full URL"},
        "max_age": {"type": "integer", "description": "Seconds until expiry (0 = never)"},
        "max_uses": {"type": "integer", "description": "Max uses (0 = unlimited)"},
        "temporary": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["action"],
}


@tool(
    "manage_invites",
    "Create, list, revoke, or revoke every invite in the server.",
    INVITE_SCHEMA,
    category="server",
    actions=INVITE_ACTIONS,
)
async def manage_invites(ctx, args):
    operation = str(args["action"]).lower()

    if operation == "create":
        channel, error = channel_or_error(ctx, args.get("channel"), default_current=True)
        if error:
            return error
        if not isinstance(channel, discord.TextChannel):
            return fail("Invites can only be created in text channels, sir.")
        invite = await channel.create_invite(
            max_age=max(0, int(args.get("max_age") or 0)),
            max_uses=max(0, int(args.get("max_uses") or 0)),
            temporary=boolean(args.get("temporary")),
            reason=audit_reason(ctx, args.get("reason") or "Invite created"),
        )
        await ctx.say(f"🔗 Created invite **{invite.url}** (expires {format_dt(invite.expires_at)}).")
        return sent(f"Created invite {invite.url}.")

    if operation == "list":
        invites = await ctx.guild.invites()
        if not invites:
            await ctx.say("There are no active invites, sir.")
            return sent("No invites.")
        lines = [
            f"🔗 `{invite.code}` — {invite.uses or 0}/{invite.max_uses or '∞'} uses, "
            f"in {invite.channel.mention if invite.channel else 'unknown'}, by {invite.inviter}"
            for invite in invites[:30]
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(invites)} invites.")

    if operation == "delete_all":
        invites = await ctx.guild.invites()
        for invite in invites:
            try:
                await invite.delete(reason=audit_reason(ctx, "All invites revoked"))
            except discord.HTTPException:
                pass
        await ctx.say(f"🧹 Revoked **{len(invites)}** invite(s).")
        return sent(f"Revoked {len(invites)} invites.")

    code = str(args.get("code") or "").strip().rsplit("/", 1)[-1]
    if not code:
        return fail("Tell me the invite code, sir.")
    invites = {invite.code: invite for invite in await ctx.guild.invites()}
    invite = invites.get(code)
    if invite is None:
        return fail("I couldn't find that invite, sir.")
    await invite.delete(reason=audit_reason(ctx, args.get("reason") or "Invite revoked"))
    await ctx.say(f"🗑️ Revoked invite `{code}`.")
    return sent("Invite revoked.")


WEBHOOK_ACTIONS = {
    "list": action(Risk.LOW, permission="manage_webhooks"),
    "create": action(Risk.MEDIUM, permission="manage_webhooks"),
    "delete": action(Risk.MEDIUM, permission="manage_webhooks"),
    "send": action(Risk.MEDIUM, permission="manage_webhooks"),
    "edit": action(Risk.MEDIUM, permission="manage_webhooks"),
}

WEBHOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(WEBHOOK_ACTIONS)},
        "channel": {"type": "string"},
        "name": {"type": "string", "description": "Webhook name or new name"},
        "webhook": {"type": "string", "description": "Webhook name or ID"},
        "content": {"type": "string"},
        "username": {"type": "string"},
        "avatar": {"type": "string", "description": "Avatar URL"},
        "reason": {"type": "string"},
    },
    "required": ["action"],
}


@tool(
    "manage_webhooks",
    "List, create, edit, delete, or post through channel webhooks.",
    WEBHOOK_SCHEMA,
    category="server",
    actions=WEBHOOK_ACTIONS,
)
async def manage_webhooks(ctx, args):
    operation = str(args["action"]).lower()

    if operation == "list":
        hooks = await ctx.guild.webhooks()
        if not hooks:
            await ctx.say("There are no webhooks, sir.")
            return sent("No webhooks.")
        lines = [
            f"🪝 **{hook.name}** (`{hook.id}`) in {hook.channel.mention if hook.channel else 'unknown'}"
            for hook in hooks[:30]
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(hooks)} webhooks.")

    if operation == "create":
        channel, error = channel_or_error(ctx, args.get("channel"), default_current=True)
        if error:
            return error
        if not isinstance(channel, discord.TextChannel):
            return fail("Webhooks need a text channel, sir.")
        hook = await channel.create_webhook(
            name=str(args.get("name") or "Jarvis"),
            reason=audit_reason(ctx, args.get("reason") or "Webhook created"),
        )
        await ctx.say(f"🪝 Created webhook **{hook.name}** in {channel.mention}.")
        return sent(f"Created webhook {hook.name}.")

    hooks = await ctx.guild.webhooks()
    wanted = str(args.get("webhook") or args.get("name") or "").strip()
    hook = next((h for h in hooks if str(h.id) == wanted), None)
    if hook is None:
        hook = next((h for h in hooks if h.name.lower() == wanted.lower()), None)
    if hook is None:
        return fail("I couldn't find that webhook, sir.")

    if operation == "delete":
        await hook.delete(reason=audit_reason(ctx, args.get("reason") or "Webhook deleted"))
        return sent("Webhook deleted.")

    if operation == "edit":
        name = str(args.get("name") or "").strip()
        avatar = str(args.get("avatar") or "").strip()
        if not name and not avatar:
            return fail("Tell me a new name or avatar, sir.")
        await hook.edit(
            name=name or None,
            avatar=avatar or None,
            reason=audit_reason(ctx, "Webhook edited"),
        )
        return sent("Webhook edited.")

    content = str(args.get("content") or "").strip()
    if not content:
        return fail("Tell me what to post, sir.")
    await hook.send(
        content=truncate(content, 2000),
        username=truncate(args.get("username") or hook.name, 80),
        avatar_url=args.get("avatar") or None,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return sent("Posted through the webhook.")


EMOJI_ACTIONS = {
    "list_emojis": action(Risk.SAFE),
    "list_stickers": action(Risk.SAFE),
    "add_emoji": action(Risk.MEDIUM, permission="manage_expressions"),
    "delete_emoji": action(Risk.MEDIUM, permission="manage_expressions"),
    "rename_emoji": action(Risk.MEDIUM, permission="manage_expressions"),
    "add_sticker": action(Risk.MEDIUM, permission="manage_expressions"),
    "delete_sticker": action(Risk.MEDIUM, permission="manage_expressions"),
}

EMOJI_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(EMOJI_ACTIONS)},
        "emoji": {"type": "string", "description": "Emoji name, mention, or ID"},
        "name": {"type": "string"},
        "url": {"type": "string", "description": "Image URL for add_emoji/add_sticker"},
        "sticker": {"type": "string", "description": "Sticker name or ID"},
        "description": {"type": "string"},
        "tags": {"type": "string", "description": "Comma-separated sticker tags"},
    },
    "required": ["action"],
}


@tool(
    "manage_expressions",
    "List, add, rename, or delete custom emojis and stickers.",
    EMOJI_SCHEMA,
    category="server",
    actions=EMOJI_ACTIONS,
)
async def manage_expressions(ctx, args):
    operation = str(args["action"]).lower()

    if operation == "list_emojis":
        emojis = [f"{e} `:{e.name}:`" for e in ctx.guild.emojis[:60]]
        await ctx.say(" ".join(emojis) or "There are no custom emojis, sir.")
        return sent(f"Listed {len(ctx.guild.emojis)} emojis.")

    if operation == "list_stickers":
        stickers = [f"🏷️ **{s.name}** (`{s.id}`)" for s in ctx.guild.stickers[:60]]
        await ctx.say("\n".join(stickers) or "There are no custom stickers, sir.")
        return sent(f"Listed {len(ctx.guild.stickers)} stickers.")

    if operation in {"add_emoji", "add_sticker"}:
        url = str(args.get("url") or "").strip()
        name = str(args.get("name") or "").strip()
        if not url or not name:
            return fail("Give me a name and an image URL, sir.")
        data = await fetch_image_bytes(ctx.bot.session, url, max_bytes=512 * 1024)
        if data is None:
            return fail("I couldn't download that image, sir.")
        payload, _mime = data
        if operation == "add_emoji":
            emoji = await ctx.guild.create_custom_emoji(
                name=sanitize_emoji_name(name),
                image=payload,
                reason=audit_reason(ctx, "Emoji added"),
            )
            await ctx.say(f"😀 Added {emoji} as `:{emoji.name}:`.")
            return sent("Emoji added.")
        sticker = await ctx.guild.create_sticker(
            name=truncate(name, 30),
            description=truncate(args.get("description") or "Added by Jarvis", 100),
            emoji=str(args.get("emoji") or "🙂"),
            file=discord.File(__import__("io").BytesIO(payload), filename="sticker.png"),
            reason=audit_reason(ctx, "Sticker added"),
        )
        await ctx.say(f"🏷️ Added sticker **{sticker.name}**.")
        return sent("Sticker added.")

    if operation in {"delete_emoji", "rename_emoji"}:
        wanted = str(args.get("emoji") or args.get("name") or "").strip()
        emoji = discord.utils.find(
            lambda e: str(e.id) == wanted or e.name.lower() == wanted.strip(":").lower(),
            ctx.guild.emojis,
        )
        if emoji is None:
            return fail("I couldn't find that emoji, sir.")
        if operation == "delete_emoji":
            name = emoji.name
            await emoji.delete(reason=audit_reason(ctx, "Emoji deleted"))
            return sent(f"Deleted emoji **{name}**.")
        name = sanitize_emoji_name(args.get("name") or wanted)
        await emoji.edit(name=name, reason=audit_reason(ctx, "Emoji renamed"))
        return sent(f"Renamed emoji to `:{name}:`.")

    if operation == "delete_sticker":
        wanted = str(args.get("sticker") or args.get("name") or "").strip()
        sticker = discord.utils.find(
            lambda s: str(s.id) == wanted or s.name.lower() == wanted.lower(),
            ctx.guild.stickers,
        )
        if sticker is None:
            return fail("I couldn't find that sticker, sir.")
        name = sticker.name
        await sticker.delete(reason=audit_reason(ctx, "Sticker deleted"))
        return sent(f"Deleted sticker **{name}**.")

    return fail(f"I don't know how to `{operation}` expressions, sir.")


EVENT_ACTIONS = {
    "list": action(Risk.SAFE),
    "create": action(Risk.MEDIUM, permission="manage_events"),
    "edit": action(Risk.MEDIUM, permission="manage_events"),
    "delete": action(Risk.MEDIUM, permission="manage_events"),
    "end": action(Risk.MEDIUM, permission="manage_events"),
}

EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(EVENT_ACTIONS)},
        "name": {"type": "string", "description": "Event name or event to edit"},
        "description": {"type": "string"},
        "start": {"type": "string", "description": "Start time, e.g. '2026-01-01 18:00' or 'in 2h'"},
        "end": {"type": "string", "description": "End time or duration"},
        "channel": {"type": "string", "description": "Voice/stage channel location"},
        "location": {"type": "string", "description": "External location"},
        "url": {"type": "string", "description": "Image cover URL"},
    },
    "required": ["action"],
}


@tool(
    "manage_events",
    "List, create, edit, end, or delete scheduled server events.",
    EVENT_SCHEMA,
    category="server",
    actions=EVENT_ACTIONS,
)
async def manage_events(ctx, args):
    operation = str(args["action"]).lower()

    if operation == "list":
        events = list(ctx.guild.scheduled_events)
        if not events:
            await ctx.say("There are no scheduled events, sir.")
            return sent("No events.")
        lines = [
            f"📅 **{event.name}** — {format_dt(event.start_time)} · {event.status.name.lower()}"
            for event in events[:20]
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(events)} events.")

    if operation == "create":
        name = str(args.get("name") or "").strip()[:100]
        start = parse_time(args.get("start"))
        if not name or start is None:
            return fail("Give me an event name and a start time, sir.")
        if start <= utcnow():
            return fail("The start time must be in the future, sir.")
        kwargs: dict = {
            "name": name,
            "description": truncate(args.get("description") or "", 1000) or None,
            "start_time": start,
            "reason": audit_reason(ctx, "Event created"),
        }
        end = parse_time(args.get("end"))
        if end is not None and end > start:
            kwargs["end_time"] = end
        if args.get("channel"):
            channel, error = channel_or_error(ctx, args.get("channel"))
            if error:
                return error
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                return fail("Event locations must be a voice or stage channel, sir.")
            kwargs["channel"] = channel
        elif args.get("location"):
            kwargs["location"] = truncate(args["location"], 100)
        else:
            kwargs["entity_type"] = discord.EntityType.external
            kwargs["location"] = "Discord"
        if args.get("url"):
            cover = await fetch_image_bytes(ctx.bot.session, str(args["url"]))
            if cover:
                kwargs["image"] = cover[0]
        event = await ctx.guild.create_scheduled_event(**kwargs)
        await ctx.say(f"📅 Created event **{event.name}** starting {format_dt(start)}.")
        return sent("Event created.")

    wanted = str(args.get("name") or "").strip()
    event = discord.utils.find(
        lambda e: e.name.lower() == wanted.lower() or str(e.id) == wanted,
        ctx.guild.scheduled_events,
    )
    if event is None:
        return fail("I couldn't find that event, sir.")

    if operation == "delete":
        await event.delete(reason=audit_reason(ctx, "Event deleted"))
        return sent("Event deleted.")
    if operation == "end":
        await event.end(reason=audit_reason(ctx, "Event ended"))
        return sent("Event ended.")
    if operation == "edit":
        kwargs = {}
        if args.get("name"):
            kwargs["name"] = truncate(args["name"], 100)
        if args.get("description"):
            kwargs["description"] = truncate(args["description"], 1000)
        start = parse_time(args.get("start"))
        if start is not None:
            kwargs["start_time"] = start
        end = parse_time(args.get("end"))
        if end is not None:
            kwargs["end_time"] = end
        if args.get("location"):
            kwargs["location"] = truncate(args["location"], 100)
        if not kwargs:
            return fail("Tell me what to change, sir.")
        await event.edit(**kwargs, reason=audit_reason(ctx, "Event edited"))
        return sent("Event edited.")

    return fail(f"I don't know how to `{operation}` events, sir.")


AUTOMOD_ACTIONS = {
    "list": action(Risk.LOW, permission="manage_guild"),
    "create": action(Risk.HIGH, permission="manage_guild", confirm=True),
    "delete": action(Risk.HIGH, permission="manage_guild", confirm=True),
    "toggle": action(Risk.HIGH, permission="manage_guild"),
}

AUTOMOD_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(AUTOMOD_ACTIONS)},
        "name": {"type": "string", "description": "Rule name (for create) or rule to find"},
        "kind": {
            "type": "string",
            "enum": ["keyword", "spam", "mention_spam", "link_spam", "profanity"],
        },
        "keywords": {"type": "array", "items": {"type": "string"}},
        "regex": {"type": "string"},
        "limit": {"type": "integer", "description": "Mention or spam limit"},
        "timeout_minutes": {"type": "integer", "description": "Timeout duration"},
        "action_type": {
            "type": "string",
            "enum": ["block", "timeout", "alert"],
        },
        "enabled": {"type": "boolean"},
        "alert_channel": {"type": "string"},
        "exempt_roles": {"type": "array", "items": {"type": "string"}},
        "exempt_channels": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action"],
}


@tool(
    "manage_automod",
    "List, create, delete, or toggle AutoMod rules (keyword, spam, mention spam, links, profanity).",
    AUTOMOD_SCHEMA,
    category="server",
    actions=AUTOMOD_ACTIONS,
)
async def manage_automod(ctx, args):
    operation = str(args["action"]).lower()

    if operation == "list":
        rules = await ctx.guild.fetch_automod_rules()
        if not rules:
            await ctx.say("There are no AutoMod rules, sir.")
            return sent("No AutoMod rules.")
        lines = [
            f"🤖 **{rule.name}** — {rule.trigger.type.name.lower()}, "
            f"{'enabled' if rule.enabled else 'disabled'}, actions: "
            f"{', '.join(a.type.name.lower() for a in rule.actions)}"
            for rule in rules
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(rules)} AutoMod rules.")

    if operation == "create":
        name = str(args.get("name") or "Jarvis rule").strip()[:100]
        kind = str(args.get("kind") or "keyword").lower()
        if kind == "keyword":
            keywords = args.get("keywords") or []
            if isinstance(keywords, str):
                keywords = [k.strip() for k in keywords.split(",") if k.strip()]
            regex = str(args.get("regex") or "").strip()
            if not keywords and not regex:
                return fail("Give me keywords or a regex, sir.")
            trigger = discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.keyword,
                keyword_filter=[str(k)[:60] for k in keywords][:100] or None,
                regex_patterns=[regex] if regex else None,
            )
        elif kind == "spam":
            trigger = discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.spam)
        elif kind == "mention_spam":
            trigger = discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.mention_spam,
                mention_total_limit=max(1, int(args.get("limit") or 5)),
            )
        elif kind == "link_spam":
            trigger = discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.keyword,
                keyword_filter=["http://", "https://", "discord.gg/"],
            )
        elif kind == "profanity":
            trigger = discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.keyword_preset)
        else:
            return fail("Pick a supported AutoMod rule kind, sir.")

        action_type = str(args.get("action_type") or "block").lower()
        actions = []
        if action_type == "block":
            actions.append(discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message))
        elif action_type == "timeout":
            actions.append(
                discord.AutoModRuleAction(
                    type=discord.AutoModRuleActionType.timeout,
                    duration=timedelta(minutes=max(1, int(args.get("timeout_minutes") or 10))),
                )
            )
        elif action_type == "alert":
            actions.append(discord.AutoModRuleAction(type=discord.AutoModRuleActionType.send_alert_message))

        exempt_roles = []
        for value in args.get("exempt_roles") or []:
            role = ctx.resolve_role(value)
            if role is not None:
                exempt_roles.append(role)
        exempt_channels = []
        for value in args.get("exempt_channels") or []:
            channel = ctx.resolve_channel(value)
            if channel is not None:
                exempt_channels.append(channel)

        rule = await ctx.guild.create_automod_rule(
            name=name,
            event_type=discord.AutoModRuleEventType.message_send,
            trigger=trigger,
            actions=actions,
            enabled=boolean(args.get("enabled"), True),
            exempt_roles=exempt_roles or None,
            exempt_channels=exempt_channels or None,
            reason=audit_reason(ctx, "AutoMod rule created"),
        )
        await ctx.say(f"🤖 Created AutoMod rule **{rule.name}**.")
        return sent("AutoMod rule created.")

    rules = await ctx.guild.fetch_automod_rules()
    wanted = str(args.get("name") or "").strip()
    rule = discord.utils.find(
        lambda r: r.name.lower() == wanted.lower() or str(r.id) == wanted, rules
    )
    if rule is None:
        return fail("I couldn't find that AutoMod rule, sir.")

    if operation == "delete":
        await rule.delete(reason=audit_reason(ctx, "AutoMod rule deleted"))
        return sent("AutoMod rule deleted.")

    if operation == "toggle":
        enabled = boolean(args.get("enabled"), not rule.enabled)
        await rule.edit(enabled=enabled, reason=audit_reason(ctx, "AutoMod rule toggled"))
        await ctx.say(f"🤖 AutoMod rule **{rule.name}** is now **{'enabled' if enabled else 'disabled'}**.")
        return sent("AutoMod rule toggled.")

    return fail(f"I don't know how to `{operation}` AutoMod rules, sir.")
