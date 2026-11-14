"""Логи сервера + приветствие/прощание/авто-роли."""
import discord
from discord.ext import commands

from ..util import fmt_ts, make_embed, send_log, trim


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class Logs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _s(self, guild):
        return await self.bot.db.settings(guild.id)

    @commands.Cog.listener()
    async def on_message_delete(self, m: discord.Message):
        if not m.guild or m.author.bot:
            return
        s = await self._s(m.guild)
        if not s.get("log_messages"):
            return
        e = make_embed(s, title="🗑️ Сообщение удалено", color="#DC2626")
        e.add_field(name="Автор", value=f"{m.author.mention} (`{m.author.id}`)").add_field(name="Канал", value=m.channel.mention)
        e.add_field(name="Текст", value=trim(m.content or "— (без текста)"), inline=False)
        if m.attachments:
            e.add_field(name="Вложения", value="\n".join(a.url for a in m.attachments)[:1000], inline=False)
        await send_log(m.guild, s, "log_messages", e)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or after.author.bot or before.content == after.content:
            return
        s = await self._s(after.guild)
        if not s.get("log_messages"):
            return
        e = make_embed(s, title="✏️ Сообщение изменено", color="#F59E0B")
        e.add_field(name="Автор", value=after.author.mention).add_field(name="Канал", value=f"{after.channel.mention} · [перейти]({after.jump_url})")
        e.add_field(name="Было", value=trim(before.content), inline=False).add_field(name="Стало", value=trim(after.content), inline=False)
        await send_log(after.guild, s, "log_messages", e)

    @commands.Cog.listener()
    async def on_member_join(self, m: discord.Member):
        s = await self._s(m.guild)
        if s.get("log_members"):
            e = make_embed(s, title="📥 Участник зашёл", color="#16A34A")
            e.add_field(name="Участник", value=f"{m.mention} (`{m.id}`)").add_field(name="Аккаунт создан", value=fmt_ts(m.created_at.timestamp(), "R"))
            e.set_thumbnail(url=m.display_avatar.url)
            await send_log(m.guild, s, "log_members", e)
        roles = [m.guild.get_role(int(r)) for r in s.get("autorole") or []]
        roles = [r for r in roles if r]
        if roles and not m.bot:
            try:
                await m.add_roles(*roles, reason="Авто-роль при входе")
            except discord.HTTPException:
                pass
        if s.get("welcome_enabled") and s.get("welcome_channel"):
            ch = m.guild.get_channel(int(s["welcome_channel"]))
            if ch:
                text = str(s.get("welcome_text", "")).format_map(_Safe(user=m.mention, server=m.guild.name, count=m.guild.member_count))
                try:
                    await ch.send(text, allowed_mentions=discord.AllowedMentions(users=[m]))
                except discord.HTTPException:
                    pass

    @commands.Cog.listener()
    async def on_member_remove(self, m: discord.Member):
        s = await self._s(m.guild)
        if s.get("log_members"):
            e = make_embed(s, title="📤 Участник вышел", color="#DC2626")
            e.add_field(name="Участник", value=f"{m} (`{m.id}`)")
            e.add_field(name="Роли", value=trim(", ".join(r.mention for r in m.roles[1:]) or "—", 900), inline=False)
            await send_log(m.guild, s, "log_members", e)
        if s.get("goodbye_enabled") and s.get("goodbye_channel"):
            ch = m.guild.get_channel(int(s["goodbye_channel"]))
            if ch:
                text = str(s.get("goodbye_text", "")).format_map(_Safe(user=str(m), server=m.guild.name, count=m.guild.member_count))
                try:
                    await ch.send(text, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    pass

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        s = await self._s(after.guild)
        if before.roles != after.roles and s.get("log_roles"):
            added = [r for r in after.roles if r not in before.roles]
            removed = [r for r in before.roles if r not in after.roles]
            e = make_embed(s, title="🎭 Изменение ролей", color="#3B82F6")
            e.add_field(name="Участник", value=after.mention, inline=False)
            if added:
                e.add_field(name="Выданы", value=", ".join(r.mention for r in added))
            if removed:
                e.add_field(name="Сняты", value=", ".join(r.mention for r in removed))
            await send_log(after.guild, s, "log_roles", e)
        if before.nick != after.nick and s.get("log_members"):
            e = make_embed(s, title="📝 Смена ника", color="#3B82F6")
            e.add_field(name="Участник", value=after.mention).add_field(name="Было", value=before.nick or "—").add_field(name="Стало", value=after.nick or "—")
            await send_log(after.guild, s, "log_members", e)

    @commands.Cog.listener()
    async def on_voice_state_update(self, m: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if before.channel == after.channel:
            return
        s = await self._s(m.guild)
        if not s.get("log_voice"):
            return
        if not before.channel:
            txt = f"зашёл в {after.channel.mention}"
        elif not after.channel:
            txt = f"вышел из {before.channel.mention}"
        else:
            txt = f"перешёл {before.channel.mention} → {after.channel.mention}"
        await send_log(m.guild, s, "log_voice", make_embed(s, desc=f"🔊 {m.mention} {txt}", footer=False, color="#6B7280"))


async def setup(bot):
    await bot.add_cog(Logs(bot))
