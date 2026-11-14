"""Музыка: YouTube / SoundCloud через yt-dlp + FFmpeg. Очередь, повтор, громкость, кнопки управления."""
import asyncio
import os
import random
import shutil
import time
from collections import deque
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..util import has_any_role, is_staff, make_embed

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
LOOP_LABEL = {"off": "выкл.", "track": "трек", "queue": "очередь"}


def _ydl_opts(extra=None):
    o = {"format": "bestaudio/best", "quiet": True, "no_warnings": True, "default_search": "auto",
         "noplaylist": True, "source_address": "0.0.0.0", "socket_timeout": 15}
    if os.getenv("YTDL_COOKIES") and os.path.exists(os.getenv("YTDL_COOKIES")):
        o["cookiefile"] = os.getenv("YTDL_COOKIES")
    o.update(extra or {})
    return o


def _extract(query: str, extra=None):
    with yt_dlp.YoutubeDL(_ydl_opts(extra)) as ydl:
        return ydl.extract_info(query, download=False)


def fmt_dur(sec) -> str:
    if not sec:
        return "live"
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class Track:
    title: str
    page_url: str
    duration: int | None
    requester: int
    thumb: str | None = None
    stream: str | None = None


@dataclass
class Player:
    queue: deque = field(default_factory=deque)
    current: Track | None = None
    loop: str = "off"
    volume: float = 0.5
    skipped: bool = False
    text: discord.abc.Messageable | None = None
    idle_since: float = field(default_factory=time.monotonic)
    alone_since: float | None = None


class Controls(discord.ui.View):
    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=6 * 3600)
        self.cog, self.gid = cog, guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.cog.control_ok(interaction)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.secondary)
    async def pause(self, interaction: discord.Interaction, b):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
        elif vc and vc.is_playing():
            vc.pause()
        await interaction.response.defer()

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.primary)
    async def skip(self, interaction: discord.Interaction, b):
        self.cog.do_skip(interaction.guild)
        await interaction.response.defer()

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary)
    async def loop(self, interaction: discord.Interaction, b):
        p = self.cog.player(interaction.guild)
        p.loop = {"off": "track", "track": "queue", "queue": "off"}[p.loop]
        await interaction.response.send_message(f"Повтор: **{LOOP_LABEL[p.loop]}**", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, b):
        self.cog.do_stop(interaction.guild)
        await interaction.response.defer()


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players: dict[int, Player] = {}
        self.watchdog.start()

    def cog_unload(self):
        self.watchdog.cancel()

    def player(self, guild) -> Player:
        if guild.id not in self.players:
            self.players[guild.id] = Player()
        return self.players[guild.id]

    # ---------- проверки ----------
    async def _gate(self, interaction: discord.Interaction, control=True) -> str | None:
        """Возвращает текст ошибки или None."""
        s = await self.bot.db.settings(interaction.guild_id)
        if not s.get("music_enabled"):
            return "Музыка на этом сервере отключена."
        if s.get("music_channel") and str(interaction.channel_id) != str(s["music_channel"]):
            return f"Музыкальные команды работают только в <#{s['music_channel']}>."
        if control and s.get("dj_roles") and not (is_staff(interaction.user, s) or has_any_role(interaction.user, s["dj_roles"])):
            return "Управлять музыкой могут только DJ."
        return None

    async def control_ok(self, interaction: discord.Interaction) -> bool:
        err = await self._gate(interaction)
        vc = interaction.guild.voice_client
        if not err and (not vc or not interaction.user.voice or interaction.user.voice.channel != vc.channel):
            err = "Зайдите в голосовой канал с ботом."
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return False
        return True

    # ---------- воспроизведение ----------
    async def resolve(self, track: Track) -> str:
        info = await asyncio.to_thread(_extract, track.page_url)
        if "entries" in info:
            info = info["entries"][0]
        track.stream = info["url"]
        track.duration = track.duration or info.get("duration")
        return track.stream

    async def play_next(self, guild: discord.Guild):
        p, vc = self.player(guild), guild.voice_client
        if not vc or not vc.is_connected():
            return
        if p.loop == "track" and p.current and not p.skipped:
            track = p.current
        else:
            if p.loop == "queue" and p.current and not p.skipped:
                p.queue.append(p.current)
            p.skipped = False
            track = p.queue.popleft() if p.queue else None
        if not track:
            p.current = None
            p.idle_since = time.monotonic()
            return
        p.current = track
        try:
            stream = await self.resolve(track)
        except Exception as e:
            if p.text:
                await p.text.send(f"⚠️ Не удалось воспроизвести **{track.title}**: {str(e)[:150]}")
            p.current = None
            return await self.play_next(guild)
        s = await self.bot.db.settings(guild.id)
        src = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(stream, before_options=FFMPEG_BEFORE, options="-vn"), volume=p.volume)

        def after(err):
            if err:
                print("player error:", err)
            asyncio.run_coroutine_threadsafe(self.play_next(guild), self.bot.loop)

        try:
            vc.play(src, after=after)
        except discord.ClientException:
            return
        if p.text:
            e = self.np_embed(s, p, guild)
            try:
                await p.text.send(embed=e, view=Controls(self, guild.id))
            except discord.HTTPException:
                pass

    def np_embed(self, s, p: Player, guild) -> discord.Embed:
        t = p.current
        e = make_embed(s, title="🎵 Сейчас играет", desc=f"**[{t.title}]({t.page_url})**")
        e.add_field(name="Длительность", value=fmt_dur(t.duration)).add_field(name="Заказал", value=f"<@{t.requester}>")
        e.add_field(name="Повтор", value=LOOP_LABEL[p.loop]).add_field(name="В очереди", value=str(len(p.queue)))
        if t.thumb:
            e.set_thumbnail(url=t.thumb)
        return e

    def do_skip(self, guild):
        p, vc = self.player(guild), guild.voice_client
        p.skipped = True
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

    def do_stop(self, guild):
        p, vc = self.player(guild), guild.voice_client
        p.queue.clear()
        p.loop, p.current = "off", None
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

    # ---------- команды ----------
    @app_commands.command(name="play", description="Включить трек или добавить в очередь (название или ссылка)")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, query: str):
        if yt_dlp is None or not shutil.which("ffmpeg"):
            return await interaction.response.send_message("На хостинге не установлен FFmpeg или yt-dlp — музыка недоступна.", ephemeral=True)
        err = await self._gate(interaction, control=False)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("Сначала зайдите в голосовой канал.", ephemeral=True)
        s = await self.bot.db.settings(interaction.guild_id)
        await interaction.response.defer()
        guild, p = interaction.guild, self.player(interaction.guild)
        vc = guild.voice_client
        try:
            if not vc:
                vc = await interaction.user.voice.channel.connect(self_deaf=True)
            elif vc.channel != interaction.user.voice.channel:
                return await interaction.followup.send("Бот уже играет в другом канале.")
        except (discord.ClientException, asyncio.TimeoutError):
            return await interaction.followup.send("Не удалось подключиться к голосовому каналу.")
        p.text = interaction.channel
        p.volume = p.volume if (vc.is_playing() or vc.is_paused()) else int(s.get("default_volume") or 50) / 100
        is_playlist = "list=" in query and "watch?v=" not in query or "/sets/" in query or "/playlist" in query
        search = query if query.startswith(("http://", "https://")) else f"ytsearch1:{query}"
        try:
            if is_playlist:
                info = await asyncio.to_thread(_extract, query, {"noplaylist": False, "extract_flat": "in_playlist", "playlistend": 50})
            else:
                try:
                    info = await asyncio.to_thread(_extract, search)
                except Exception:
                    if search.startswith("ytsearch"):
                        info = await asyncio.to_thread(_extract, f"scsearch1:{query}")
                    else:
                        raise
        except Exception as e:
            return await interaction.followup.send(f"Не нашёл или не смог открыть: {str(e)[:200]}")
        entries = info.get("entries") if "entries" in info else [info]
        entries = [x for x in entries if x][:50]
        if not entries:
            return await interaction.followup.send("Ничего не найдено.")
        maxq = int(s.get("max_queue") or 100)
        added = []
        for x in entries:
            if len(p.queue) >= maxq:
                break
            url = x.get("webpage_url") or x.get("url") or x.get("original_url")
            if not url:
                continue
            t = Track(title=x.get("title") or "Без названия", page_url=url, duration=x.get("duration"),
                      requester=interaction.user.id, thumb=x.get("thumbnail") or (x.get("thumbnails") or [{}])[-1].get("url"))
            p.queue.append(t)
            added.append(t)
        if not added:
            return await interaction.followup.send("Очередь заполнена.")
        playing = vc.is_playing() or vc.is_paused() or p.current is not None
        if len(added) == 1:
            t = added[0]
            e = make_embed(s, title="➕ Добавлено в очередь" if playing else "🎵 Загружаю", desc=f"**[{t.title}]({t.page_url})** · {fmt_dur(t.duration)}")
            if t.thumb:
                e.set_thumbnail(url=t.thumb)
        else:
            e = make_embed(s, title="➕ Плейлист добавлен", desc=f"Треков: **{len(added)}**")
        await interaction.followup.send(embed=e)
        if not playing:
            await self.play_next(guild)

    @app_commands.command(name="pause", description="Пауза / продолжить")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction):
        if not await self.control_ok(interaction):
            return
        vc = interaction.guild.voice_client
        if vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Продолжаю.")
        elif vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ Пауза.")
        else:
            await interaction.response.send_message("Ничего не играет.", ephemeral=True)

    @app_commands.command(name="skip", description="Пропустить трек")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction):
        if not await self.control_ok(interaction):
            return
        self.do_skip(interaction.guild)
        await interaction.response.send_message("⏭️ Пропускаю.")

    @app_commands.command(name="stop", description="Остановить и очистить очередь")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction):
        if not await self.control_ok(interaction):
            return
        self.do_stop(interaction.guild)
        await interaction.response.send_message("⏹️ Остановлено, очередь очищена.")

    @app_commands.command(name="leave", description="Выйти из голосового канала")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction):
        if not await self.control_ok(interaction):
            return
        self.do_stop(interaction.guild)
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("👋 Вышел из канала.")

    @app_commands.command(name="queue", description="Очередь воспроизведения")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction):
        p = self.player(interaction.guild)
        s = await self.bot.db.settings(interaction.guild_id)
        lines = [f"**Сейчас:** {p.current.title} · {fmt_dur(p.current.duration)}"] if p.current else []
        lines += [f"`{i}.` {t.title} · {fmt_dur(t.duration)}" for i, t in enumerate(list(p.queue)[:15], 1)]
        if len(p.queue) > 15:
            lines.append(f"…и ещё {len(p.queue) - 15}")
        await interaction.response.send_message(embed=make_embed(s, title="📜 Очередь", desc="\n".join(lines) or "Пусто."))

    @app_commands.command(name="nowplaying", description="Что играет сейчас")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction):
        p = self.player(interaction.guild)
        if not p.current:
            return await interaction.response.send_message("Ничего не играет.", ephemeral=True)
        s = await self.bot.db.settings(interaction.guild_id)
        await interaction.response.send_message(embed=self.np_embed(s, p, interaction.guild), view=Controls(self, interaction.guild_id))

    @app_commands.command(name="volume", description="Громкость 1–100")
    @app_commands.guild_only()
    async def volume(self, interaction: discord.Interaction, percent: app_commands.Range[int, 1, 100]):
        if not await self.control_ok(interaction):
            return
        p, vc = self.player(interaction.guild), interaction.guild.voice_client
        p.volume = percent / 100
        if vc.source and hasattr(vc.source, "volume"):
            vc.source.volume = p.volume
        await interaction.response.send_message(f"🔊 Громкость: {percent}%")

    @app_commands.command(name="loop", description="Повтор: выкл / трек / очередь")
    @app_commands.choices(mode=[app_commands.Choice(name="Выкл.", value="off"), app_commands.Choice(name="Трек", value="track"),
                                app_commands.Choice(name="Очередь", value="queue")])
    @app_commands.guild_only()
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        if not await self.control_ok(interaction):
            return
        self.player(interaction.guild).loop = mode.value
        await interaction.response.send_message(f"🔁 Повтор: **{mode.name.lower()}**")

    @app_commands.command(name="shuffle", description="Перемешать очередь")
    @app_commands.guild_only()
    async def shuffle(self, interaction: discord.Interaction):
        if not await self.control_ok(interaction):
            return
        p = self.player(interaction.guild)
        items = list(p.queue)
        random.shuffle(items)
        p.queue = deque(items)
        await interaction.response.send_message("🔀 Очередь перемешана.")

    @app_commands.command(name="remove", description="Убрать трек из очереди по номеру")
    @app_commands.guild_only()
    async def remove(self, interaction: discord.Interaction, number: app_commands.Range[int, 1, 500]):
        if not await self.control_ok(interaction):
            return
        p = self.player(interaction.guild)
        if number > len(p.queue):
            return await interaction.response.send_message("Нет такого номера.", ephemeral=True)
        items = list(p.queue)
        t = items.pop(number - 1)
        p.queue = deque(items)
        await interaction.response.send_message(f"Убрано: **{t.title}**")

    # ---------- авто-выход ----------
    @tasks.loop(seconds=30)
    async def watchdog(self):
        for g in self.bot.guilds:
            vc = g.voice_client
            if not vc:
                continue
            p = self.player(g)
            s = await self.bot.db.settings(g.id)
            busy = vc.is_playing() or vc.is_paused()
            humans = [m for m in vc.channel.members if not m.bot]
            t = time.monotonic()
            if not humans:
                p.alone_since = p.alone_since or t
                if t - p.alone_since > 90:
                    self.do_stop(g)
                    await vc.disconnect()
                    p.alone_since = None
                    continue
            else:
                p.alone_since = None
            if busy:
                p.idle_since = t
            elif t - p.idle_since > int(s.get("idle_leave_min") or 3) * 60:
                await vc.disconnect()

    @watchdog.before_loop
    async def _w(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Music(bot))
