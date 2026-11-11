"""Inspect and maintain Jarvis's Discord channel data store."""

from __future__ import annotations

from ..ai.tools import Risk, action, tool
from ..util import truncate
from .helpers import sent

ACTIONS = {
    "status": action(Risk.SAFE),
    "setup": action(Risk.MEDIUM, permission="manage_guild"),
    "refresh": action(Risk.MEDIUM, permission="manage_guild"),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
    },
    "required": ["action"],
}


@tool(
    "manage_storage",
    "Show where Jarvis keeps its Discord channel data store, create the private "
    "storage category/channels, or re-read them from Discord.",
    SCHEMA,
    category="server",
    actions=ACTIONS,
)
async def manage_storage(ctx, args):
    operation = str(args["action"]).lower()

    if operation == "status":
        await ctx.say(_status_text(ctx))
        return sent("Showed data storage status.")

    if operation == "setup":
        await ctx.db.ensure_all_channels(ctx.guild)
        await ctx.db.flush()
        await ctx.say("📦 Data storage is ready.\n" + _status_text(ctx))
        return sent("Data storage set up.")

    if operation == "refresh":
        await ctx.db.refresh_guild(ctx.guild)
        await ctx.say("🔄 Re-read the data store from Discord.\n" + _status_text(ctx))
        return sent("Data store refreshed.")

    return sent(f"I don't know how to `{operation}` the data store, sir.")


def _status_text(ctx) -> str:
    lines = [
        f"📦 **Channel data store** — category `{ctx.db.category_name}`",
        "Data lives in private channels; each channel holds the latest JSON snapshot.",
    ]
    for row in ctx.db.summary(ctx.guild_id):
        domain = str(row["domain"])
        count = int(row["records"])
        channel_id = int(row["channel_id"])
        if channel_id:
            lines.append(
                f"• `{truncate(domain, 24)}` — {count} record(s) — <#{channel_id}>"
            )
        else:
            lines.append(f"• `{truncate(domain, 24)}` — {count} record(s) — not created yet")
    return "\n".join(lines)


__all__ = ["manage_storage"]
