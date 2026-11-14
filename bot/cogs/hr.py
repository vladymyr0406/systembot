"""Кадры: должности и составы, приём/перевод/увольнение, личные дела, документы, бюджет."""
import discord
from discord import app_commands
from discord.ext import commands

from ..db import now
from ..util import (chan, dm, fmt_ts, make_embed, send_log, staff_only, trim, is_staff)

HR_KEY = "hr_roles"


class HR(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------- автодополнение ----------
    async def pos_ac(self, interaction: discord.Interaction, current: str):
        items = await self.bot.db.items(interaction.guild_id, "position")
        cur = current.lower()
        return [app_commands.Choice(name=f"{v['name']} · {v['unit']}"[:100], value=k)
                for k, v in sorted(items.items(), key=lambda kv: -int(kv[1].get("rank") or 0))
                if cur in v["name"].lower() or cur in v["unit"].lower() or cur in k][:25]

    async def kind_ac(self, interaction: discord.Interaction, current: str):
        s = await self.bot.db.settings(interaction.guild_id)
        kinds = [k.strip() for k in str(s.get("doc_kinds", "")).split(",") if k.strip()]
        return [app_commands.Choice(name=k, value=k) for k in kinds if current.lower() in k.lower()][:25]

    # ---------- роли должности ----------
    def _roles(self, guild, pos):
        if not pos:
            return []
        ids = [pos.get("role")] + list(pos.get("extra_roles") or [])
        return [r for r in (guild.get_role(int(i)) for i in ids if i and str(i).isdigit()) if r]

    async def _set_position(self, guild, member, old_pos, new_pos, reason):
        warn = None
        try:
            old = self._roles(guild, old_pos)
            new = self._roles(guild, new_pos)
            rem = [r for r in old if r not in new]
            if rem:
                await member.remove_roles(*rem, reason=reason)
            add = [r for r in new if r not in member.roles]
            if add:
                await member.add_roles(*add, reason=reason)
        except discord.HTTPException:
            warn = "⚠️ Не удалось изменить роли (проверьте, что роль бота выше ролей должности)."
        return warn

    async def _log_hr(self, interaction, member, action, old_key, new_key, reason):
        db, guild = self.bot.db, interaction.guild
        s = await db.settings(guild.id)
        await db.run("INSERT INTO hr_log(guild_id,user_id,action,from_pos,to_pos,by_id,reason,ts) VALUES(?,?,?,?,?,?,?,?)",
                     (str(guild.id), str(member.id), action, old_key, new_key, str(interaction.user.id), reason, now()))
        pos_old = await db.item(guild.id, "position", old_key) if old_key else None
        pos_new = await db.item(guild.id, "position", new_key) if new_key else None
        names = {"hire": ("Приём на должность", "#16A34A"), "promote": ("Повышение", "#16A34A"),
                 "demote": ("Понижение", "#F59E0B"), "transfer": ("Перевод", "#3B82F6"), "fire": ("Увольнение", "#DC2626")}
        title, color = names[action]
        profile = await db.profile(guild.id, member.id)
        e = make_embed(s, title=f"🗂️ {title}", color=color)
        e.add_field(name="Сотрудник", value=f"{member.mention}" + (f"\n{profile['fio']} · {profile['static']}" if profile else ""))
        if pos_old:
            e.add_field(name="Было", value=f"{pos_old['name']}\n_{pos_old['unit']}_")
        if pos_new:
            e.add_field(name="Стало", value=f"{pos_new['name']}\n_{pos_new['unit']}_")
        e.add_field(name="Причина", value=trim(reason or "не указана"), inline=False)
        e.add_field(name="Приказ издал", value=interaction.user.mention)
        await send_log(guild, s, "hr_channel", e)
        if s.get("hr_dm"):
            await dm(member, e)
        await db.notify(guild.id, member.id, title, f"{(pos_new or pos_old or {}).get('name', '')}. {reason or ''}".strip())
        await self.refresh_roster(guild)

    hr = app_commands.Group(name="hr", description="Кадры", guild_only=True)

    @hr.command(name="hire", description="Принять сотрудника на должность")
    @app_commands.autocomplete(position=pos_ac)
    @staff_only(HR_KEY)
    async def hire(self, interaction: discord.Interaction, user: discord.Member, position: str, reason: str | None = None):
        db, gid = self.bot.db, interaction.guild_id
        pos = await db.item(gid, "position", position)
        if not pos:
            return await interaction.response.send_message("Должность не найдена.", ephemeral=True)
        if await db.one("SELECT 1 FROM roster WHERE guild_id=? AND user_id=?", (str(gid), str(user.id))):
            return await interaction.response.send_message("Он уже в составе — используйте /hr transfer.", ephemeral=True)
        if int(pos.get("slots") or 0):
            c = await db.one("SELECT COUNT(*) c FROM roster WHERE guild_id=? AND position_key=?", (str(gid), position))
            if c["c"] >= int(pos["slots"]):
                return await interaction.response.send_message(f"Мест на должности «{pos['name']}» нет ({pos['slots']}/{pos['slots']}).", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        warn = await self._set_position(interaction.guild, user, None, pos, f"Приём: {reason or '—'}")
        await db.run("INSERT INTO roster(guild_id,user_id,position_key,since) VALUES(?,?,?,?)", (str(gid), str(user.id), position, now()))
        await self._log_hr(interaction, user, "hire", None, position, reason)
        await interaction.followup.send(f"{user.mention} принят на должность **{pos['name']}**." + (f"\n{warn}" if warn else ""), ephemeral=True)

    @hr.command(name="transfer", description="Перевод / повышение / понижение")
    @app_commands.autocomplete(position=pos_ac)
    @staff_only(HR_KEY)
    async def transfer(self, interaction: discord.Interaction, user: discord.Member, position: str, reason: str | None = None):
        db, gid = self.bot.db, interaction.guild_id
        row = await db.one("SELECT * FROM roster WHERE guild_id=? AND user_id=?", (str(gid), str(user.id)))
        new = await db.item(gid, "position", position)
        if not row or not new:
            return await interaction.response.send_message("Сотрудник не в составе или должность не найдена.", ephemeral=True)
        if row["position_key"] == position:
            return await interaction.response.send_message("Он уже на этой должности.", ephemeral=True)
        if int(new.get("slots") or 0):
            c = await db.one("SELECT COUNT(*) c FROM roster WHERE guild_id=? AND position_key=?", (str(gid), position))
            if c["c"] >= int(new["slots"]):
                return await interaction.response.send_message("На этой должности нет свободных мест.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        old = await db.item(gid, "position", row["position_key"])
        warn = await self._set_position(interaction.guild, user, old, new, f"Перевод: {reason or '—'}")
        await db.run("UPDATE roster SET position_key=?, since=? WHERE guild_id=? AND user_id=?", (position, now(), str(gid), str(user.id)))
        o, n = int((old or {}).get("rank") or 0), int(new.get("rank") or 0)
        action = "promote" if n > o else "demote" if n < o else "transfer"
        await self._log_hr(interaction, user, action, row["position_key"], position, reason)
        await interaction.followup.send(f"{user.mention} → **{new['name']}**." + (f"\n{warn}" if warn else ""), ephemeral=True)

    @hr.command(name="fire", description="Уволить сотрудника")
    @staff_only(HR_KEY)
    async def fire(self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None):
        db, gid = self.bot.db, interaction.guild_id
        row = await db.one("SELECT * FROM roster WHERE guild_id=? AND user_id=?", (str(gid), str(user.id)))
        if not row:
            return await interaction.response.send_message("Он не состоит в составах.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        old = await db.item(gid, "position", row["position_key"])
        warn = await self._set_position(interaction.guild, user, old, None, f"Увольнение: {reason or '—'}")
        await db.run("DELETE FROM roster WHERE guild_id=? AND user_id=?", (str(gid), str(user.id)))
        await self._log_hr(interaction, user, "fire", row["position_key"], None, reason)
        await interaction.followup.send(f"{user.mention} уволен." + (f"\n{warn}" if warn else ""), ephemeral=True)

    @hr.command(name="history", description="История кадровых изменений")
    @staff_only(HR_KEY)
    async def history(self, interaction: discord.Interaction, user: discord.Member):
        rows = await self.bot.db.all("SELECT * FROM hr_log WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 15",
                                     (str(interaction.guild_id), str(user.id)))
        names = {"hire": "приём", "promote": "повышение", "demote": "понижение", "transfer": "перевод", "fire": "увольнение"}
        poss = await self.bot.db.items(interaction.guild_id, "position")
        lines = [f"{fmt_ts(r['ts'], 'd')} · **{names.get(r['action'], r['action'])}** · "
                 f"{poss.get(r['from_pos'], {}).get('name', '—')} → {poss.get(r['to_pos'], {}).get('name', '—')}"
                 + (f" · _{trim(r['reason'], 80)}_" if r['reason'] else "") for r in rows]
        await interaction.response.send_message(embed=make_embed(await self.bot.db.settings(interaction.guild_id),
                                                                 title=f"История: {user.display_name}", desc="\n".join(lines) or "Записей нет."), ephemeral=True)

    # ---------- составы ----------
    async def build_roster(self, guild) -> list[discord.Embed]:
        db = self.bot.db
        s = await db.settings(guild.id)
        poss = await db.items(guild.id, "position")
        rows = await db.all("SELECT r.user_id, r.position_key, p.fio FROM roster r LEFT JOIN profiles p "
                            "ON p.guild_id=r.guild_id AND p.user_id=r.user_id WHERE r.guild_id=?", (str(guild.id),))
        units: dict[str, list] = {}
        for k, p in poss.items():
            units.setdefault(p["unit"], [])
        people: dict[str, list] = {k: [] for k in poss}
        for r in rows:
            people.setdefault(r["position_key"], []).append(r)
        for k, p in sorted(poss.items(), key=lambda kv: -int(kv[1].get("rank") or 0)):
            who = people.get(k, [])
            slots = int(p.get("slots") or 0)
            head = f"**{p['name']}**" + (f" ({len(who)}/{slots})" if slots else "")
            body = ", ".join(f"<@{w['user_id']}>" + (f" ({w['fio']})" if w["fio"] else "") for w in who) or "_вакантно_"
            units[p["unit"]].append(f"{head} — {body}")
        e = make_embed(s, title="🏛️ Составы", desc=f"Всего сотрудников: **{len(rows)}**")
        embeds = [e]
        for unit, lines in units.items():
            text = "\n".join(lines)
            if len(e.fields) >= 24:
                e = make_embed(s, title="🏛️ Составы (продолжение)")
                embeds.append(e)
            e.add_field(name=unit[:250], value=trim(text, 1024), inline=False)
        if not units:
            e.description += "\n\nДолжности ещё не созданы (веб-панель → Должности)."
        e.timestamp = discord.utils.utcnow()
        return embeds[:10]

    async def refresh_roster(self, guild):
        s = await self.bot.db.settings(guild.id)
        ch = chan(guild, s.get("roster_channel"))
        if not ch or not hasattr(ch, "send"):
            return
        embeds = await self.build_roster(guild)
        try:
            mid = s.get("_roster_msg")
            if mid:
                try:
                    m = await ch.fetch_message(int(mid))
                    return await m.edit(embeds=embeds)
                except discord.NotFound:
                    pass
            m = await ch.send(embeds=embeds)
            await self.bot.db.set_setting(guild.id, "_roster_msg", str(m.id))
        except discord.HTTPException:
            pass

    async def show_roster(self, interaction: discord.Interaction):
        await interaction.response.send_message(embeds=await self.build_roster(interaction.guild), ephemeral=True)

    @app_commands.command(name="roster", description="Показать составы")
    @app_commands.guild_only()
    async def roster(self, interaction: discord.Interaction):
        await self.show_roster(interaction)

    # ---------- личное дело ----------
    async def show_file(self, interaction: discord.Interaction, member: discord.Member):
        db, gid = self.bot.db, interaction.guild_id
        s = await db.settings(gid)
        profile = await db.profile(gid, member.id)
        e = make_embed(s, title=f"🗂️ Личное дело · {member.display_name}")
        e.set_thumbnail(url=member.display_avatar.url)
        if not profile:
            e.description = "Профиль не создан."
            return await interaction.response.send_message(embed=e, ephemeral=True)
        e.add_field(name="ФИО", value=profile["fio"]).add_field(name="Статик", value=profile["static"])
        pos = await db.one("SELECT * FROM roster WHERE guild_id=? AND user_id=?", (str(gid), str(member.id)))
        if pos:
            p = await db.item(gid, "position", pos["position_key"])
            e.add_field(name="Должность", value=f"{p['name']} · {p['unit']}\nс {fmt_ts(pos['since'], 'D')}" if p else pos["position_key"], inline=False)
        else:
            e.add_field(name="Должность", value="Не состоит в составах", inline=False)
        poss = await db.items(gid, "position")
        hist = await db.all("SELECT * FROM hr_log WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 5", (str(gid), str(member.id)))
        names = {"hire": "приём", "promote": "повышение", "demote": "понижение", "transfer": "перевод", "fire": "увольнение"}
        e.add_field(name="Кадровая история", value="\n".join(
            f"{fmt_ts(h['ts'], 'd')} · {names.get(h['action'])} → {poss.get(h['to_pos'], {}).get('name', '—')}" for h in hist) or "—", inline=False)
        docs = await db.all("SELECT * FROM docs WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 5", (str(gid), str(member.id)))
        e.add_field(name="Документы", value="\n".join(f"`#{d['id']}` {d['kind']} · {trim(d['title'], 60)} · {fmt_ts(d['ts'], 'd')}" for d in docs) or "—", inline=False)
        warns = await db.one("SELECT COUNT(*) c FROM cases WHERE guild_id=? AND user_id=? AND type='warn' AND active=1", (str(gid), str(member.id)))
        e.add_field(name="Предупреждения", value=str(warns["c"]))
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="file", description="Личное дело (своё или сотрудника — для кадровиков)")
    @app_commands.guild_only()
    async def file(self, interaction: discord.Interaction, user: discord.Member | None = None):
        s = await self.bot.db.settings(interaction.guild_id)
        if user and user.id != interaction.user.id and not is_staff(interaction.user, s, HR_KEY):
            return await interaction.response.send_message("Чужие личные дела доступны только кадровикам.", ephemeral=True)
        await self.show_file(interaction, user or interaction.user)

    # ---------- документы ----------
    doc = app_commands.Group(name="doc", description="Документы", guild_only=True)

    @doc.command(name="issue", description="Выдать документ (приказ, справка, выговор…)")
    @app_commands.autocomplete(kind=kind_ac)
    @app_commands.describe(kind="Вид документа", title="Заголовок", content="Текст")
    @staff_only(HR_KEY)
    async def doc_issue(self, interaction: discord.Interaction, user: discord.Member, kind: str, title: str, content: str):
        db, guild = self.bot.db, interaction.guild
        s = await db.settings(guild.id)
        did = await db.run("INSERT INTO docs(guild_id,user_id,kind,title,content,author_id,ts) VALUES(?,?,?,?,?,?,?)",
                           (str(guild.id), str(user.id), kind, title, content, str(interaction.user.id), now()))
        e = make_embed(s, title=f"📄 {kind}: {title}", desc=content[:3500])
        e.add_field(name="Кому", value=user.mention).add_field(name="Выдал", value=interaction.user.mention)
        e.set_footer(text=f"Документ №{did} · {s.get('footer') or ''}")
        await send_log(guild, s, "docs_channel", e)
        await dm(user, e)
        await db.notify(guild.id, user.id, f"Документ: {kind}", title)
        await interaction.response.send_message(f"Документ №{did} выдан {user.mention}.", ephemeral=True)

    @doc.command(name="list", description="Документы сотрудника")
    @staff_only(HR_KEY)
    async def doc_list(self, interaction: discord.Interaction, user: discord.Member):
        rows = await self.bot.db.all("SELECT * FROM docs WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 20",
                                     (str(interaction.guild_id), str(user.id)))
        await interaction.response.send_message(
            "\n".join(f"`#{d['id']}` **{d['kind']}** · {trim(d['title'], 70)} · {fmt_ts(d['ts'], 'd')}" for d in rows) or "Документов нет.", ephemeral=True)

    @doc.command(name="delete", description="Аннулировать документ по номеру")
    @staff_only(HR_KEY)
    async def doc_delete(self, interaction: discord.Interaction, number: int):
        await self.bot.db.run("DELETE FROM docs WHERE id=? AND guild_id=?", (number, str(interaction.guild_id)))
        await interaction.response.send_message(f"Документ №{number} аннулирован.", ephemeral=True)

    # ---------- бюджет ----------
    budget = app_commands.Group(name="budget", description="Бюджет", guild_only=True)

    async def _entry(self, interaction, amount: float, kind: str, category: str, comment: str | None):
        db, guild = self.bot.db, interaction.guild
        s = await db.settings(guild.id)
        if amount <= 0:
            return await interaction.response.send_message("Сумма должна быть больше нуля.", ephemeral=True)
        await db.run("INSERT INTO budget(guild_id,amount,kind,category,comment,author_id,ts) VALUES(?,?,?,?,?,?,?)",
                     (str(guild.id), amount, kind, category, comment, str(interaction.user.id), now()))
        bal = await self._balance(guild.id)
        cur = s.get("currency", "₽")
        inc = kind == "income"
        e = make_embed(s, title=f"💰 {'Поступление' if inc else 'Расход'}: {amount:,.0f} {cur}".replace(",", " "), color="#16A34A" if inc else "#DC2626")
        e.add_field(name="Статья", value=category).add_field(name="Баланс", value=f"{bal:,.0f} {cur}".replace(",", " "))
        e.add_field(name="Комментарий", value=comment or "—", inline=False).add_field(name="Автор", value=interaction.user.mention)
        await send_log(guild, s, "budget_channel", e)
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def _balance(self, gid) -> float:
        r = await self.bot.db.one("SELECT COALESCE(SUM(CASE WHEN kind='income' THEN amount ELSE -amount END),0) b FROM budget WHERE guild_id=?", (str(gid),))
        return r["b"]

    @budget.command(name="income", description="Записать поступление")
    @staff_only("budget_roles")
    async def b_income(self, interaction: discord.Interaction, amount: float, category: str = "Прочее", comment: str | None = None):
        await self._entry(interaction, amount, "income", category, comment)

    @budget.command(name="expense", description="Записать расход")
    @staff_only("budget_roles")
    async def b_expense(self, interaction: discord.Interaction, amount: float, category: str = "Прочее", comment: str | None = None):
        await self._entry(interaction, amount, "expense", category, comment)

    @budget.command(name="report", description="Баланс и последние операции")
    @staff_only("budget_roles")
    async def b_report(self, interaction: discord.Interaction):
        s = await self.bot.db.settings(interaction.guild_id)
        cur = s.get("currency", "₽")
        rows = await self.bot.db.all("SELECT * FROM budget WHERE guild_id=? ORDER BY id DESC LIMIT 10", (str(interaction.guild_id),))
        bal = await self._balance(interaction.guild_id)
        lines = [f"{'🟢 +' if r['kind'] == 'income' else '🔴 −'}{r['amount']:,.0f} {cur} · {r['category']} · {fmt_ts(r['ts'], 'd')}"
                 + (f" · _{trim(r['comment'], 50)}_" if r["comment"] else "") for r in rows]
        e = make_embed(s, title=f"💰 Баланс: {bal:,.0f} {cur}".replace(",", " "), desc="\n".join(lines) or "Операций нет.")
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot):
    await bot.add_cog(HR(bot))
