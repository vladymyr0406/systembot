"""Тикеты: приватные каналы по категориям, закрытие с транскриптом."""
import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands

from ..db import now
from ..util import dm, has_any_role, is_staff, make_embed, send_log


class TicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rptk:(?P<op>close|claim):(?P<id>\d+)"):
    def __init__(self, op: str, tid: int):
        close = op == "close"
        super().__init__(discord.ui.Button(label="Закрыть" if close else "Взять в работу", emoji="🔒" if close else "🙋",
                                           style=discord.ButtonStyle.danger if close else discord.ButtonStyle.success,
                                           custom_id=f"rptk:{op}:{tid}"))
        self.op, self.tid = op, tid

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["op"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Tickets")
        if self.op == "close":
            await cog.ask_close(interaction, self.tid)
        else:
            await cog.claim(interaction, self.tid)


class ConfirmClose(discord.ui.View):
    def __init__(self, cog, tid):
        super().__init__(timeout=60)
        self.cog, self.tid = cog, tid

    @discord.ui.button(label="Да, закрыть тикет", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button):
        await interaction.response.edit_message(content="Закрываю…", view=None)
        await self.cog.close(interaction, self.tid)


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_dynamic_items(TicketButton)

    async def _can(self, member, s, cat, row) -> bool:
        return (str(member.id) == row["user_id"] or is_staff(member, s)
                or has_any_role(member, (cat or {}).get("support_roles") or []))

    async def open_ticket(self, interaction: discord.Interaction, key: str):
        db, guild, user = self.bot.db, interaction.guild, interaction.user
        cat = await db.item(guild.id, "ticket", key)
        if not cat or not cat.get("enabled"):
            return await interaction.response.send_message("Эта категория недоступна.", ephemeral=True)
        s = await db.settings(guild.id)
        limit = int(s.get("ticket_limit") or 1)
        r = await db.one("SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND user_id=? AND status='open'", (str(guild.id), str(user.id)))
        if limit and r["c"] >= limit:
            return await interaction.response.send_message(f"У вас уже есть открытые тикеты ({r['c']}). Закройте их перед созданием нового.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        support = [guild.get_role(int(x)) for x in cat.get("support_roles") or []]
        ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
              user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
              guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True, embed_links=True)}
        for role in support:
            if role:
                ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True)
        parent = guild.get_channel(int(cat["category"])) if cat.get("category") else None
        name = f"{key}-{user.name}"[:90]
        try:
            ch = await guild.create_text_channel(name, overwrites=ow, category=parent if isinstance(parent, discord.CategoryChannel) else None,
                                                 topic=f"Тикет · {cat['name']} · {user} ({user.id})", reason="Новый тикет")
        except discord.HTTPException:
            return await interaction.followup.send("Не удалось создать канал (нет прав у бота).", ephemeral=True)
        tid = await db.run("INSERT INTO tickets(guild_id,channel_id,user_id,cat_key,created_at) VALUES(?,?,?,?,?)",
                           (str(guild.id), str(ch.id), str(user.id), key, now()))
        e = make_embed(s, title=f"{cat.get('emoji') or '🎫'} {cat['name']} · #{tid}", desc=f"{user.mention}\n\n{cat.get('welcome') or ''}")
        v = discord.ui.View(timeout=None)
        v.add_item(TicketButton("claim", tid))
        v.add_item(TicketButton("close", tid))
        ping = " ".join(r.mention for r in support if r) if cat.get("ping_support") else None
        await ch.send(content=ping, embed=e, view=v, allowed_mentions=discord.AllowedMentions(roles=True))
        await interaction.followup.send(f"Тикет создан: {ch.mention}", ephemeral=True)

    async def claim(self, interaction: discord.Interaction, tid: int):
        row = await self.bot.db.one("SELECT * FROM tickets WHERE id=?", (tid,))
        s = await self.bot.db.settings(interaction.guild_id)
        cat = await self.bot.db.item(interaction.guild_id, "ticket", row["cat_key"]) if row else None
        if not row or str(interaction.user.id) == row["user_id"] or not await self._can(interaction.user, s, cat, row):
            return await interaction.response.send_message("Только поддержка может взять тикет.", ephemeral=True)
        await interaction.response.send_message(f"🙋 Тикет принял в работу {interaction.user.mention}.")

    async def ask_close(self, interaction: discord.Interaction, tid: int):
        row = await self.bot.db.one("SELECT * FROM tickets WHERE id=?", (tid,))
        if not row or row["status"] != "open":
            return await interaction.response.send_message("Тикет уже закрыт.", ephemeral=True)
        s = await self.bot.db.settings(interaction.guild_id)
        cat = await self.bot.db.item(interaction.guild_id, "ticket", row["cat_key"])
        if not await self._can(interaction.user, s, cat, row):
            return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        await interaction.response.send_message("Закрыть тикет? Канал будет удалён.", view=ConfirmClose(self, tid), ephemeral=True)

    async def close(self, interaction: discord.Interaction, tid: int):
        db, guild = self.bot.db, interaction.guild
        row = await db.one("SELECT * FROM tickets WHERE id=?", (tid,))
        if not row or row["status"] != "open":
            return
        s = await db.settings(guild.id)
        ch = guild.get_channel(int(row["channel_id"]))
        await db.run("UPDATE tickets SET status='closed', closed_at=? WHERE id=?", (now(), tid))
        if not ch:
            return
        file_bytes = None
        if s.get("ticket_transcripts"):
            lines = [f"Тикет #{tid} · {row['cat_key']} · автор {row['user_id']}", "=" * 50]
            async for m in ch.history(limit=None, oldest_first=True):
                body = m.content or ""
                if m.embeds and not body:
                    body = "[embed] " + " | ".join(filter(None, (m.embeds[0].title, m.embeds[0].description)))[:500]
                att = "".join(f"\n    📎 {a.url}" for a in m.attachments)
                lines.append(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author} : {body}{att}")
            file_bytes = "\n".join(lines).encode("utf-8")

        def mk():
            return discord.File(io.BytesIO(file_bytes), filename=f"ticket-{tid}.txt") if file_bytes else None

        e = make_embed(s, title=f"🔒 Тикет #{tid} закрыт",
                       desc=f"**Автор:** <@{row['user_id']}>\n**Категория:** {row['cat_key']}\n**Закрыл:** {interaction.user.mention}")
        await send_log(guild, s, "log_tickets", e, file=mk())
        if s.get("ticket_dm_transcript"):
            member = guild.get_member(int(row["user_id"]))
            if member:
                try:
                    await member.send(embed=e, file=mk())
                except discord.HTTPException:
                    pass
        try:
            await ch.send("Тикет закрыт, канал будет удалён через 5 секунд.")
            await asyncio.sleep(5)
            await ch.delete(reason=f"Тикет #{tid} закрыт: {interaction.user}")
        except discord.HTTPException:
            pass

    ticket = app_commands.Group(name="ticket", description="Тикеты", guild_only=True)

    async def _row(self, interaction):
        return await self.bot.db.one("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (str(interaction.channel_id),))

    @ticket.command(name="add", description="Добавить участника в тикет")
    async def add(self, interaction: discord.Interaction, user: discord.Member):
        row = await self._row(interaction)
        s = await self.bot.db.settings(interaction.guild_id)
        if not row or not is_staff(interaction.user, s):
            cat = await self.bot.db.item(interaction.guild_id, "ticket", row["cat_key"]) if row else None
            if not row or not has_any_role(interaction.user, (cat or {}).get("support_roles") or []):
                return await interaction.response.send_message("Команда работает в тикете и только для поддержки.", ephemeral=True)
        await interaction.channel.set_permissions(user, view_channel=True, send_messages=True, read_message_history=True)
        await interaction.response.send_message(f"{user.mention} добавлен в тикет.")

    @ticket.command(name="remove", description="Убрать участника из тикета")
    async def remove(self, interaction: discord.Interaction, user: discord.Member):
        row = await self._row(interaction)
        s = await self.bot.db.settings(interaction.guild_id)
        if not row or not is_staff(interaction.user, s):
            return await interaction.response.send_message("Команда работает в тикете и только для администрации.", ephemeral=True)
        await interaction.channel.set_permissions(user, overwrite=None)
        await interaction.response.send_message(f"{user.mention} убран из тикета.")

    @ticket.command(name="close", description="Закрыть текущий тикет")
    async def close_cmd(self, interaction: discord.Interaction):
        row = await self._row(interaction)
        if not row:
            return await interaction.response.send_message("Это не тикет.", ephemeral=True)
        await self.ask_close(interaction, row["id"])


async def setup(bot):
    await bot.add_cog(Tickets(bot))
