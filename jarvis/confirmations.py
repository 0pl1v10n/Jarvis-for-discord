"""Confirmation and moderator-approval prompts.

Dangerous actions always get one confirmation from the person who asked.  If
they lack the permission, a qualified moderator/administrator can approve the
request instead.  Both flows accept buttons or a plain chat ``yes``/``no``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import discord

from .permissions import approval_roles, approver_qualifies, risk_label
from .ai.tools import Risk

YES = {
    "yes",
    "y",
    "yeah",
    "yep",
    "yup",
    "sure",
    "ok",
    "okay",
    "confirm",
    "confirmed",
    "approve",
    "approved",
    "do it",
    "go ahead",
    "affirmative",
    "please do",
    "yes please",
    "send it",
    "proceed",
}
NO = {
    "no",
    "n",
    "nope",
    "nah",
    "cancel",
    "stop",
    "deny",
    "denied",
    "reject",
    "abort",
    "negative",
    "nevermind",
    "never mind",
    "leave it",
    "dont",
    "don't",
}


def parse_yes_no(text: str) -> bool | None:
    cleaned = re.sub(r"[^\w\s']", "", str(text or "").lower()).strip()
    if not cleaned:
        return None
    if cleaned in YES:
        return True
    if cleaned in NO:
        return False
    first = cleaned.split()[0]
    if first in {"yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "approve", "approved"}:
        return True
    if first in {"no", "n", "nope", "nah", "cancel", "deny", "denied", "reject", "stop", "abort"}:
        return False
    return None


@dataclass
class PendingConfirmation:
    future: asyncio.Future
    user_id: int
    channel_id: int
    title: str
    description: str
    message: discord.Message | None = None
    view: discord.ui.View | None = None


@dataclass
class PendingApproval:
    future: asyncio.Future
    permission: str | tuple[str, ...] | None
    requester_id: int
    guild_id: int
    channel_id: int
    title: str
    description: str
    message: discord.Message | None = None
    view: discord.ui.View | None = None
    approver: discord.Member | None = None


class _ConfirmView(discord.ui.View):
    def __init__(self, future: asyncio.Future, user_id: int, timeout: float):
        super().__init__(timeout=timeout)
        self.future = future
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the person who asked can confirm this.", ephemeral=True
            )
            return False
        return True

    def _finish(self, value: bool) -> None:
        if not self.future.done():
            self.future.set_result(value)
        self.stop()

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self._finish(True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="🚫")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self._finish(False)


class _ApprovalView(discord.ui.View):
    def __init__(
        self,
        future: asyncio.Future,
        requester_id: int,
        permission,
        guild: discord.Guild | None,
        settings,
        timeout: float,
    ):
        super().__init__(timeout=timeout)
        self.future = future
        self.requester_id = requester_id
        self.permission = permission
        self.guild = guild
        self.settings = settings
        self.approver: discord.Member | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            await interaction.response.send_message(
                "You can't approve your own request.", ephemeral=True
            )
            return False
        if not approver_qualifies(
            interaction.user,
            self.permission,
            guild=self.guild,
            settings=self.settings,
        ):
            await interaction.response.send_message(
                "You don't have the permission needed to approve this.", ephemeral=True
            )
            return False
        return True

    def _finish(self, interaction: discord.Interaction, value: bool) -> None:
        self.approver = interaction.user
        if not self.future.done():
            self.future.set_result(value)
        self.stop()

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self._finish(interaction, True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="🚫")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self._finish(interaction, False)


class ConfirmationManager:
    def __init__(self, settings):
        self.settings = settings
        self._pending: dict[int, PendingConfirmation] = {}

    def has_pending(self, user_id: int) -> bool:
        pending = self._pending.get(user_id)
        return pending is not None and not pending.future.done()

    async def confirm(
        self,
        ctx,
        title: str,
        description: str,
        *,
        timeout: float = 150.0,
        risk: Risk = Risk.HIGH,
    ) -> bool:
        user_id = ctx.author_id
        old = self._pending.get(user_id)
        if old and not old.future.done():
            old.future.set_result(False)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        view = _ConfirmView(future, user_id, timeout + 60)
        pending = PendingConfirmation(
            future=future,
            user_id=user_id,
            channel_id=ctx.channel_id,
            title=title,
            description=description,
            view=view,
        )
        self._pending[user_id] = pending

        embed = discord.Embed(
            title=title,
            description=description,
            colour=discord.Colour.orange() if risk < Risk.CRITICAL else discord.Colour.red(),
        )
        embed.add_field(name="Requested by", value=ctx.author.mention if hasattr(ctx.author, "mention") else str(ctx.author))
        embed.add_field(name="Risk", value=risk_label(risk), inline=True)
        embed.set_footer(text="Press a button, or reply “yes” or “no”.")

        try:
            pending.message = await ctx.channel.send(embed=embed, view=view)
        except discord.HTTPException:
            self._pending.pop(user_id, None)
            view.stop()
            return False

        approved = False
        try:
            approved = bool(await asyncio.wait_for(future, timeout=timeout))
        except asyncio.TimeoutError:
            approved = False
        finally:
            self._pending.pop(user_id, None)
            view.stop()

        await self._finalize(pending, approved, timed_out=not future.done())
        return approved

    async def _finalize(self, pending: PendingConfirmation, approved: bool, *, timed_out: bool = False) -> None:
        if pending.message is None:
            return
        if timed_out:
            title, colour = "Request expired", discord.Colour.dark_grey()
        elif approved:
            title, colour = "Confirmed", discord.Colour.green()
        else:
            title, colour = "Cancelled", discord.Colour.red()
        embed = discord.Embed(title=title, description=pending.description, colour=colour)
        try:
            await pending.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass

    async def handle_message(self, message) -> bool:
        pending = self._pending.get(message.author.id)
        if pending is None or pending.future.done():
            return False
        if message.channel.id != pending.channel_id:
            return False
        value = parse_yes_no(message.content)
        if value is None:
            return False
        if not pending.future.done():
            pending.future.set_result(value)
        try:
            await message.add_reaction("✅" if value else "🚫")
        except discord.HTTPException:
            pass
        return True


class ApprovalManager:
    def __init__(self, bot):
        self.bot = bot
        self.settings = bot.settings
        self._pending: dict[int, list[PendingApproval]] = {}

    def has_pending(self, channel_id: int) -> bool:
        return any(not p.future.done() for p in self._pending.get(channel_id, []))

    async def request(
        self,
        ctx,
        title: str,
        description: str,
        permission,
        *,
        risk: Risk = Risk.HIGH,
        timeout: float = 300.0,
    ) -> tuple[bool, discord.Member | None]:
        guild = ctx.guild
        destination = ctx.channel
        if self.settings.approval_channel_id:
            configured = self.bot.get_channel(self.settings.approval_channel_id)
            if configured is not None:
                destination = configured

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        view = _ApprovalView(
            future,
            ctx.author_id,
            permission,
            guild,
            self.settings,
            timeout + 60,
        )
        pending = PendingApproval(
            future=future,
            permission=permission,
            requester_id=ctx.author_id,
            guild_id=ctx.guild_id,
            channel_id=destination.id,
            title=title,
            description=description,
            view=view,
        )

        embed = discord.Embed(title="Approval needed", description=description, colour=discord.Colour.orange())
        embed.add_field(name="Requested by", value=ctx.author.mention if hasattr(ctx.author, "mention") else str(ctx.author))
        embed.add_field(name="Risk", value=risk_label(risk), inline=True)
        embed.set_footer(text="A qualified moderator can approve or deny. Reply yes/no too.")

        ping = None
        allowed = discord.AllowedMentions.none()
        role_id, _ = approval_roles(self.settings, permission)
        if role_id:
            role = guild.get_role(role_id) if guild else None
            if role is not None:
                ping = role.mention
                allowed = discord.AllowedMentions(roles=True)
        elif guild and guild.owner_id:
            ping = f"<@{guild.owner_id}>"
            allowed = discord.AllowedMentions(users=True)

        try:
            pending.message = await destination.send(
                content=ping, embed=embed, view=view, allowed_mentions=allowed
            )
        except discord.HTTPException:
            view.stop()
            return False, None

        self._pending.setdefault(destination.id, []).append(pending)
        if destination.id != ctx.channel_id:
            try:
                await ctx.reply("I've asked a qualified moderator to approve that, sir.")
            except discord.HTTPException:
                pass

        approved = False
        try:
            approved = bool(await asyncio.wait_for(future, timeout=timeout))
        except asyncio.TimeoutError:
            approved = False
        finally:
            view.stop()
            try:
                self._pending.get(destination.id, []).remove(pending)
            except ValueError:
                pass

        approver = view.approver
        await self._finalize(pending, approved, approver, timed_out=not future.done())
        return approved, approver

    async def _finalize(
        self,
        pending: PendingApproval,
        approved: bool,
        approver: discord.Member | None,
        *,
        timed_out: bool = False,
    ) -> None:
        if pending.message is None:
            return
        if timed_out:
            title, colour = "Approval expired", discord.Colour.dark_grey()
        elif approved:
            title = f"Approved by {approver.display_name}" if approver else "Approved"
            colour = discord.Colour.green()
        else:
            title = f"Denied by {approver.display_name}" if approver else "Denied"
            colour = discord.Colour.red()
        embed = discord.Embed(title=title, description=pending.description, colour=colour)
        try:
            await pending.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass

    async def handle_message(self, message) -> bool:
        pending_list = self._pending.get(message.channel.id) or []
        if not pending_list or message.guild is None:
            return False
        value = parse_yes_no(message.content)
        if value is None:
            return False
        for pending in list(pending_list):
            if pending.future.done():
                continue
            if message.author.id == pending.requester_id:
                continue
            if not approver_qualifies(
                message.author,
                pending.permission,
                guild=message.guild,
                settings=self.settings,
            ):
                continue
            pending.view.approver = message.author
            if not pending.future.done():
                pending.future.set_result(value)
            try:
                await message.add_reaction("✅" if value else "🚫")
            except discord.HTTPException:
                pass
            return True
        return False
