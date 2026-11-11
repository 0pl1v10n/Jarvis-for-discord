"""Personal reminders."""

from __future__ import annotations

from datetime import datetime

import discord

from ..ai.tools import Risk, action, tool
from ..util import format_dt, human_delta, parse_time, truncate, utcnow
from .helpers import fail, int_between, sent

ACTIONS = {
    "create": action(Risk.LOW, cooldown=2),
    "list": action(Risk.SAFE),
    "cancel": action(Risk.LOW),
    "clear": action(Risk.MEDIUM),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "message": {"type": "string", "description": "What to remind the user about"},
        "when": {"type": "string", "description": "e.g. 'in 20m', 'tomorrow 09:00', '2026-05-01 18:00'"},
        "duration": {"type": "string", "description": "Alias for when"},
        "identifier": {"type": "integer", "description": "Reminder ID to cancel"},
    },
    "required": ["action"],
}


@tool(
    "manage_reminder",
    "Create, list, cancel, or clear personal reminders.",
    SCHEMA,
    category="utility",
    actions=ACTIONS,
)
async def manage_reminder(ctx, args):
    operation = str(args["action"]).lower()

    if operation == "create":
        message = str(args.get("message") or "").strip()
        if not message:
            return fail("Tell me what to remind you about, sir.")
        when = parse_time(args.get("when") or args.get("duration"))
        if when is None:
            return fail("Tell me when, for example “in 20 minutes” or “tomorrow at 09:00”, sir.")
        if when <= utcnow():
            return fail("That time is in the past, sir.")
        reminder_id = ctx.db.add_reminder(
            ctx.author_id,
            ctx.channel_id,
            ctx.guild_id,
            truncate(message, 1500),
            when.isoformat(),
        )
        await ctx.say(
            f"⏰ I'll remind you {format_dt(when)} ({format_dt(when, 'f')}) — reminder `{reminder_id}`."
        )
        return sent("Reminder created.")

    if operation == "list":
        rows = ctx.db.reminders(ctx.author_id)
        if not rows:
            await ctx.say("You have no reminders, sir.")
            return sent("No reminders.")
        lines = [
            f"`{row['id']}` — {format_dt(_parse(row['remind_at']))}: {truncate(row['message'], 120)}"
            for row in rows[:30]
        ]
        await ctx.say("\n".join(lines))
        return sent(f"Listed {len(rows)} reminders.")

    if operation == "clear":
        rows = ctx.db.reminders(ctx.author_id)
        for row in rows:
            ctx.db.delete_reminder(ctx.author_id, int(row["id"]))
        await ctx.say(f"🧹 Cleared **{len(rows)}** reminder(s).")
        return sent("Reminders cleared.")

    reminder_id = int_between(args.get("identifier"), 0, 2**31, 0)
    if not reminder_id:
        return fail("Tell me the reminder ID, sir.")
    if not ctx.db.delete_reminder(ctx.author_id, reminder_id):
        return fail("I couldn't find that reminder, sir.")
    await ctx.say(f"🗑️ Cancelled reminder `{reminder_id}`.")
    return sent("Reminder cancelled.")


def _parse(value: str):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


__all__ = ["manage_reminder"]
