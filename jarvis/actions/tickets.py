"""Support ticket system."""

from __future__ import annotations

import io

import discord

from ..ai.tools import Risk, action, tool
from ..util import sanitize_channel_name, truncate, utcnow
from .helpers import (
    audit_reason,
    channel_or_error,
    fail,
    role_hierarchy_error,
    role_or_error,
    sent,
)

ACTIONS = {
    "setup": action(Risk.HIGH, permission="manage_guild", confirm=True),
    "create": action(Risk.LOW),
    "close": action(Risk.MEDIUM, permission="manage_threads"),
    "claim": action(Risk.LOW, permission="manage_threads"),
    "add": action(Risk.LOW, permission="manage_threads"),
    "remove": action(Risk.LOW, permission="manage_threads"),
    "list": action(Risk.LOW, permission="manage_guild"),
    "transcript": action(Risk.MEDIUM, permission="manage_messages"),
    "delete": action(Risk.HIGH, permission="manage_channels", confirm=True),
    "panel": action(Risk.MEDIUM, permission="manage_guild"),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "channel": {"type": "string", "description": "Ticket channel"},
        "category": {"type": "string", "description": "Category for new tickets"},
        "role": {"type": "string", "description": "Staff role"},
        "user": {"type": "string", "description": "Member to add/remove"},
        "message": {"type": "string", "description": "Panel or welcome message"},
        "reason": {"type": "string"},
    },
    "required": ["action"],
}


@tool(
    "manage_tickets",
    "Set up and operate support tickets: create, claim, close, add/remove members, "
    "post a ticket panel, list tickets, or generate a transcript.",
    SCHEMA,
    category="community",
    actions=ACTIONS,
)
async def manage_tickets(ctx, args):
    operation = str(args["action"]).lower()
    guild_id = ctx.guild_id
    config = ctx.db.get_config(guild_id, "tickets", {}) or {}

    if operation == "setup":
        category = None
        if args.get("category"):
            category = next(
                (c for c in ctx.guild.categories if c.name.lower() == str(args["category"]).lower()),
                None,
            )
            if category is None:
                return fail("I couldn't find that category, sir.")
        role = None
        if args.get("role"):
            role, error = role_or_error(ctx, args.get("role"))
            if error:
                return error
            problem = role_hierarchy_error(ctx, role)
            if problem:
                return problem
        config = {
            "category_id": category.id if category else 0,
            "staff_role_id": role.id if role else 0,
            "message": str(args.get("message") or "Click the button below to open a support ticket."),
        }
        ctx.db.set_config(guild_id, "tickets", config)
        await ctx.say(
            "🎫 Ticket system configured. Use the **panel** action to post the opener."
        )
        return sent("Ticket system configured.")

    if operation == "panel":
        channel, error = channel_or_error(ctx, args.get("channel"), default_current=True)
        if error:
            return error
        await channel.send(
            content=str(config.get("message") or "Need help? Open a ticket."),
            view=TicketPanelView(ctx.bot),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return sent("Ticket panel posted.")

    if operation == "list":
        rows = ctx.db.tickets(guild_id, "open")
        if not rows:
            await ctx.say("There are no open tickets, sir.")
            return sent("No open tickets.")
        lines = [
            f"🎫 <#{row['channel_id']}> — opened by <@{row['user_id']}>"
            + (f", claimed by <@{row['claimed_by']}>" if row["claimed_by"] else "")
            for row in rows
        ]
        await ctx.say("\n".join(lines[:40]))
        return sent(f"Listed {len(rows)} tickets.")

    channel, error = channel_or_error(
        ctx, args.get("channel"), default_current=True
    )
    if error:
        return error
    ticket = ctx.db.ticket_by_channel(channel.id) if hasattr(channel, "id") else None

    if operation == "create":
        if ticket is not None:
            return fail("There is already an open ticket here, sir.")
        category = ctx.guild.get_channel(int(config.get("category_id") or 0))
        if not isinstance(category, discord.CategoryChannel):
            category = None
        staff = ctx.guild.get_role(int(config.get("staff_role_id") or 0))
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            ctx.author: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True, read_message_history=True
            ),
            ctx.guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True, manage_messages=True
            ),
        }
        if staff is not None:
            overwrites[staff] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
        try:
            new_channel = await ctx.guild.create_text_channel(
                name=sanitize_channel_name(f"ticket-{ctx.display_name}"),
                category=category,
                overwrites=overwrites,
                reason=audit_reason(ctx, "Ticket opened"),
            )
        except discord.HTTPException as exc:
            return fail(f"Discord refused to create the ticket: {truncate(exc, 200)}")
        ticket_id = ctx.db.add_ticket(guild_id, new_channel.id, ctx.author_id)
        embed = discord.Embed(
            title=f"🎫 Ticket #{ticket_id}",
            description=str(config.get("message") or "Describe your issue and a staff member will help you."),
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="Opened by", value=ctx.author.mention, inline=True)
        embed.add_field(name="Staff", value=staff.mention if staff else "—", inline=True)
        await new_channel.send(
            content=ctx.author.mention + (f" {staff.mention}" if staff else ""),
            embed=embed,
            view=TicketControlsView(ctx.bot),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        await ctx.say(f"🎫 Opened {new_channel.mention}.")
        return sent("Ticket opened.")

    if ticket is None:
        return fail("This channel isn't an open ticket, sir.")

    if operation == "claim":
        ctx.db.claim_ticket(channel.id, ctx.author_id)
        await channel.send(f"🙋 {ctx.author.mention} has claimed this ticket.")
        return sent("Ticket claimed.")

    if operation in {"add", "remove"}:
        member = await ctx.resolve_member_async(args.get("user"))
        if member is None:
            return fail("Tell me which member, sir.")
        overwrite = channel.overwrites_for(member)
        overwrite.view_channel = operation == "add"
        await channel.set_permissions(
            member,
            overwrite=overwrite,
            reason=audit_reason(ctx, f"Ticket member {operation}"),
        )
        await ctx.say(
            f"{'➕ Added' if operation == 'add' else '➖ Removed'} {member.mention} "
            f"{'to' if operation == 'add' else 'from'} the ticket."
        )
        return sent("Ticket member updated.")

    if operation == "transcript":
        lines = []
        async for message in channel.history(limit=1000, oldest_first=True):
            lines.append(
                f"[{message.created_at.isoformat()}] {message.author} ({message.author.id}): "
                f"{message.content}"
            )
        data = "\n".join(lines) or "No messages."
        buffer = io.BytesIO(data.encode("utf-8"))
        await ctx.say(
            files=[discord.File(buffer, filename=f"ticket-{channel.id}-transcript.txt")]
        )
        return sent("Transcript generated.")

    if operation == "close":
        ctx.db.close_ticket(channel.id, ctx.author_id)
        log_channel = ctx.guild.get_channel(int(config.get("log_channel_id") or 0))
        if log_channel is not None:
            await log_channel.send(
                f"🎫 Ticket {channel.mention} closed by {ctx.author.mention}."
            )
        await channel.send("🔒 This ticket has been closed. Deleting the channel in 10 seconds.")
        import asyncio

        await asyncio.sleep(10)
        try:
            await channel.delete(reason=audit_reason(ctx, "Ticket closed"))
        except discord.HTTPException:
            pass
        return sent("Ticket closed.")

    if operation == "delete":
        ctx.db.close_ticket(channel.id, ctx.author_id)
        await channel.delete(reason=audit_reason(ctx, args.get("reason") or "Ticket deleted"))
        return sent("Ticket deleted.")

    return fail(f"I don't know how to `{operation}` a ticket, sir.")


class TicketPanelView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Open a ticket",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="jarvis:ticket:open",
    )
    async def open_ticket(self, interaction: discord.Interaction, _button: discord.ui.Button):
        from ..context import ActionContext

        ctx = ActionContext(
            bot=self.bot,
            message=interaction.message,
            guild=interaction.guild,
            channel=interaction.channel,
            author=interaction.user,
            member=interaction.user if isinstance(interaction.user, discord.Member) else None,
        )
        config = self.bot.db.get_config(interaction.guild_id, "tickets", {}) or {}
        existing = next(
            (
                row
                for row in self.bot.db.tickets(interaction.guild_id, "open")
                if int(row["user_id"]) == interaction.user.id
            ),
            None,
        )
        if existing:
            await interaction.response.send_message(
                f"You already have an open ticket: <#{existing['channel_id']}>.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await manage_tickets(
            ctx,
            {"action": "create", "message": config.get("message")},
        )
        await interaction.followup.send("Your ticket has been opened.", ephemeral=True)


class TicketControlsView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Claim",
        emoji="🙋",
        style=discord.ButtonStyle.success,
        custom_id="jarvis:ticket:claim",
    )
    async def claim(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.bot.db.claim_ticket(interaction.channel_id, interaction.user.id)
        await interaction.response.send_message(
            f"🙋 {interaction.user.mention} has claimed this ticket.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @discord.ui.button(
        label="Close",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="jarvis:ticket:close",
    )
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.bot.db.close_ticket(interaction.channel_id, interaction.user.id)
        await interaction.response.send_message("🔒 Closing this ticket in 10 seconds.")
        import asyncio

        await asyncio.sleep(10)
        try:
            await interaction.channel.delete(reason="Ticket closed by button")
        except discord.HTTPException:
            pass


__all__ = ["manage_tickets", "TicketPanelView", "TicketControlsView"]
