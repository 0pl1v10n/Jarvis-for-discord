"""Message sending, editing, purging, reactions, embeds, and polls."""

from __future__ import annotations

import json
from datetime import timedelta

import discord

from ..ai.tools import Risk, action, tool
from ..util import (
    parse_emoji,
    parse_colour,
    snowflake,
    truncate,
)
from .helpers import (
    audit_reason,
    boolean,
    channel_or_error,
    fail,
    int_between,
    message_embed,
    message_or_error,
    ok,
    sent,
)

ACTIONS = {
    "send": action(Risk.LOW, permission="send_messages"),
    "reply": action(Risk.LOW, permission="send_messages"),
    "edit": action(Risk.MEDIUM, permission="manage_messages"),
    "delete": action(Risk.MEDIUM, permission="manage_messages"),
    "purge": action(Risk.HIGH, permission="manage_messages", confirm=True),
    "pin": action(Risk.LOW, permission="manage_messages", confirm=False),
    "unpin": action(Risk.LOW, permission="manage_messages", confirm=False),
    "react": action(Risk.LOW, permission="add_reactions"),
    "unreact": action(Risk.LOW, permission="manage_messages", confirm=False),
    "embed": action(Risk.LOW, permission="send_messages"),
    "poll": action(Risk.LOW, permission="send_messages"),
    "info": action(Risk.SAFE),
    "download": action(Risk.SAFE),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "channel": {"type": "string", "description": "Target channel (defaults to here)"},
        "message": {
            "type": "string",
            "description": "Message link, ID, 'last', or 'reply'",
        },
        "content": {"type": "string", "description": "Message text"},
        "limit": {"type": "integer", "description": "How many messages to purge (1-500)"},
        "user": {"type": "string", "description": "Only purge messages from this member"},
        "contains": {"type": "string", "description": "Only purge messages containing this text"},
        "emoji": {"type": "string", "description": "Emoji for react/unreact"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "colour": {"type": "string", "description": "Embed colour name or hex"},
        "url": {"type": "string", "description": "Embed URL or image URL"},
        "image": {"type": "string", "description": "Image URL"},
        "thumbnail": {"type": "string", "description": "Thumbnail URL"},
        "footer": {"type": "string"},
        "author": {"type": "string"},
        "fields": {
            "type": "string",
            "description": 'JSON array of {"name","value","inline"} fields',
        },
        "question": {"type": "string", "description": "Poll question"},
        "choices": {"type": "array", "items": {"type": "string"}},
        "hours": {"type": "number", "description": "Poll duration in hours"},
        "multiple": {"type": "boolean", "description": "Allow multiple poll answers"},
        "silent": {"type": "boolean", "description": "Suppress notifications"},
    },
    "required": ["action"],
}


@tool(
    "manage_message",
    "Send, reply, edit, delete, purge, pin, react to, or inspect messages; create rich embeds and polls.",
    SCHEMA,
    category="messages",
    actions=ACTIONS,
)
async def manage_message(ctx, args):
    operation = str(args["action"]).lower()

    if operation in {"send", "reply", "embed", "poll"}:
        channel, error = channel_or_error(ctx, args.get("channel"), default_current=True)
        if error:
            return error
        silent = boolean(args.get("silent"))
        allowed = discord.AllowedMentions.none() if silent else discord.AllowedMentions(
            everyone=False, roles=False, users=True
        )

        async def dispatch(**kwargs):
            if operation == "reply" and hasattr(ctx.message, "reply"):
                return await ctx.message.reply(**kwargs)
            return await channel.send(**kwargs)

        if operation == "poll":
            question = str(args.get("question") or args.get("content") or "").strip()
            if not question:
                return fail("Tell me the poll question, sir.")
            choices = args.get("choices") or []
            if isinstance(choices, str):
                choices = [part.strip() for part in choices.split("|") if part.strip()]
            choices = [str(c)[:55] for c in choices][:10]
            if len(choices) < 2:
                return fail("A poll needs at least two choices, sir.")
            hours = max(0.01, min(168.0, float(args.get("hours") or 24)))
            poll = discord.Poll(
                question=truncate(question, 300),
                duration=timedelta(hours=hours),
                multiple=boolean(args.get("multiple")),
            )
            for choice in choices:
                poll.add_answer(text=choice)
            message = await dispatch(
                poll=poll,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return sent(f"Created a poll ({message.jump_url}).")

        if operation == "embed":
            embed = _build_embed(ctx, args)
            if embed is None:
                return fail("Give me at least a title or description for the embed, sir.")
            message = await dispatch(embed=embed, allowed_mentions=allowed)
            return sent(f"Sent an embed ({message.jump_url}).")

        content = str(args.get("content") or "").strip()
        if not content:
            return fail("Tell me what to send, sir.")
        message = await dispatch(content=truncate(content, 2000), allowed_mentions=allowed)
        return sent(f"Sent the message ({message.jump_url}).")

    if operation == "purge":
        channel, error = channel_or_error(ctx, args.get("channel"), default_current=True)
        if error:
            return error
        limit = int_between(args.get("limit"), 1, 500, 20)
        target_member = None
        if args.get("user"):
            target_member = await ctx.resolve_member_async(args.get("user"))
            if target_member is None:
                return fail(f"I couldn't find a member matching “{args.get('user')}”, sir.")
        contains = str(args.get("contains") or "").lower().strip()

        def check(message: discord.Message) -> bool:
            if target_member is not None and message.author.id != target_member.id:
                return False
            if contains and contains not in (message.content or "").lower():
                return False
            return True

        try:
            deleted = await channel.purge(
                limit=limit,
                check=check,
                reason=audit_reason(ctx, args.get("reason") or "Message purge"),
            )
        except discord.HTTPException as exc:
            return fail(f"Discord refused the purge: {truncate(exc, 200)}")
        await ctx.say(f"🧹 Deleted **{len(deleted)}** message(s) in {channel.mention}.")
        return sent(f"Purged {len(deleted)} messages.")

    message, error = await message_or_error(ctx, args.get("message"))
    if error:
        return error

    if operation == "edit":
        content = str(args.get("content") or "").strip()
        if not content:
            return fail("Tell me the new message text, sir.")
        if message.author.id != ctx.bot.user.id:
            return fail("I can only edit my own messages, sir.")
        await message.edit(content=truncate(content, 2000))
        return sent("Message edited.")

    if operation == "delete":
        if message.author.id != ctx.bot.user.id and not ctx.can("manage_messages"):
            return fail("You need manage messages to delete that, sir.")
        await message.delete()
        return sent("Message deleted.")

    if operation in {"pin", "unpin"}:
        if operation == "pin":
            await message.pin(reason=audit_reason(ctx, "Message pinned"))
        else:
            await message.unpin(reason=audit_reason(ctx, "Message unpinned"))
        return sent(f"Message {operation}ned.")

    if operation == "react":
        emoji = parse_emoji(args.get("emoji"))
        if not emoji:
            return fail("Tell me which emoji to react with, sir.")
        await message.add_reaction(emoji)
        return sent(f"Reacted with {emoji}.")

    if operation == "unreact":
        emoji = parse_emoji(args.get("emoji"))
        if emoji:
            await message.remove_reaction(emoji, ctx.bot.user)
            await message.remove_reaction(emoji, ctx.author)
        else:
            await message.clear_reactions()
        return sent("Reactions removed.")

    if operation == "info":
        await ctx.say(embed=message_embed(message))
        return sent("Showed message information.")

    if operation == "download":
        if not message.attachments:
            return fail("That message has no attachments, sir.")
        links = "\n".join(
            f"📎 [{a.filename}]({a.url}) — {a.size // 1024} KB" for a in message.attachments[:20]
        )
        await ctx.say(links)
        return sent("Listed attachments.")

    return fail(f"I don't know how to `{operation}` a message, sir.")


def _build_embed(ctx, args) -> discord.Embed | None:
    title = str(args.get("title") or "").strip()
    description = str(args.get("description") or args.get("content") or "").strip()
    if not title and not description:
        return None
    embed = discord.Embed(
        title=truncate(title, 256) or None,
        description=truncate(description, 4000) or None,
        colour=parse_colour(args.get("colour")),
        url=str(args["url"]) if args.get("url", "").startswith("http") else None,
    )
    if args.get("image"):
        embed.set_image(url=str(args["image"]))
    if args.get("thumbnail"):
        embed.set_thumbnail(url=str(args["thumbnail"]))
    if args.get("footer"):
        embed.set_footer(text=truncate(args["footer"], 2048))
    if args.get("author"):
        embed.set_author(name=truncate(args["author"], 256))
    fields = args.get("fields")
    if isinstance(fields, str) and fields.strip():
        try:
            fields = json.loads(fields)
        except json.JSONDecodeError:
            fields = None
    if isinstance(fields, list):
        for field in fields[:25]:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "—")
            value = str(field.get("value") or "—")
            embed.add_field(name=truncate(name, 256), value=truncate(value, 1024), inline=bool(field.get("inline")))
    return embed


BULK_ACTIONS = {
    "clear_reactions": action(Risk.MEDIUM, permission="manage_messages", confirm=False),
    "delete_user_messages": action(Risk.HIGH, permission="manage_messages", confirm=True),
    "forward": action(Risk.LOW, permission="send_messages"),
}

BULK_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(BULK_ACTIONS)},
        "message": {"type": "string"},
        "channel": {"type": "string"},
        "user": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "required": ["action"],
}


@tool(
    "bulk_message",
    "Clear reactions, delete every recent message from one member, or forward a message.",
    BULK_SCHEMA,
    category="messages",
    actions=BULK_ACTIONS,
)
async def bulk_message(ctx, args):
    operation = str(args["action"]).lower()
    channel, error = channel_or_error(ctx, args.get("channel"), default_current=True)
    if error:
        return error

    if operation == "forward":
        message, error = await message_or_error(ctx, args.get("message"))
        if error:
            return error
        copied = await channel.send(
            content=f"↪️ Forwarded from **{message.author}**:\n{truncate(message.content, 1800)}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if message.embeds:
            await channel.send(embeds=message.embeds[:3])
        if message.attachments:
            await channel.send("\n".join(a.url for a in message.attachments[:5]))
        return sent(f"Forwarded the message ({copied.jump_url}).")

    if operation == "clear_reactions":
        message, error = await message_or_error(ctx, args.get("message"))
        if error:
            return error
        await message.clear_reactions()
        return sent("Cleared every reaction.")

    if operation == "delete_user_messages":
        member = await ctx.resolve_member_async(args.get("user"))
        if member is None:
            return fail("Tell me which member's messages to delete, sir.")
        limit = int_between(args.get("limit"), 1, 500, 50)
        deleted = await channel.purge(
            limit=limit,
            check=lambda m: m.author.id == member.id,
            reason=audit_reason(ctx, "User message cleanup"),
        )
        await ctx.say(f"🧹 Deleted **{len(deleted)}** message(s) from {member.mention}.")
        return sent(f"Deleted {len(deleted)} messages.")

    return fail(f"I don't know how to `{operation}` messages, sir.")
