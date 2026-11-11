"""Event-driven community features and periodic background jobs."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import timedelta

import discord

from ..actions.community import _counter_value, _render as render_message
from ..actions.giveaways import GiveawayView, finish_giveaway
from ..actions.tickets import TicketControlsView, TicketPanelView
from ..util import format_dt, human_delta, parse_emoji, truncate, utcnow

log = logging.getLogger(__name__)


class Features:
    def __init__(self, bot):
        self.bot = bot
        self.temp_channels: dict[int, int] = {}
        self._task: asyncio.Task | None = None

    # -- startup -----------------------------------------------------------
    async def startup(self) -> None:
        self._register_views()
        self._task = asyncio.create_task(self._periodic())

    def _register_views(self) -> None:
        """Re-attach persistent buttons after a restart."""
        try:
            self.bot.add_view(TicketPanelView(self.bot))
            self.bot.add_view(TicketControlsView(self.bot))
            for row in self.bot.db.active_giveaways():
                self.bot.add_view(GiveawayView(self.bot, int(row["id"])))
        except Exception:
            log.exception("Could not register persistent views")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- logging helpers ---------------------------------------------------
    def _log_channel(self, guild: discord.Guild):
        config = self.bot.db.get_config(guild.id, "logging", {}) or {}
        return guild.get_channel(int(config.get("channel_id") or 0))

    async def _log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        channel = self._log_channel(guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    # -- messages ----------------------------------------------------------
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        await self._autoresponses(message)
        await self._leveling(message)

    async def _autoresponses(self, message: discord.Message) -> None:
        row = self.bot.db.match_autoresponse(message.guild.id, message.content)
        if row is None:
            return
        try:
            await message.channel.send(
                truncate(row["response"], 2000),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass

    async def _leveling(self, message: discord.Message) -> None:
        config = self.bot.db.get_config(message.guild.id, "leveling", {}) or {}
        if not config.get("enabled"):
            return
        if len(message.content.strip()) < 2:
            return
        result = self.bot.db.add_xp(
            message.guild.id,
            message.author.id,
            random.randint(15, 25),
            utcnow().timestamp(),
        )
        if not result or not result["leveled"]:
            return
        channel = message.guild.get_channel(int(config.get("channel_id") or 0)) or message.channel
        template = str(
            config.get("message")
            or "🎉 {user} reached level **{level}**!"
        )
        text = template.replace("{user}", message.author.mention).replace(
            "{level}", str(result["level"])
        ).replace("{name}", message.author.display_name)
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions(users=True))
        except discord.HTTPException:
            pass
        rewards = config.get("rewards", {}) or {}
        role_id = rewards.get(str(result["level"]))
        if role_id:
            role = message.guild.get_role(int(role_id))
            if role is not None:
                try:
                    await message.author.add_roles(role, reason="Level reward")
                except discord.HTTPException:
                    pass

    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        embed = discord.Embed(
            title="🗑️ Message deleted",
            description=truncate(message.content or "*No text*", 3000),
            colour=discord.Colour.red(),
            timestamp=utcnow(),
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(name="Author", value=message.author.mention, inline=True)
        if message.attachments:
            embed.add_field(
                name="Attachments",
                value="\n".join(a.filename for a in message.attachments[:10]),
                inline=False,
            )
        await self._log(message.guild, embed)

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.guild is None or after.author.bot or before.content == after.content:
            return
        embed = discord.Embed(
            title="✏️ Message edited",
            colour=discord.Colour.orange(),
            timestamp=utcnow(),
        )
        embed.set_author(name=str(after.author), icon_url=after.author.display_avatar.url)
        embed.add_field(name="Before", value=truncate(before.content or "*empty*", 1000), inline=False)
        embed.add_field(name="After", value=truncate(after.content or "*empty*", 1000), inline=False)
        embed.add_field(name="Jump", value=f"[link]({after.jump_url})", inline=False)
        await self._log(after.guild, embed)

    # -- membership --------------------------------------------------------
    async def on_member_join(self, member: discord.Member) -> None:
        config = self.bot.db.get_config(member.guild.id, "welcome", {}) or {}
        channel = member.guild.get_channel(int(config.get("channel_id") or 0))
        if channel is not None:
            try:
                await channel.send(
                    render_message(config.get("message") or "Welcome, {user}!", member, member.guild),
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                pass
        for role_id in self.bot.db.get_config(member.guild.id, "autoroles", []) or []:
            role = member.guild.get_role(int(role_id))
            if role is not None:
                try:
                    await member.add_roles(role, reason="Autorole")
                except discord.HTTPException:
                    pass
        embed = discord.Embed(
            title="📥 Member joined",
            description=f"{member.mention} ({member})",
            colour=discord.Colour.green(),
            timestamp=utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Account created", value=format_dt(member.created_at), inline=True)
        embed.add_field(name="Members", value=str(member.guild.member_count), inline=True)
        await self._log(member.guild, embed)

    async def on_member_remove(self, member: discord.Member) -> None:
        config = self.bot.db.get_config(member.guild.id, "goodbye", {}) or {}
        channel = member.guild.get_channel(int(config.get("channel_id") or 0))
        if channel is not None:
            try:
                await channel.send(
                    render_message(config.get("message") or "{user} left.", member, member.guild),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                pass
        embed = discord.Embed(
            title="📤 Member left",
            description=f"{member} (`{member.id}`)",
            colour=discord.Colour.red(),
            timestamp=utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Joined", value=format_dt(member.joined_at), inline=True)
        await self._log(member.guild, embed)

    # -- reactions ---------------------------------------------------------
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        emoji = str(payload.emoji)
        await self._reaction_role(guild, payload, emoji, add=True)
        await self._starboard(guild, payload, emoji, add=True)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        emoji = str(payload.emoji)
        await self._reaction_role(guild, payload, emoji, add=False)
        await self._starboard(guild, payload, emoji, add=False)

    async def _reaction_role(self, guild, payload, emoji: str, *, add: bool) -> None:
        row = self.bot.db.reaction_role_for(payload.message_id, emoji)
        if row is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return
        role = guild.get_role(int(row["role_id"]))
        if role is None:
            return
        try:
            if add:
                await member.add_roles(role, reason="Reaction role")
            else:
                await member.remove_roles(role, reason="Reaction role")
        except discord.HTTPException:
            pass

    async def _starboard(self, guild, payload, emoji: str, *, add: bool) -> None:
        config = self.bot.db.get_config(guild.id, "starboard", {}) or {}
        if not config:
            return
        wanted = parse_emoji(config.get("emoji")) or "⭐"
        if emoji != wanted:
            return
        channel = guild.get_channel(int(config.get("channel_id") or 0))
        if channel is None:
            return
        source_channel = guild.get_channel(payload.channel_id)
        if source_channel is None:
            return
        try:
            message = await source_channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return
        count = next(
            (reaction.count for reaction in message.reactions if str(reaction.emoji) == wanted),
            0,
        )
        threshold = max(1, int(config.get("threshold") or 3))
        entry = self.bot.db.starboard_entry(message.id)
        if count >= threshold:
            if entry is None:
                embed = _starboard_embed(message, count)
                try:
                    posted = await channel.send(embed=embed)
                except discord.HTTPException:
                    return
                self.bot.db.add_starboard(
                    message.id, guild.id, message.channel.id, posted.id, message.author.id
                )
            else:
                try:
                    posted = await channel.fetch_message(int(entry["starboard_message_id"] or 0))
                    embed = _starboard_embed(message, count)
                    await posted.edit(embed=embed)
                except discord.HTTPException:
                    pass
        elif entry is not None:
            try:
                posted = await channel.fetch_message(int(entry["starboard_message_id"] or 0))
                await posted.delete()
            except discord.HTTPException:
                pass
            self.bot.db.remove_starboard(message.id)

    # -- voice -------------------------------------------------------------
    async def on_voice_state_update(self, member, before, after) -> None:
        if member.bot or member.guild is None:
            return
        config = self.bot.db.get_config(member.guild.id, "tempvoice", {}) or {}
        if config:
            hub = member.guild.get_channel(int(config.get("hub_id") or 0))
            if hub is not None and after.channel is not None and after.channel.id == hub.id:
                category = member.guild.get_channel(int(config.get("category_id") or 0))
                name = str(config.get("template") or "{user}'s channel").replace(
                    "{user}", member.display_name
                )[:90]
                try:
                    created = await member.guild.create_voice_channel(
                        name=name,
                        category=category if isinstance(category, discord.CategoryChannel) else None,
                        reason="Temporary voice channel",
                    )
                    self.temp_channels[created.id] = hub.id
                    try:
                        await member.move_to(created)
                    except discord.HTTPException:
                        pass
                except discord.HTTPException:
                    pass
        if before.channel is not None and before.channel.id in self.temp_channels:
            channel = before.channel
            if len(channel.members) == 0:
                try:
                    await channel.delete(reason="Temporary voice channel empty")
                except discord.HTTPException:
                    pass
                self.temp_channels.pop(channel.id, None)

    # -- periodic tasks ----------------------------------------------------
    async def _periodic(self) -> None:
        await self.bot.wait_until_ready()
        counter_tick = 0
        while not self.bot.is_closed():
            try:
                await self._run_reminders()
                await self._run_giveaways()
                await self._run_scheduled()
                counter_tick += 1
                if counter_tick >= 20:  # roughly every 10 minutes
                    counter_tick = 0
                    await self._run_counters()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Background task cycle failed")
            await asyncio.sleep(30)

    async def _run_reminders(self) -> None:
        due = self.bot.db.due_reminders(utcnow().isoformat(), limit=20)
        for row in due:
            user = self.bot.get_user(int(row["user_id"]))
            channel = self.bot.get_channel(int(row["channel_id"]))
            text = f"⏰ Reminder: {truncate(row['message'], 1500)}"
            delivered = False
            if channel is not None:
                try:
                    await channel.send(
                        f"<@{row['user_id']}> {text}",
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
                    delivered = True
                except discord.HTTPException:
                    pass
            if not delivered and user is not None:
                try:
                    await user.send(text)
                except discord.HTTPException:
                    pass
            self.bot.db.complete_reminder(int(row["id"]))

    async def _run_giveaways(self) -> None:
        for row in self.bot.db.due_giveaways(utcnow().isoformat(), limit=10):
            try:
                await finish_giveaway(self.bot, int(row["id"]))
            except Exception:
                log.exception("Giveaway %s could not be finished", row["id"])

    async def _run_scheduled(self) -> None:
        for row in self.bot.db.due_scheduled(utcnow().isoformat(), limit=20):
            guild = self.bot.get_guild(int(row["guild_id"]))
            if guild is not None and row["kind"] == "unban":
                try:
                    user = discord.Object(id=int(row["target_id"]))
                    await guild.unban(user, reason="Temporary ban expired")
                except discord.HTTPException:
                    pass
            self.bot.db.complete_scheduled(int(row["id"]))

    async def _run_counters(self) -> None:
        for guild in self.bot.guilds:
            counters = self.bot.db.get_config(guild.id, "counters", []) or []
            for counter in counters:
                channel = guild.get_channel(int(counter.get("channel_id") or 0))
                if channel is None:
                    continue
                kind = counter.get("kind", "members")
                value = _counter_value(guild, kind)
                template = str(counter.get("template") or f"{kind.title()}: {{count}}")
                name = template.replace("{count}", f"{value:,}")[:100]
                if channel.name == name:
                    continue
                try:
                    await channel.edit(name=name, reason="Stat counter update")
                except discord.HTTPException:
                    pass


def _starboard_embed(message: discord.Message, count: int) -> discord.Embed:
    embed = discord.Embed(
        description=truncate(message.content or "*No text*", 3500),
        colour=discord.Colour.gold(),
        timestamp=message.created_at,
    )
    embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
    embed.add_field(name="Source", value=f"[Jump]({message.jump_url})", inline=True)
    embed.add_field(name="Stars", value=f"⭐ {count}", inline=True)
    if message.attachments:
        image = next(
            (a for a in message.attachments if (a.content_type or "").startswith("image/")),
            None,
        )
        if image is not None:
            embed.set_image(url=image.url)
    return embed


__all__ = ["Features"]
