"""Channel, category, and thread management."""

from __future__ import annotations

import discord

from ..ai.tools import Risk, action, tool
from ..util import (
    parse_permissions,
    sanitize_channel_name,
    snowflake,
    truncate,
)
from .helpers import (
    audit_reason,
    boolean,
    bot_permissions_error,
    category_or_error,
    channel_by_id,
    channel_or_error,
    channels_of,
    fail,
    ok,
    role_hierarchy_error,
    role_or_error,
    sent,
)

CHANNEL_ACTIONS = {
    "create": action(Risk.MEDIUM, permission="manage_channels"),
    "delete": action(Risk.HIGH, permission="manage_channels", confirm=True),
    "rename": action(Risk.MEDIUM, permission="manage_channels"),
    "move": action(Risk.LOW, permission="manage_channels"),
    "clone": action(Risk.MEDIUM, permission="manage_channels"),
    "lock": action(Risk.MEDIUM, permission="manage_channels", confirm=False),
    "unlock": action(Risk.LOW, permission="manage_channels", confirm=False),
    "slowmode": action(Risk.LOW, permission="manage_channels", confirm=False),
    "topic": action(Risk.LOW, permission="manage_channels", confirm=False),
    "nsfw": action(Risk.MEDIUM, permission="manage_channels", confirm=False),
    "bitrate": action(Risk.LOW, permission="manage_channels", confirm=False),
    "user_limit": action(Risk.LOW, permission="manage_channels", confirm=False),
    "permissions": action(Risk.HIGH, permission="manage_channels", confirm=True),
    "sync": action(Risk.MEDIUM, permission="manage_channels", confirm=False),
    "list": action(Risk.SAFE),
}

CHANNEL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(CHANNEL_ACTIONS)},
        "channel": {"type": "string", "description": "Channel name, mention, or ID"},
        "name": {"type": "string", "description": "New or created channel name"},
        "kind": {
            "type": "string",
            "enum": ["text", "voice", "category", "forum", "stage", "news"],
            "description": "Type of channel to create",
        },
        "category": {"type": "string", "description": "Category name"},
        "topic": {"type": "string"},
        "position": {"type": "integer", "description": "Position / ordering"},
        "seconds": {"type": "integer", "description": "Slowmode seconds (0 disables)"},
        "nsfw": {"type": "boolean"},
        "bitrate": {"type": "integer", "description": "Voice bitrate in kbps"},
        "user_limit": {"type": "integer", "description": "Voice user limit (0 = unlimited)"},
        "target": {"type": "string", "description": "Role or member for permissions"},
        "allow": {"type": "string", "description": "Comma-separated permissions to allow"},
        "deny": {"type": "string", "description": "Comma-separated permissions to deny"},
    },
    "required": ["action"],
}

CHANNEL_KIND_FLAGS = {
    "text": {"type": "text"},
    "category": {"type": "category"},
    "voice": {"type": "voice"},
    "forum": {"type": "forum"},
    "stage": {"type": "stage"},
    "news": {"type": "news"},
}


def _create_kwargs(args) -> tuple[dict, str | None]:
    kind = str(args.get("kind") or "text").lower()
    if kind not in CHANNEL_KIND_FLAGS:
        return {}, f"`{kind}` isn't a channel type I know, sir."
    name = sanitize_channel_name(args.get("name") or "jarvis-channel")
    kwargs: dict = {"name": name}
    if kind == "text":
        kwargs["type"] = discord.ChannelType.text
        if args.get("topic"):
            kwargs["topic"] = truncate(args["topic"], 1024)
        kwargs["nsfw"] = boolean(args.get("nsfw"))
    elif kind == "news":
        kwargs["type"] = discord.ChannelType.news
        if args.get("topic"):
            kwargs["topic"] = truncate(args["topic"], 1024)
        kwargs["nsfw"] = boolean(args.get("nsfw"))
    elif kind == "voice":
        kwargs["type"] = discord.ChannelType.voice
        if args.get("bitrate"):
            kwargs["bitrate"] = max(8000, min(384000, int(args["bitrate"]) * 1000))
        if args.get("user_limit") is not None:
            kwargs["user_limit"] = max(0, min(99, int(args["user_limit"])))
    elif kind == "stage":
        kwargs["type"] = discord.ChannelType.stage_voice
    elif kind == "forum":
        kwargs["type"] = discord.ChannelType.forum
        if args.get("topic"):
            kwargs["topic"] = truncate(args["topic"], 1024)
    return kwargs, None


@tool(
    "manage_channel",
    "Create, delete, rename, move, clone, lock, unlock, configure slowmode/topic/NSFW/bitrate, "
    "edit permission overwrites, sync permissions, or list channels.",
    CHANNEL_SCHEMA,
    category="channels",
    actions=CHANNEL_ACTIONS,
)
async def manage_channel(ctx, args):
    operation = str(args["action"]).lower()

    if operation == "list":
        embed = discord.Embed(title=f"📚 Channels in {ctx.guild.name}", colour=discord.Colour.blurple())
        for category in ctx.guild.categories:
            children = [
                f"{'🔊' if isinstance(c, discord.VoiceChannel) else '💬'} {c.mention}"
                for c in category.channels
            ]
            embed.add_field(
                name=f"📁 {category.name} ({len(children)})",
                value=truncate(", ".join(children) or "—", 1024),
                inline=False,
            )
        uncategorised = [c for c in channels_of(ctx.guild) if getattr(c, "category", None) is None]
        if uncategorised:
            embed.add_field(
                name="No category",
                value=truncate(", ".join(c.mention for c in uncategorised), 1024),
                inline=False,
            )
        await ctx.say(embed=embed)
        return sent(f"Listed {len(channels_of(ctx.guild))} channels.")
    if operation == "create":
        kwargs, error = _create_kwargs(args)
        if error:
            return fail(error)
        category, error = category_or_error(ctx, args.get("category"))
        if error:
            return error
        if category is not None:
            kwargs["category"] = category
        if args.get("position") is not None:
            kwargs["position"] = max(0, int(args["position"]))
        reason = audit_reason(ctx, "Channel created")
        kind = kwargs.pop("type")
        if kind == discord.ChannelType.voice:
            created = await ctx.guild.create_voice_channel(reason=reason, **kwargs)
        elif kind == discord.ChannelType.category:
            created = await ctx.guild.create_category(reason=reason, **kwargs)
        elif kind == discord.ChannelType.stage_voice:
            created = await ctx.guild.create_stage_channel(reason=reason, **kwargs)
        elif kind == discord.ChannelType.forum:
            created = await ctx.guild.create_forum(reason=reason, **kwargs)
        elif kind == discord.ChannelType.news:
            created = await ctx.guild.create_text_channel(news=True, reason=reason, **kwargs)
        else:
            created = await ctx.guild.create_text_channel(reason=reason, **kwargs)
        return sent(f"Created {created.mention}.")

    channel, error = channel_or_error(ctx, args.get("channel"))
    if error:
        return error

    if operation == "delete":
        name = channel.name
        await channel.delete(reason=audit_reason(ctx, args.get("reason") or "Channel deleted"))
        return sent(f"Deleted **#{name}**.")

    if operation == "rename":
        name = sanitize_channel_name(args.get("name") or "")
        if not name:
            return fail("Tell me the new channel name, sir.")
        old = channel.name
        await channel.edit(name=name, reason=audit_reason(ctx, "Channel renamed"))
        await ctx.say(f"✏️ Renamed **#{old}** to **#{name}**.")
        return sent(f"Renamed #{old} to #{name}.")

    if operation == "move":
        kwargs = {}
        category, error = category_or_error(ctx, args.get("category"))
        if error:
            return error
        if category is not None:
            kwargs["category"] = category
        elif args.get("category") in {None, "", "none", "remove"}:
            kwargs["category"] = None
        if args.get("position") is not None:
            kwargs["position"] = max(0, int(args["position"]))
        if not kwargs:
            return fail("Tell me a category or a position, sir.")
        await channel.edit(**kwargs, reason=audit_reason(ctx, "Channel moved"))
        await ctx.say(f"📦 Moved {channel.mention}.")
        return sent("Channel moved.")

    if operation == "clone":
        clone = await channel.clone(reason=audit_reason(ctx, "Channel cloned"))
        if args.get("name"):
            await clone.edit(name=sanitize_channel_name(args["name"]))
        await ctx.say(f"🧬 Created {clone.mention} as a copy of {channel.mention}.")
        return sent(f"Cloned into {clone.mention}.")

    if operation in {"lock", "unlock"}:
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = operation == "unlock"
        if isinstance(channel, discord.TextChannel):
            overwrite.send_messages_in_threads = operation == "unlock"
        await channel.set_permissions(
            ctx.guild.default_role,
            overwrite=overwrite,
            reason=audit_reason(ctx, f"Channel {operation}ed"),
        )
        await ctx.say(
            f"{'🔒' if operation == 'lock' else '🔓'} {channel.mention} is now "
            f"**{operation}ed** for everyone."
        )
        return sent(f"Channel {operation}ed.")

    if operation == "slowmode":
        seconds = max(0, min(21600, int(args.get("seconds") or 0)))
        await channel.edit(slowmode_delay=seconds, reason=audit_reason(ctx, "Slowmode changed"))
        await ctx.say(
            f"🐢 Slowmode in {channel.mention} is now **{seconds}s**."
            if seconds
            else f"🐇 Slowmode disabled in {channel.mention}."
        )
        return sent(f"Slowmode set to {seconds}s.")

    if operation == "topic":
        topic = truncate(args.get("topic") or "", 1024)
        await channel.edit(topic=topic or None, reason=audit_reason(ctx, "Topic changed"))
        await ctx.say(f"📝 Updated the topic of {channel.mention}.")
        return sent("Topic updated.")

    if operation == "nsfw":
        value = boolean(args.get("nsfw"))
        await channel.edit(nsfw=value, reason=audit_reason(ctx, "NSFW flag changed"))
        await ctx.say(f"🔞 **#{channel.name}** NSFW is now **{'on' if value else 'off'}**.")
        return sent("NSFW flag updated.")

    if operation == "bitrate":
        kbps = max(8, min(384, int(args.get("bitrate") or 64)))
        await channel.edit(bitrate=kbps * 1000, reason=audit_reason(ctx, "Bitrate changed"))
        await ctx.say(f"🎚️ Bitrate for {channel.mention} is now **{kbps} kbps**.")
        return sent("Bitrate updated.")

    if operation == "user_limit":
        limit = max(0, min(99, int(args.get("user_limit") or 0)))
        await channel.edit(user_limit=limit, reason=audit_reason(ctx, "User limit changed"))
        await ctx.say(
            f"👥 User limit for {channel.mention} is now **{limit or 'unlimited'}**."
        )
        return sent("User limit updated.")

    if operation == "permissions":
        target = args.get("target") or ""
        if not target:
            return fail("Tell me which role or member to change permissions for, sir.")
        target_obj = ctx.resolve_role(target)
        if target_obj is None:
            target_obj = ctx.resolve_member(target)
        if target_obj is None:
            return fail(f"I couldn't find a role or member matching “{target}”, sir.")
        if isinstance(target_obj, discord.Role):
            problem = role_hierarchy_error(ctx, target_obj)
            if problem:
                return problem
        overwrite = channel.overwrites_for(target_obj)
        allow = parse_permissions(args.get("allow"))
        deny = parse_permissions(args.get("deny"))
        for name, value in allow:
            if value:
                setattr(overwrite, name, True)
        for name, value in deny:
            if value:
                setattr(overwrite, name, False)
        await channel.set_permissions(
            target_obj,
            overwrite=overwrite,
            reason=audit_reason(ctx, "Permission overwrite changed"),
        )
        await ctx.say(f"🛡️ Updated permissions for **{target_obj.name}** in {channel.mention}.")
        return sent("Permissions updated.")

    if operation == "sync":
        await channel.edit(sync_permissions=True, reason=audit_reason(ctx, "Permissions synced"))
        await ctx.say(f"♻️ Synced {channel.mention} with its category.")
        return sent("Permissions synced.")

    return fail(f"I don't know how to `{operation}` a channel, sir.")


CATEGORY_ACTIONS = {
    "create": action(Risk.MEDIUM, permission="manage_channels"),
    "delete": action(Risk.HIGH, permission="manage_channels", confirm=True),
    "rename": action(Risk.MEDIUM, permission="manage_channels"),
    "position": action(Risk.LOW, permission="manage_channels"),
    "list": action(Risk.SAFE),
}

CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(CATEGORY_ACTIONS)},
        "category": {"type": "string"},
        "name": {"type": "string"},
        "position": {"type": "integer"},
    },
    "required": ["action"],
}


@tool(
    "manage_category",
    "Create, delete, rename, reposition, or list channel categories.",
    CATEGORY_SCHEMA,
    category="channels",
    actions=CATEGORY_ACTIONS,
)
async def manage_category(ctx, args):
    operation = str(args["action"]).lower()
    if operation == "list":
        lines = [f"📁 **{c.name}** — {len(c.channels)} channel(s)" for c in ctx.guild.categories]
        await ctx.say("\n".join(lines) or "There are no categories, sir.")
        return sent("Listed categories.")
    if operation == "create":
        name = str(args.get("name") or "New Category").strip()[:100]
        category = await ctx.guild.create_category(name, reason=audit_reason(ctx, "Category created"))
        await ctx.say(f"📁 Created category **{category.name}**.")
        return sent("Category created.")
    category, error = category_or_error(ctx, args.get("category"))
    if error:
        return error
    if category is None:
        return fail("Tell me which category, sir.")
    if operation == "delete":
        name = category.name
        await category.delete(reason=audit_reason(ctx, "Category deleted"))
        return sent(f"Deleted category **{name}**.")
    if operation == "rename":
        name = str(args.get("name") or "").strip()[:100]
        if not name:
            return fail("Tell me the new category name, sir.")
        await category.edit(name=name, reason=audit_reason(ctx, "Category renamed"))
        await ctx.say(f"✏️ Renamed category to **{name}**.")
        return sent("Category renamed.")
    if operation == "position":
        position = max(0, int(args.get("position") or 0))
        await category.edit(position=position, reason=audit_reason(ctx, "Category moved"))
        return sent(f"Moved category to position {position}.")
    return fail(f"I don't know how to `{operation}` a category, sir.")


THREAD_ACTIONS = {
    "create": action(Risk.LOW, permission="create_public_threads"),
    "delete": action(Risk.MEDIUM, permission="manage_threads"),
    "rename": action(Risk.LOW, permission="manage_threads"),
    "lock": action(Risk.LOW, permission="manage_threads", confirm=False),
    "unlock": action(Risk.LOW, permission="manage_threads", confirm=False),
    "archive": action(Risk.LOW, permission="manage_threads", confirm=False),
    "unarchive": action(Risk.LOW, permission="manage_threads", confirm=False),
    "add_member": action(Risk.LOW, permission="manage_threads"),
    "remove_member": action(Risk.LOW, permission="manage_threads"),
    "list": action(Risk.SAFE),
}

THREAD_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(THREAD_ACTIONS)},
        "name": {"type": "string", "description": "Thread name or new name"},
        "channel": {"type": "string", "description": "Parent channel for create/list"},
        "message": {"type": "string", "description": "Message ID to start a thread from"},
        "user": {"type": "string", "description": "Member to add/remove"},
        "private": {"type": "boolean", "description": "Create a private thread"},
        "slowmode": {"type": "integer"},
    },
    "required": ["action"],
}


@tool(
    "manage_thread",
    "Create, delete, rename, lock, unlock, archive, add or remove members from threads, or list threads.",
    THREAD_SCHEMA,
    category="channels",
    actions=THREAD_ACTIONS,
)
async def manage_thread(ctx, args):
    operation = str(args["action"]).lower()
    if operation == "list":
        threads = list(ctx.guild.threads)
        for channel in ctx.guild.text_channels:
            threads.extend(channel.threads)
        if not threads:
            await ctx.say("There are no active threads, sir.")
            return sent("No threads.")
        lines = [
            f"🧵 {t.mention} — {'archived' if t.archived else 'active'}, {t.message_count} messages"
            for t in threads[:50]
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(threads)} thread(s).")

    if operation == "create":
        name = str(args.get("name") or "Jarvis thread").strip()[:100]
        parent, error = channel_or_error(ctx, args.get("channel"), default_current=True)
        if error:
            return error
        message_id = snowflake(args.get("message"))
        try:
            if isinstance(parent, discord.TextChannel) and message_id:
                message = await parent.fetch_message(message_id)
                thread = await message.create_thread(name=name)
            elif isinstance(parent, discord.TextChannel):
                thread = await parent.create_thread(
                    name=name,
                    type=discord.ChannelType.private_thread
                    if boolean(args.get("private"))
                    else discord.ChannelType.public_thread,
                )
            else:
                return fail("Threads can only be created in text channels, sir.")
        except discord.HTTPException as exc:
            return fail(f"Discord refused to create the thread: {truncate(exc, 200)}")
        await ctx.say(f"🧵 Created thread {thread.mention}.")
        return sent("Thread created.")

    thread, error = channel_or_error(ctx, args.get("channel") or args.get("name"))
    if error:
        return error
    if not isinstance(thread, discord.Thread):
        return fail("Tell me the thread name or mention, sir.")

    if operation == "delete":
        name = thread.name
        await thread.delete(reason=audit_reason(ctx, "Thread deleted"))
        return sent(f"Deleted thread **{name}**.")
    if operation == "rename":
        name = str(args.get("name") or "").strip()[:100]
        if not name:
            return fail("Tell me the new thread name, sir.")
        await thread.edit(name=name, reason=audit_reason(ctx, "Thread renamed"))
        return sent("Thread renamed.")
    if operation == "lock":
        await thread.edit(locked=True, reason=audit_reason(ctx, "Thread locked"))
        await ctx.say(f"🔒 Locked {thread.mention}.")
        return sent("Thread locked.")
    if operation == "unlock":
        await thread.edit(locked=False, reason=audit_reason(ctx, "Thread unlocked"))
        await ctx.say(f"🔓 Unlocked {thread.mention}.")
        return sent("Thread unlocked.")
    if operation in {"archive", "unarchive"}:
        await thread.edit(archived=operation == "archive", reason=audit_reason(ctx, "Thread archive"))
        await ctx.say(f"🗄️ Thread {thread.mention} **{operation}d**.")
        return sent(f"Thread {operation}d.")
    if operation in {"add_member", "remove_member"}:
        member = await ctx.resolve_member_async(args.get("user"))
        if member is None:
            return fail("Tell me which member, sir.")
        if operation == "add_member":
            await thread.add_user(member)
            await ctx.say(f"➕ Added {member.mention} to {thread.mention}.")
        else:
            await thread.remove_user(member)
            await ctx.say(f"➖ Removed {member.mention} from {thread.mention}.")
        return sent("Thread member updated.")
    return fail(f"I don't know how to `{operation}` a thread, sir.")
