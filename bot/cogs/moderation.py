"""Модерация: баны/муты/варны с делами, автомодерация, временные баны."""
import re
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..db import now
from ..util import (can_manage, dm, fmt_ts, has_any_role, human_duration, is_admin, make_embed,
                    parse_duration, send_log, staff_only, trim)

INVITE_RE = re.compile(r"(?:discord(?:app)?\.com/invite|discord\.gg|dsc\.gg)/[\w-]+", re.I)
URL_RE = re.compile(r"https?://([^\s/<>]+)", re.I)
CASE_ICON = {"ban": "🔨", "unban": "♻️", "kick": "👢", "timeout": "🔇", "untimeout": "🔊", "warn": "⚠️", "automod": "🤖"}
CASE_NAME = {"ban": "Бан", "unban": "Разбан", "kick": "Кик", "timeout": "Тайм-аут", "untimeout": "Снятие тайм-аута",
             "warn": "Предупреждение", "automod": "Автомодерация"}
CASE_COLOR = {"ban": "#DC2626", "kick": "#F59E0B", "timeout": "#F59E0B", "warn": "#EAB308", "unban": "#16A34A", "untimeout": "#16A34A"}


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.spam: dict[tuple, deque] = defaultdict(deque)
        self.unban_loop.start()

    def cog_unload(self):
        self.unban_loop.cancel()

    # ---------- дела ----------
    async def add_case(self, guild, user, mod, ctype, reason, duration=None, notify=True):
        db = self.bot.db
        s = await db.settings(guild.id)
        cid = await db.run("INSERT INTO cases(guild_id,user_id,mod_id,type,reason,duration,ts) VALUES(?,?,?,?,?,?,?)",
                           (str(guild.id), str(user.id), str(mod.id), ctype, reason, duration, now()))
        e = make_embed(s, title=f"{CASE_ICON[ctype]} {CASE_NAME[ctype]} · дело #{cid}", color=CASE_COLOR.get(ctype))
        e.add_field(name="Участник", value=f"{user.mention}\n`{user.id}`")
        e.add_field(name="Модератор", value=mod.mention if hasattr(mod, "mention") else str(mod))
        if duration:
            e.add_field(name="Срок", value=human_duration(duration))
        e.add_field(name="Причина", value=trim(reason or "не указана"), inline=False)
        await send_log(guild, s, "log_mod", e)
        if notify:
            d = make_embed(s, title=f"{CASE_ICON[ctype]} {CASE_NAME[ctype]}", desc=f"Сервер: **{guild.name}**",
                           color=CASE_COLOR.get(ctype))
            d.add_field(name="Причина", value=trim(reason or "не указана"), inline=False)
            if duration:
                d.add_field(name="Срок", value=human_duration(duration))
            await dm(user, d)
        return cid

    async def _guard(self, interaction, target) -> bool:
        if isinstance(target, discord.Member) and not can_manage(interaction.guild, interaction.user, target):
            await interaction.response.send_message("Вы не можете применить это к данному участнику (иерархия ролей).", ephemeral=True)
            return False
        if isinstance(target, discord.Member) and target.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message("Роль бота ниже или равна роли участника.", ephemeral=True)
            return False
        return True

    # ---------- команды ----------
    @app_commands.command(name="ban", description="Забанить участника")
    @app_commands.describe(duration="Срок: 30m, 12h, 7d (пусто = навсегда)", delete_days="Удалить сообщения за N дней (0–7)")
    @app_commands.guild_only()
    @staff_only()
    async def ban(self, interaction: discord.Interaction, user: discord.User, reason: str | None = None,
                  duration: str | None = None, delete_days: app_commands.Range[int, 0, 7] = 0):
        m = interaction.guild.get_member(user.id)
        if m and not await self._guard(interaction, m):
            return
        sec = parse_duration(duration) if duration else None
        if duration and not sec:
            return await interaction.response.send_message("Не понял срок. Примеры: 30m, 12h, 7d.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        cid = await self.add_case(interaction.guild, user, interaction.user, "ban", reason, sec)
        try:
            await interaction.guild.ban(user, reason=f"{interaction.user}: {reason or '—'}", delete_message_seconds=delete_days * 86400)
        except discord.HTTPException:
            await self.bot.db.run("UPDATE cases SET active=0 WHERE id=?", (cid,))
            return await interaction.followup.send("Не удалось забанить (нет прав).", ephemeral=True)
        await interaction.followup.send(f"🔨 {user.mention} забанен · дело #{cid}", ephemeral=True)

    @app_commands.command(name="unban", description="Разбанить по ID")
    @app_commands.guild_only()
    @staff_only()
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str | None = None):
        try:
            u = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(u, reason=f"{interaction.user}: {reason or '—'}")
        except (ValueError, discord.HTTPException):
            return await interaction.response.send_message("Пользователь не найден в списке банов.", ephemeral=True)
        await self.bot.db.run("UPDATE cases SET active=0 WHERE guild_id=? AND user_id=? AND type='ban'", (str(interaction.guild_id), str(u.id)))
        cid = await self.add_case(interaction.guild, u, interaction.user, "unban", reason, notify=False)
        await interaction.response.send_message(f"♻️ {u} разбанен · дело #{cid}", ephemeral=True)

    @app_commands.command(name="kick", description="Выгнать участника")
    @app_commands.guild_only()
    @staff_only()
    async def kick(self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None):
        if not await self._guard(interaction, user):
            return
        await interaction.response.defer(ephemeral=True)
        cid = await self.add_case(interaction.guild, user, interaction.user, "kick", reason)
        try:
            await user.kick(reason=f"{interaction.user}: {reason or '—'}")
        except discord.HTTPException:
            return await interaction.followup.send("Не удалось выгнать (нет прав).", ephemeral=True)
        await interaction.followup.send(f"👢 {user.mention} выгнан · дело #{cid}", ephemeral=True)

    @app_commands.command(name="timeout", description="Тайм-аут (мут)")
    @app_commands.describe(duration="Например 10m, 2h, 1d (до 28 дней)")
    @app_commands.guild_only()
    @staff_only()
    async def timeout(self, interaction: discord.Interaction, user: discord.Member, duration: str, reason: str | None = None):
        sec = parse_duration(duration)
        if not sec or sec > 28 * 86400:
            return await interaction.response.send_message("Срок: от секунд до 28 дней, например 30m, 2h, 7d.", ephemeral=True)
        if not await self._guard(interaction, user):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await user.timeout(timedelta(seconds=sec), reason=f"{interaction.user}: {reason or '—'}")
        except discord.HTTPException:
            return await interaction.followup.send("Не удалось выдать тайм-аут.", ephemeral=True)
        cid = await self.add_case(interaction.guild, user, interaction.user, "timeout", reason, sec)
        await interaction.followup.send(f"🔇 {user.mention} в тайм-ауте на {human_duration(sec)} · дело #{cid}", ephemeral=True)

    @app_commands.command(name="untimeout", description="Снять тайм-аут")
    @app_commands.guild_only()
    @staff_only()
    async def untimeout(self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None):
        await interaction.response.defer(ephemeral=True)
        try:
            await user.timeout(None, reason=f"{interaction.user}: {reason or '—'}")
        except discord.HTTPException:
            return await interaction.followup.send("Не удалось снять тайм-аут.", ephemeral=True)
        cid = await self.add_case(interaction.guild, user, interaction.user, "untimeout", reason, notify=False)
        await interaction.followup.send(f"🔊 Тайм-аут снят · дело #{cid}", ephemeral=True)

    async def apply_warn(self, guild, user, mod, reason, ctype="warn"):
        db = self.bot.db
        s = await db.settings(guild.id)
        cid = await self.add_case(guild, user, mod, ctype, reason)
        r = await db.one("SELECT COUNT(*) c FROM cases WHERE guild_id=? AND user_id=? AND type='warn' AND active=1", (str(guild.id), str(user.id)))
        count, limit = r["c"], int(s.get("warn_limit") or 0)
        extra = ""
        if limit and count >= limit and isinstance(user, discord.Member):
            act = s.get("warn_limit_action")
            why = f"Достигнут лимит предупреждений ({count}/{limit})"
            try:
                if act == "timeout":
                    await user.timeout(timedelta(hours=24), reason=why)
                    await self.add_case(guild, user, guild.me, "timeout", why, 86400)
                    extra = " Автоматически: тайм-аут 24 ч."
                elif act == "kick":
                    await self.add_case(guild, user, guild.me, "kick", why)
                    await user.kick(reason=why)
                    extra = " Автоматически: кик."
                elif act == "ban":
                    await self.add_case(guild, user, guild.me, "ban", why)
                    await user.ban(reason=why)
                    extra = " Автоматически: бан."
            except discord.HTTPException:
                extra = " (не удалось применить авто-наказание)"
        return cid, count, limit, extra

    @app_commands.command(name="warn", description="Выдать предупреждение")
    @app_commands.guild_only()
    @staff_only()
    async def warn(self, interaction: discord.Interaction, user: discord.Member, reason: str):
        if not await self._guard(interaction, user):
            return
        await interaction.response.defer(ephemeral=True)
        cid, count, limit, extra = await self.apply_warn(interaction.guild, user, interaction.user, reason)
        await interaction.followup.send(f"⚠️ {user.mention}: предупреждение {count}{'/' + str(limit) if limit else ''} · дело #{cid}.{extra}", ephemeral=True)

    @app_commands.command(name="warns", description="Предупреждения участника")
    @app_commands.guild_only()
    @staff_only()
    async def warns(self, interaction: discord.Interaction, user: discord.User):
        rows = await self.bot.db.all("SELECT * FROM cases WHERE guild_id=? AND user_id=? AND type='warn' AND active=1 ORDER BY id DESC",
                                     (str(interaction.guild_id), str(user.id)))
        text = "\n".join(f"`#{r['id']}` {fmt_ts(r['ts'], 'd')} · <@{r['mod_id']}> · {trim(r['reason'], 120)}" for r in rows)
        await interaction.response.send_message(embed=make_embed(await self.bot.db.settings(interaction.guild_id),
                                                                 title=f"Предупреждения: {user}", desc=text or "Нет активных предупреждений."), ephemeral=True)

    @app_commands.command(name="unwarn", description="Снять предупреждение по номеру дела")
    @app_commands.guild_only()
    @staff_only()
    async def unwarn(self, interaction: discord.Interaction, case_id: int):
        row = await self.bot.db.one("SELECT * FROM cases WHERE id=? AND guild_id=? AND type='warn'", (case_id, str(interaction.guild_id)))
        if not row:
            return await interaction.response.send_message("Такого предупреждения нет.", ephemeral=True)
        await self.bot.db.run("UPDATE cases SET active=0 WHERE id=?", (case_id,))
        await interaction.response.send_message(f"Предупреждение #{case_id} снято.", ephemeral=True)

    @app_commands.command(name="cases", description="История нарушений участника")
    @app_commands.guild_only()
    @staff_only()
    async def cases(self, interaction: discord.Interaction, user: discord.User):
        rows = await self.bot.db.all("SELECT * FROM cases WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 15",
                                     (str(interaction.guild_id), str(user.id)))
        text = "\n".join(f"`#{r['id']}` {CASE_ICON.get(r['type'], '•')} **{CASE_NAME.get(r['type'], r['type'])}**"
                         f"{'' if r['active'] else ' (снято)'} · {fmt_ts(r['ts'], 'd')} · {trim(r['reason'], 80)}" for r in rows)
        await interaction.response.send_message(embed=make_embed(await self.bot.db.settings(interaction.guild_id),
                                                                 title=f"Дела: {user}", desc=text or "Чисто."), ephemeral=True)

    @app_commands.command(name="purge", description="Удалить последние сообщения")
    @app_commands.guild_only()
    @staff_only()
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 200], user: discord.Member | None = None):
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount, check=(lambda m: m.author.id == user.id) if user else None)
        except discord.HTTPException:
            return await interaction.followup.send("Не удалось удалить сообщения.", ephemeral=True)
        await interaction.followup.send(f"🧹 Удалено: {len(deleted)}", ephemeral=True)

    @app_commands.command(name="slowmode", description="Медленный режим в канале (0 = выкл.)")
    @app_commands.guild_only()
    @staff_only()
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
        await interaction.channel.edit(slowmode_delay=seconds)
        await interaction.response.send_message(f"Медленный режим: {seconds} сек.", ephemeral=True)

    @app_commands.command(name="lock", description="Закрыть канал для @everyone")
    @app_commands.guild_only()
    @staff_only()
    async def lock(self, interaction: discord.Interaction):
        ow = interaction.channel.overwrites_for(interaction.guild.default_role)
        ow.send_messages = False
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow)
        await interaction.response.send_message("🔒 Канал закрыт.")

    @app_commands.command(name="unlock", description="Открыть канал")
    @app_commands.guild_only()
    @staff_only()
    async def unlock(self, interaction: discord.Interaction):
        ow = interaction.channel.overwrites_for(interaction.guild.default_role)
        ow.send_messages = None
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow)
        await interaction.response.send_message("🔓 Канал открыт.")

    # ---------- временные баны ----------
    @tasks.loop(seconds=60)
    async def unban_loop(self):
        rows = await self.bot.db.all("SELECT * FROM cases WHERE type='ban' AND active=1 AND duration IS NOT NULL AND ts+duration<=?", (now(),))
        for r in rows:
            g = self.bot.get_guild(int(r["guild_id"]))
            await self.bot.db.run("UPDATE cases SET active=0 WHERE id=?", (r["id"],))
            if not g:
                continue
            try:
                u = await self.bot.fetch_user(int(r["user_id"]))
                await g.unban(u, reason="Срок бана истёк")
                await self.add_case(g, u, g.me, "unban", "Срок бана истёк", notify=False)
            except discord.HTTPException:
                pass

    @unban_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    # ---------- автомодерация ----------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return
        s = await self.bot.db.settings(message.guild.id)
        if not s.get("automod_enabled"):
            return
        m = message.author
        if is_admin(m) or has_any_role(m, s.get("staff_roles") or []) or has_any_role(m, s.get("exempt_roles") or []):
            return
        text = message.content or ""
        reason = None
        if s.get("anti_invite") and INVITE_RE.search(text):
            reason = "Приглашения на другие серверы запрещены"
        elif s.get("anti_links"):
            allow = {d.strip().lower() for d in str(s.get("link_whitelist", "")).split(",") if d.strip()}
            for host in URL_RE.findall(text):
                h = host.lower().removeprefix("www.")
                if not any(h == a or h.endswith("." + a) for a in allow):
                    reason = "Ссылки запрещены"
                    break
        if not reason and s.get("anti_caps") and len(text) >= 10:
            letters = [c for c in text if c.isalpha()]
            if len(letters) >= 8 and sum(c.isupper() for c in letters) * 100 / len(letters) >= float(s.get("caps_percent") or 70):
                reason = "Слишком много заглавных букв"
        if not reason:
            words = [w.strip().lower() for w in str(s.get("bad_words", "")).splitlines() if w.strip()]
            low = text.lower()
            if any(w in low for w in words):
                reason = "Запрещённые слова"
        if not reason and s.get("anti_spam"):
            q = self.spam[(message.guild.id, m.id)]
            t = time.monotonic()
            q.append(t)
            while q and t - q[0] > float(s.get("spam_seconds") or 6):
                q.popleft()
            if len(q) >= int(s.get("spam_count") or 6):
                q.clear()
                reason = "Спам"
        if not reason:
            return
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        act = s.get("automod_action")
        if act == "warn":
            await self.apply_warn(message.guild, m, message.guild.me, f"Автомодерация: {reason}")
        elif act == "timeout":
            try:
                await m.timeout(timedelta(minutes=int(s.get("automod_timeout_min") or 10)), reason=reason)
            except discord.HTTPException:
                pass
            await self.add_case(message.guild, m, message.guild.me, "automod", reason, int(s.get("automod_timeout_min") or 10) * 60, notify=False)
        else:
            await self.add_case(message.guild, m, message.guild.me, "automod", reason, notify=False)
        try:
            await message.channel.send(f"{m.mention}, сообщение удалено: {reason}.", delete_after=6)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Moderation(bot))
