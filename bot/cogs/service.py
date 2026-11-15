"""Служебное: /status (что включено и что работает), резервные копии, keep-alive для бесплатных хостингов."""
import asyncio
import logging
import time

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

from .. import config
from .. import backup
from ..backup import make_backup
from ..schema import MODULES
from ..util import make_embed, staff_only

log = logging.getLogger("rpbot.service")
NEED_PERMS = {"manage_roles": "управлять ролями", "manage_channels": "управлять каналами", "kick_members": "кикать",
              "ban_members": "банить", "moderate_members": "тайм-ауты", "manage_messages": "удалять сообщения",
              "create_public_threads": "создавать ветки", "embed_links": "встраивать ссылки"}


async def collect_status(bot, guild: discord.Guild) -> dict:
    """Единый отчёт для команды /status и веб-панели. ok: True — работает, False — не работает, None — по желанию."""
    db = bot.db
    s = await db.settings(guild.id)
    forms = await db.items(guild.id, "form")
    panels = await db.items(guild.id, "panel")
    modules = [{"key": k, "name": n, "icon": i, "on": bool(s.get(k, True))} for k, n, i in MODULES]
    checks = []
    add = lambda ok, text, route=None: checks.append({"ok": ok, "text": text, "route": route})

    add(bool(s.get("staff_roles")), "Роли администрации выбраны" if s.get("staff_roles") else "Не выбраны роли администрации (команды доступны только админам сервера)", "#/s/general")
    has_review = bool(s.get("log_forms")) or any(f.get("review_channel") for f in forms.values())
    add(has_review, "Канал рассмотрения заявок задан" if has_review else "Не задан канал рассмотрения заявок — формы не смогут отправляться", "#/i/form")
    add(bool(s.get("log_mod")), "Канал логов модерации задан" if s.get("log_mod") else "Не задан канал логов модерации", "#/s/logs")
    add(bool(s.get("news_channel")) or None, "Канал новостей задан" if s.get("news_channel") else "Канал новостей не задан (можно указывать при отправке)", "#/s/news")
    pub = sum(1 for p in panels.values() if p.get("_msg"))
    add(pub > 0, f"Опубликовано панелей: {pub}" if pub else "Ни одна панель не опубликована", "#/i/panel")
    perms = guild.me.guild_permissions
    miss = [t for k, t in NEED_PERMS.items() if not getattr(perms, k)] if not perms.administrator else []
    add(not miss, "У бота достаточно прав" if not miss else "Боту не хватает прав: " + ", ".join(miss), None)
    if backup.channel_id() and not backup.STATE["blocked"]:
        add(True, f"Резервные копии базы включены (канал <#{backup.channel_id()}>) — настройки переживут перезапуск", None)
    else:
        why = backup.STATE["error"] or "канал для копий не найден и не создан (боту нужно право «Управление каналами»)"
        add(False, f"Резервные копии не работают: {why}. На бесплатном хостинге настройки пропадут после перезапуска", None)
    from .music import voice_report
    for ok, text in voice_report():
        add(ok, text, None)
    return {"modules": modules, "checks": checks}


ICON = {True: "✅", False: "❌", None: "➖"}


class Service(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_changes = None
        self.backup_loop.start()
        if config.KEEPALIVE and config.BASE_URL.startswith("https") and "localhost" not in config.BASE_URL:
            self.keepalive.start()

    @commands.Cog.listener()
    async def on_ready(self):
        """Канал для копий находим или создаём; первая копия сразу, чтобы настройки уже были в безопасности."""
        if not backup.channel_id():
            await backup.find_channel(self.bot)
        if not backup.channel_id():
            await backup.ensure_channel(self.bot)
        self._last_changes = None

    async def cog_unload(self):
        self.backup_loop.cancel()
        self.keepalive.cancel()
        if backup.channel_id() and self.bot.db.conn and self.bot.db.conn.total_changes != self._last_changes:
            try:
                await asyncio.wait_for(make_backup(self.bot, "выключение"), 20)
            except Exception:
                log.exception("Не удалось сделать копию при выключении")

    @tasks.loop(seconds=20)
    async def backup_loop(self):
        """Копия делается вскоре после любых изменений (не чаще раза в 25 секунд)."""
        if not backup.channel_id() or backup.STATE["blocked"] or not self.bot.db.conn:
            return
        changes = self.bot.db.conn.total_changes
        if changes == self._last_changes or time.time() - backup.STATE["last"] < 25:
            return
        try:
            if await make_backup(self.bot):
                self._last_changes = changes
        except Exception:
            log.exception("Резервная копия не создана")

    @backup_loop.before_loop
    async def _b(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=8)
    async def keepalive(self):
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                await c.get(f"{config.BASE_URL}/health")
        except Exception:
            pass

    @keepalive.before_loop
    async def _k(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="status", description="Что в боте включено и что работает")
    @app_commands.guild_only()
    @staff_only()
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        st = await collect_status(self.bot, interaction.guild)
        s = await self.bot.db.settings(interaction.guild_id)
        e = make_embed(s, title="📊 Состояние бота")
        e.add_field(name="Модули", value="\n".join(f"{'✅' if m['on'] else '⛔'} {m['icon']} {m['name']}" for m in st["modules"]), inline=False)
        e.add_field(name="Проверки", value="\n".join(f"{ICON[c['ok']]} {c['text']}" for c in st["checks"]), inline=False)
        e.add_field(name="Веб-панель", value=config.BASE_URL, inline=False)
        e.set_footer(text="Включать и выключать модули: /setting → Модули или веб-панель")
        await interaction.followup.send(embed=e, ephemeral=True)

    @app_commands.command(name="backup", description="Сделать резервную копию базы сейчас (только владелец бота)")
    async def backup(self, interaction: discord.Interaction):
        if interaction.user.id not in config.OWNER_IDS:
            return await interaction.response.send_message("Команда только для владельца бота (OWNER_IDS).", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        if not backup.channel_id():
            await backup.ensure_channel(self.bot)
        if not backup.channel_id():
            return await interaction.followup.send(f"Канал для копий не создан: {backup.STATE['error'] or 'нужно право «Управление каналами»'}", ephemeral=True)
        try:
            url = await make_backup(self.bot, f"вручную, {interaction.user}")
        except Exception as e:
            return await interaction.followup.send(f"Не удалось: {e}", ephemeral=True)
        await interaction.followup.send(f"💾 Готово: {url}" if url else "Копирование приостановлено (см. логи).", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Service(bot))
