"""Voice music playback with an in-memory queue.

Music is optional: if yt-dlp is not installed, Jarvis explains how to enable it
instead of pretending to play something.  FFmpeg is required for streaming.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections import deque
from dataclasses import dataclass, field

import discord

from ..ai.tools import Risk, action, tool
from ..util import human_delta, truncate
from .helpers import fail, sent

log = logging.getLogger(__name__)

ACTIONS = {
    "join": action(Risk.LOW, permission="connect"),
    "leave": action(Risk.LOW, permission="connect"),
    "play": action(Risk.LOW, permission="connect", cooldown=2),
    "search": action(Risk.LOW, permission="connect"),
    "queue": action(Risk.SAFE),
    "nowplaying": action(Risk.SAFE),
    "skip": action(Risk.LOW, permission="connect"),
    "stop": action(Risk.LOW, permission="connect"),
    "pause": action(Risk.LOW, permission="connect"),
    "resume": action(Risk.LOW, permission="connect"),
    "volume": action(Risk.LOW, permission="connect"),
    "loop": action(Risk.LOW, permission="connect"),
    "shuffle": action(Risk.LOW, permission="connect"),
    "remove": action(Risk.LOW, permission="connect"),
    "clear": action(Risk.LOW, permission="connect"),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "query": {"type": "string", "description": "Song name or URL"},
        "channel": {"type": "string", "description": "Voice channel to join"},
        "position": {"type": "integer", "description": "Queue position to remove"},
        "volume": {"type": "integer", "description": "Volume 0-200"},
        "loop": {"type": "boolean", "description": "Loop the current song"},
    },
    "required": ["action"],
}


@dataclass
class Track:
    title: str
    url: str
    requester: int
    duration: int | None = None
    stream_url: str | None = None

    @property
    def duration_text(self) -> str:
        if not self.duration:
            return "live / unknown"
        return human_delta(__import__("datetime").timedelta(seconds=self.duration))


@dataclass
class GuildMusic:
    queue: deque[Track] = field(default_factory=deque)
    current: Track | None = None
    loop: bool = False
    volume: float = 0.5
    text_channel_id: int = 0


class MusicManager:
    def __init__(self, bot):
        self.bot = bot
        self.guilds: dict[int, GuildMusic] = {}

    def state(self, guild_id: int) -> GuildMusic:
        return self.guilds.setdefault(guild_id, GuildMusic())

    async def connect(self, ctx, channel=None) -> discord.VoiceClient | None:
        if ctx.member is None or ctx.member.voice is None or ctx.member.voice.channel is None:
            return None
        target = channel or ctx.member.voice.channel
        if not isinstance(target, (discord.VoiceChannel, discord.StageChannel)):
            return None
        voice = ctx.guild.voice_client
        if voice is not None:
            if voice.channel != target:
                await voice.move_to(target)
            return voice
        return await target.connect(self_deaf=True)

    async def enqueue(self, ctx, query: str, *, play_now: bool = True) -> Track | ValueError:
        if shutil.which("ffmpeg") is None:
            return ValueError("FFmpeg isn't installed, sir, so I can't stream audio.")
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            return ValueError(
                "I need `yt-dlp` for music, sir. Install it with `pip install yt-dlp`."
            )
        query = query.strip()
        if not query:
            return ValueError("Tell me what to play, sir.")
        target = query if query.startswith("http") else f"ytsearch1:{query}"
        options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "default_search": "auto",
            "extract_flat": False,
        }
        try:
            info = await asyncio.to_thread(self._extract, yt_dlp, target, options)
        except Exception as exc:
            return ValueError(f"I couldn't find that track: {truncate(exc, 160)}")
        if not info:
            return ValueError("I couldn't find that track, sir.")
        stream_url = info.get("url")
        if not stream_url:
            formats = info.get("formats") or []
            audio = next(
                (f for f in reversed(formats) if f.get("acodec") not in {None, "none"}),
                None,
            )
            stream_url = audio.get("url") if audio else None
        track = Track(
            title=str(info.get("title") or query)[:200],
            url=str(info.get("webpage_url") or query),
            requester=ctx.author_id,
            duration=int(info["duration"]) if info.get("duration") else None,
            stream_url=stream_url,
        )
        voice = await self.connect(ctx)
        if voice is None:
            return ValueError("Join a voice channel first, sir.")
        state = self.state(ctx.guild_id)
        state.text_channel_id = ctx.channel_id
        if voice.is_playing() or voice.is_paused():
            state.queue.append(track)
            return track
        state.current = track
        self._start(voice, state, track)
        return track

    @staticmethod
    def _extract(yt_dlp, target: str, options: dict):
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(target, download=False)

    def _start(self, voice: discord.VoiceClient, state: GuildMusic, track: Track) -> None:
        source = discord.FFmpegPCMAudio(
            track.stream_url,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options="-vn",
        )
        transformer = discord.PCMVolumeTransformer(source, volume=state.volume)
        voice.play(transformer, after=lambda error: self._after(voice, state, error))

    def _after(self, voice: discord.VoiceClient, state: GuildMusic, error) -> None:
        if error is not None:
            log.warning("Voice playback error: %s", error)
        if state.loop and state.current is not None:
            try:
                self._start(voice, state, state.current)
                return
            except Exception:
                log.exception("Could not loop the current track")
        state.current = None
        if state.queue and voice.is_connected():
            next_track = state.queue.popleft()
            state.current = next_track
            try:
                self._start(voice, state, next_track)
            except Exception:
                log.exception("Could not play the next track")
        elif voice.is_connected():
            channel = self.bot.get_channel(state.text_channel_id)
            if channel is not None:
                asyncio.run_coroutine_threadsafe(
                    channel.send("🎵 The queue has finished, sir."),
                    self.bot.loop,
                )


@tool(
    "manage_voice",
    "Join/leave voice and play music: play, queue, now playing, skip, stop, pause, resume, "
    "volume, loop, shuffle, remove, or clear the queue.",
    SCHEMA,
    category="voice",
    actions=ACTIONS,
)
async def manage_voice(ctx, args):
    operation = str(args["action"]).lower()
    manager: MusicManager | None = getattr(ctx.bot, "music", None)
    if manager is None:
        return fail("Music isn't available, sir.")
    if not ctx.settings.music_enabled and operation in {
        "play", "search", "skip", "stop", "pause", "resume", "volume", "loop", "shuffle"
    }:
        return fail("Music playback is disabled in the configuration, sir.")

    voice = ctx.guild.voice_client
    state = manager.state(ctx.guild_id)

    if operation == "join":
        channel = ctx.resolve_channel(args.get("channel")) if args.get("channel") else None
        if channel is not None and not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return fail("That isn't a voice channel, sir.")
        client = await manager.connect(ctx, channel)
        if client is None:
            return fail("Join a voice channel first, sir.")
        await ctx.say(f"🔊 Joined {client.channel.mention}.")
        return sent("Joined voice.")

    if operation in {"leave", "stop"}:
        if voice is None:
            return fail("I'm not in a voice channel, sir.")
        state.queue.clear()
        state.current = None
        await voice.disconnect(force=True)
        await ctx.say("👋 Left the voice channel.")
        return sent("Left voice.")

    if operation == "play" or operation == "search":
        result = await manager.enqueue(ctx, str(args.get("query") or args.get("text") or ""))
        if isinstance(result, ValueError):
            return fail(str(result))
        playing_now = voice is None or (not voice.is_playing() and not voice.is_paused())
        await ctx.say(
            f"▶️ **{truncate(result.title, 200)}** ({result.duration_text}) — {result.url}"
            + ("\nAdded to the queue." if not playing_now else "")
        )
        return sent(f"Playing {result.title}.")

    if voice is None:
        return fail("I'm not in a voice channel, sir.")

    if operation == "queue":
        if state.current is None and not state.queue:
            await ctx.say("The queue is empty, sir.")
            return sent("Empty queue.")
        lines = []
        if state.current is not None:
            lines.append(f"▶️ **{truncate(state.current.title, 180)}** (now playing)")
        for index, track in enumerate(list(state.queue)[:20], start=1):
            lines.append(f"`{index}.` {truncate(track.title, 180)} ({track.duration_text})")
        embed = discord.Embed(
            title="🎶 Music queue",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(text=f"Loop: {'on' if state.loop else 'off'} · Volume: {int(state.volume * 100)}%")
        await ctx.say(embed=embed)
        return sent("Showed the queue.")

    if operation == "nowplaying":
        if state.current is None:
            return fail("Nothing is playing, sir.")
        embed = discord.Embed(
            title="🎵 Now playing",
            description=f"**{truncate(state.current.title, 300)}**",
            url=state.current.url if state.current.url.startswith("http") else None,
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="Duration", value=state.current.duration_text, inline=True)
        embed.add_field(name="Requested by", value=f"<@{state.current.requester}>", inline=True)
        embed.add_field(name="Volume", value=f"{int(state.volume * 100)}%", inline=True)
        await ctx.say(embed=embed)
        return sent("Showed now playing.")

    if operation == "pause":
        if not voice.is_playing():
            return fail("Nothing is playing, sir.")
        voice.pause()
        return sent("Paused.")
    if operation == "resume":
        if not voice.is_paused():
            return fail("Playback isn't paused, sir.")
        voice.resume()
        return sent("Resumed.")
    if operation == "skip":
        if state.current is None:
            return fail("Nothing is playing, sir.")
        skipped = state.current.title
        state.current = None
        voice.stop()
        await ctx.say(f"⏭️ Skipped **{truncate(skipped, 200)}**.")
        return sent("Skipped.")
    if operation == "volume":
        value = max(0, min(200, int(args.get("volume") or 50)))
        state.volume = value / 100
        if voice.source is not None and isinstance(voice.source, discord.PCMVolumeTransformer):
            voice.source.volume = state.volume
        await ctx.say(f"🔉 Volume set to **{value}%**.")
        return sent("Volume set.")
    if operation == "loop":
        state.loop = bool(args.get("loop")) if "loop" in args else not state.loop
        await ctx.say(f"🔁 Loop is **{'on' if state.loop else 'off'}**.")
        return sent("Loop toggled.")
    if operation == "shuffle":
        items = list(state.queue)
        __import__("random").shuffle(items)
        state.queue = deque(items)
        await ctx.say(f"🔀 Shuffled **{len(items)}** queued track(s).")
        return sent("Shuffled.")
    if operation == "remove":
        position = int(args.get("position") or 0)
        if position < 1 or position > len(state.queue):
            return fail("Give me a valid queue position, sir.")
        track = list(state.queue)[position - 1]
        state.queue.remove(track)
        await ctx.say(f"➖ Removed **{truncate(track.title, 180)}** from the queue.")
        return sent("Removed.")
    if operation == "clear":
        count = len(state.queue)
        state.queue.clear()
        await ctx.say(f"🧹 Cleared **{count}** queued track(s).")
        return sent("Queue cleared.")

    return fail(f"I don't know how to `{operation}` voice, sir.")


__all__ = ["manage_voice", "MusicManager"]
