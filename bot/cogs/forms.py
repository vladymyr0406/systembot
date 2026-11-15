"""Заявки: форма (modal) → рассмотрение в канале с кнопками → решение, роли, ЛС, уведомление."""
import json
import re

import discord
from discord.ext import commands

from ..db import now
from ..util import (STATUS_ICON, STATUS_TEXT, chan, dm, fmt_ts, has_any_role, is_admin, is_staff,
                    make_embed, parse_color, send_log, to_int, trim)

GREEN, RED, YELLOW = "#16A34A", "#DC2626", "#F59E0B"


def app_embed(s: dict, form: dict, app: dict, user: discord.abc.User | None) -> discord.Embed:
    data = json.loads(app["data"]) if isinstance(app["data"], str) else app["data"]
    color = {"approved": GREEN, "denied": RED}.get(app["status"], None)
    e = make_embed(s, title=f"{form.get('title', 'Заявка')} · #{app['id']}", color=color)
    if user:
        e.set_author(name=f"{user.display_name} ({user.id})", icon_url=user.display_avatar.url)
    if data.get("_choice"):
        e.add_field(name="Тип", value=data["_choice"], inline=False)
    for f in form.get("fields", []):
        e.add_field(name=f["label"][:250], value=trim(data.get(f["key"]), 1000) or "—", inline=f.get("style") == "short")
    e.add_field(name="Статус", value=f"{STATUS_ICON[app['status']]} {STATUS_TEXT[app['status']]}", inline=False)
    if app["status"] != "pending":
        who = f"<@{app['reviewer_id']}>" if app.get("reviewer_id") else "—"
        e.add_field(name="Рассмотрел", value=f"{who} · {fmt_ts(app.get('decided_at'))}", inline=True)
        if app.get("reason"):
            e.add_field(name="Комментарий", value=trim(app["reason"], 1000), inline=False)
    e.timestamp = discord.utils.utcnow()
    return e


class FormModal(discord.ui.Modal):
    def __init__(self, cog, key: str, form: dict, prefill: dict, choice: dict | None = None):
        base = form.get("title") or "Заявка"
        super().__init__(title=(f"{base} · {choice['label']}" if choice else base)[:45], timeout=900)
        self.cog, self.key, self.form, self.choice = cog, key, form, choice
        self.inputs: dict[str, discord.ui.TextInput | discord.ui.Select] = {}
        for f in (form.get("fields") or [])[:5]:
            label = (f.get("label") or "Поле")[:45]
            if f.get("style") == "select":
                opts = [o.strip()[:100] for o in re.split(r"[,\n]", f.get("options") or "") if o.strip()][:25]
                if opts:
                    cur = prefill.get(f["key"])
                    sel = discord.ui.Select(required=bool(f.get("required")), options=[
                        discord.SelectOption(label=o, value=o, default=(o == cur)) for o in opts],
                        placeholder=(f.get("placeholder") or "Выберите…")[:100])
                    self.add_item(discord.ui.Label(text=label, component=sel))
                    self.inputs[f["key"]] = sel
                    continue
            maxlen = max(1, min(int(f.get("max_length") or 100), 4000))
            default = (prefill.get(f["key"]) or "")[:maxlen] or None
            ti = discord.ui.TextInput(
                label=label,
                style=discord.TextStyle.paragraph if f.get("style") == "long" else discord.TextStyle.short,
                required=bool(f.get("required")), placeholder=(f.get("placeholder") or "")[:100] or None,
                max_length=maxlen, default=default)
            self.add_item(ti)
            self.inputs[f["key"]] = ti

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.submit(interaction, self.key, self.form, {k: _val(i) for k, i in self.inputs.items()}, self.choice)


def _val(i) -> str:
    if isinstance(i, discord.ui.Select):
        return (i.values[0] if i.values else "").strip()
    return (i.value or "").strip()


def _emoji(v):
    v = (v or "").strip()
    return v if v and (len(v) <= 8 or v.startswith("<")) else None


class ChoiceView(discord.ui.View):
    """Шаг 1: выбор варианта (например, тип кадрового решения). Шаг 2: форма."""
    def __init__(self, cog, key, form, prefill, owner_id):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        choices = [c for c in form.get("choices") or [] if c.get("label")][:25]
        sel = discord.ui.Select(placeholder="Выберите вариант…", options=[
            discord.SelectOption(label=c["label"][:100], value=str(i), emoji=_emoji(c.get("emoji"))) for i, c in enumerate(choices)])

        async def cb(i: discord.Interaction):
            await i.response.send_modal(FormModal(cog, key, form, prefill, choices[int(sel.values[0])]))
        sel.callback = cb
        self.add_item(sel)

    async def interaction_check(self, interaction):
        return interaction.user.id == self.owner_id


class ReviewModal(discord.ui.Modal):
    def __init__(self, cog, op: str, app_id: int):
        super().__init__(title="Одобрить заявку" if op == "approve" else "Отклонить заявку", timeout=600)
        self.cog, self.op, self.app_id = cog, op, app_id
        self.reason = discord.ui.TextInput(
            label="Комментарий" + (" (необязательно)" if op == "approve" else " / причина отказа"),
            style=discord.TextStyle.paragraph, required=op == "deny", max_length=800)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.finish(interaction, self.app_id, self.op, self.reason.value.strip())


class AppDecision(discord.ui.DynamicItem[discord.ui.Button], template=r"rpapp:(?P<op>approve|deny):(?P<id>\d+)"):
    def __init__(self, op: str, app_id: int):
        approve = op == "approve"
        super().__init__(discord.ui.Button(
            label="Одобрить" if approve else "Отклонить", emoji="✅" if approve else "⛔",
            style=discord.ButtonStyle.success if approve else discord.ButtonStyle.danger,
            custom_id=f"rpapp:{op}:{app_id}"))
        self.op, self.app_id = op, app_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["op"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.get_cog("Forms").start_review(interaction, self.op, self.app_id)


class Forms(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_dynamic_items(AppDecision)

    # ---------- открытие формы ----------
    async def open_form(self, interaction: discord.Interaction, key: str):
        db, gid, uid = self.bot.db, interaction.guild_id, interaction.user.id
        form = await db.item(gid, "form", key)
        if not form or not form.get("enabled"):
            return await interaction.response.send_message("Эта форма сейчас недоступна.", ephemeral=True)
        profile = await db.profile(gid, uid)
        kind = form.get("kind")
        allowed = form.get("allowed_roles") or []
        if allowed:
            st = await db.settings(gid)
            if not (is_staff(interaction.user, st) or has_any_role(interaction.user, allowed)):
                return await interaction.response.send_message("Эта форма доступна только отдельным ролям.", ephemeral=True)
        if form.get("require_profile") and not profile:
            return await interaction.response.send_message(
                "Сначала нужен профиль: нажмите «Заполнить профиль» и дождитесь одобрения.", ephemeral=True)
        if kind == "profile" and profile:
            return await interaction.response.send_message(
                "Ваш профиль уже создан. Чтобы изменить данные, подайте заявку на изменение.", ephemeral=True)
        pend = await db.one("SELECT id FROM applications WHERE guild_id=? AND user_id=? AND form_key=? AND status='pending'",
                            (str(gid), str(uid), key))
        if pend and kind in ("profile", "change"):
            return await interaction.response.send_message(
                f"Ваша заявка #{pend['id']} уже на рассмотрении — дождитесь решения.", ephemeral=True)
        cd = int(form.get("cooldown_min") or 0)
        if cd:
            last = await db.one("SELECT created_at FROM applications WHERE guild_id=? AND user_id=? AND form_key=? ORDER BY id DESC LIMIT 1",
                                (str(gid), str(uid), key))
            if last and last["created_at"] + cd * 60 > now():
                return await interaction.response.send_message(
                    f"Подавать заявку этого типа можно раз в {cd} мин. Повторите {fmt_ts(last['created_at'] + cd * 60, 'R')}.", ephemeral=True)
        prefill = {}
        if profile and kind == "change":
            prefill = {"fio": profile["fio"], "static": profile["static"]}
        if any(c.get("label") for c in form.get("choices") or []):
            return await interaction.response.send_message("Выберите тип:", view=ChoiceView(self, key, form, prefill, uid), ephemeral=True)
        await interaction.response.send_modal(FormModal(self, key, form, prefill))

    # ---------- сообщение без рассмотрения («СМС») ----------
    async def post_message(self, interaction: discord.Interaction, key: str, form: dict, values: dict, choice: dict | None):
        db, guild, user = self.bot.db, interaction.guild, interaction.user
        s = await db.settings(guild.id)
        ch = chan(guild, form.get("review_channel")) or chan(guild, s.get("log_forms"))
        if not ch or not hasattr(ch, "send"):
            return await interaction.response.send_message("⚠️ Канал публикации не настроен. Сообщите администрации.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        profile = await db.profile(guild.id, user.id)
        emoji = (choice or {}).get("emoji") or ""
        title = form.get("title") or "Сообщение"
        if choice:
            title = f"{emoji + ' ' if emoji else ''}{title} · {choice['label']}"
        e = make_embed(s, title=title, color=(choice or {}).get("color") or None)
        for f in form.get("fields", []):
            val = values.get(f["key"])
            if val or f.get("required"):
                e.add_field(name=f["label"][:250], value=trim(val, 1000) or "—", inline=f.get("style") == "short")
        who = f"{user.mention}" + (f"\n{profile['fio']} · {profile['static']}" if profile else "")
        e.add_field(name="Отправил", value=who, inline=False)
        e.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        e.timestamp = discord.utils.utcnow()
        pings = [int(r) for r in form.get("ping_roles") or [] if str(r).isdigit()]
        try:
            msg = await ch.send(content=" ".join(f"<@&{r}>" for r in pings) or None, embed=e,
                                allowed_mentions=discord.AllowedMentions(roles=True, users=False))
        except discord.HTTPException:
            return await interaction.followup.send("⚠️ Не удалось отправить в канал (нет прав у бота).", ephemeral=True)
        data = dict(values)
        if choice:
            data["_choice"] = choice["label"]
        await db.run("INSERT INTO applications(guild_id,user_id,form_key,data,status,channel_id,message_id,created_at,decided_at) "
                     "VALUES(?,?,?,?, 'sent', ?,?,?,?)",
                     (str(guild.id), str(user.id), key, json.dumps(data, ensure_ascii=False), str(ch.id), str(msg.id), now(), now()))
        await interaction.followup.send(f"✅ Отправлено: {msg.jump_url}", ephemeral=True)

    # ---------- отправка ----------
    async def submit(self, interaction: discord.Interaction, key: str, form: dict, values: dict, choice: dict | None = None):
        if form.get("mode") == "message":
            return await self.post_message(interaction, key, form, values, choice)
        if choice:
            values["_choice"] = choice["label"]
        db, guild, user = self.bot.db, interaction.guild, interaction.user
        s = await db.settings(guild.id)
        if form.get("kind") in ("profile", "change"):
            fio = " ".join(values.get("fio", "").split())
            static = values.get("static", "").strip()
            values["fio"], values["static"] = fio, static
            try:
                ok_fio = re.fullmatch(s.get("fio_regex") or r".+", fio)
            except re.error:
                ok_fio = True
            try:
                ok_static = re.fullmatch(s.get("static_regex") or r".+", static)
            except re.error:
                ok_static = True
            if not ok_fio:
                return await interaction.response.send_message(f"⚠️ ФИО не принято. {s.get('fio_hint') or ''}", ephemeral=True)
            if not ok_static:
                return await interaction.response.send_message("⚠️ Статик указан неверно (проверьте формат).", ephemeral=True)
            if s.get("profile_unique_static"):
                dup = await db.one("SELECT user_id FROM profiles WHERE guild_id=? AND static=? AND user_id!=?",
                                   (str(guild.id), static, str(user.id)))
                if dup:
                    return await interaction.response.send_message("⚠️ Профиль с таким Статиком уже существует.", ephemeral=True)
        review = chan(guild, form.get("review_channel")) or chan(guild, s.get("log_forms"))
        if not review or not hasattr(review, "send"):
            return await interaction.response.send_message(
                "⚠️ Канал рассмотрения не настроен. Сообщите администрации.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        app_id = await db.run(
            "INSERT INTO applications(guild_id,user_id,form_key,data,status,created_at) VALUES(?,?,?,?, 'pending', ?)",
            (str(guild.id), str(user.id), key, json.dumps(values, ensure_ascii=False), now()))
        app = await db.one("SELECT * FROM applications WHERE id=?", (app_id,))
        view = discord.ui.View(timeout=None)
        view.add_item(AppDecision("approve", app_id))
        view.add_item(AppDecision("deny", app_id))
        pings = [int(r) for r in form.get("ping_roles") or [] if str(r).isdigit()]
        try:
            msg = await review.send(
                content=" ".join(f"<@&{r}>" for r in pings) or None, embed=app_embed(s, form, app, user), view=view,
                allowed_mentions=discord.AllowedMentions(roles=True))
        except discord.HTTPException:
            await db.run("DELETE FROM applications WHERE id=?", (app_id,))
            return await interaction.followup.send("⚠️ Не удалось отправить заявку в канал рассмотрения (нет прав у бота).", ephemeral=True)
        thread = None
        if form.get("use_threads") and isinstance(review, discord.TextChannel):
            try:
                thread = await msg.create_thread(name=f"Заявка #{app_id} · {user.display_name}"[:100], auto_archive_duration=4320)
            except discord.HTTPException:
                thread = None
        await db.run("UPDATE applications SET channel_id=?, message_id=?, thread_id=? WHERE id=?",
                     (str(review.id), str(msg.id), str(thread.id) if thread else None, app_id))

        e = make_embed(s, title="Заявка отправлена", desc=f"Ваша заявка «{form.get('title')}» отправлена на рассмотрение.", color=GREEN)
        for f in form.get("fields", [])[:3]:
            e.add_field(name=f["label"][:250], value=f"`{trim(values.get(f['key']), 200)}`", inline=False)
        can_see = review.permissions_for(user).view_channel if isinstance(user, discord.Member) else False
        e.add_field(name="Канал рассмотрения", value=review.mention if can_see else "🔒 Нет доступа", inline=False)
        e.set_footer(text=f"Заявка #{app_id}. Уведомление придёт после принятия решения.")
        v = discord.ui.View()
        if thread and can_see:
            v.add_item(discord.ui.Button(label="Перейти в ветку обсуждения", url=thread.jump_url))
        await interaction.followup.send(embed=e, view=v, ephemeral=True)

    # ---------- рассмотрение ----------
    def _can_review(self, member: discord.Member, s: dict, form: dict) -> bool:
        return is_staff(member, s) or has_any_role(member, form.get("reviewer_roles") or [])

    async def start_review(self, interaction: discord.Interaction, op: str, app_id: int):
        app = await self.bot.db.one("SELECT * FROM applications WHERE id=?", (app_id,))
        if not app:
            return await interaction.response.send_message("Заявка не найдена.", ephemeral=True)
        s = await self.bot.db.settings(interaction.guild_id)
        form = await self.bot.db.item(interaction.guild_id, "form", app["form_key"]) or {}
        if not self._can_review(interaction.user, s, form):
            return await interaction.response.send_message("У вас нет права рассматривать эти заявки.", ephemeral=True)
        if app["status"] != "pending":
            return await interaction.response.send_message("Заявка уже рассмотрена.", ephemeral=True)
        if str(interaction.user.id) == app["user_id"] and not is_admin(interaction.user):
            return await interaction.response.send_message("Нельзя рассматривать собственную заявку.", ephemeral=True)
        await interaction.response.send_modal(ReviewModal(self, op, app_id))

    async def finish(self, interaction: discord.Interaction, app_id: int, op: str, reason: str):
        db, guild = self.bot.db, interaction.guild
        await interaction.response.defer(ephemeral=True)
        app = await db.one("SELECT * FROM applications WHERE id=?", (app_id,))
        if not app or app["status"] != "pending":
            return await interaction.followup.send("Заявка уже рассмотрена.", ephemeral=True)
        s = await db.settings(guild.id)
        form = await db.item(guild.id, "form", app["form_key"]) or {"title": "Заявка", "fields": []}
        status = "approved" if op == "approve" else "denied"
        await db.run("UPDATE applications SET status=?, reason=?, reviewer_id=?, decided_at=? WHERE id=?",
                     (status, reason or None, str(interaction.user.id), now(), app_id))
        app = await db.one("SELECT * FROM applications WHERE id=?", (app_id,))
        data = json.loads(app["data"])
        member = guild.get_member(int(app["user_id"]))
        notes = []
        if status == "approved":
            if form.get("kind") in ("profile", "change") and data.get("fio") and data.get("static"):
                await db.upsert_profile(guild.id, app["user_id"], data["fio"], data["static"])
                if s.get("nickname_enabled") and member:
                    nick = s.get("nickname_format", "{fio} | {static}").format(fio=data["fio"], static=data["static"])[:32]
                    try:
                        await member.edit(nick=nick, reason=f"Профиль одобрен (заявка #{app_id})")
                    except discord.HTTPException:
                        notes.append("не удалось сменить ник")
            if member:
                try:
                    add = [guild.get_role(int(r)) for r in form.get("give_roles") or []]
                    rem = [guild.get_role(int(r)) for r in form.get("remove_roles") or []]
                    add, rem = [r for r in add if r], [r for r in rem if r]
                    if add:
                        await member.add_roles(*add, reason=f"Заявка #{app_id}")
                    if rem:
                        await member.remove_roles(*rem, reason=f"Заявка #{app_id}")
                except discord.HTTPException:
                    notes.append("не удалось выдать/снять роли")
        text = form.get("accept_text") if status == "approved" else form.get("deny_text")
        title = f"Заявка #{app_id} {'одобрена' if status == 'approved' else 'отклонена'}"
        body = f"{text or ''}\n\n**Форма:** {form.get('title')}"
        if reason:
            body += f"\n**Комментарий:** {reason}"
        await db.notify(guild.id, app["user_id"], title, body)
        if member:
            e = make_embed(s, title=title, desc=f"{body}\n\nСервер: **{guild.name}**", color=GREEN if status == "approved" else RED)
            await dm(member, e)

        # обновляем сообщение рассмотрения
        ch = chan(guild, app["channel_id"])
        if ch:
            try:
                msg = await ch.fetch_message(int(app["message_id"]))
                author = member or await self.bot.fetch_user(int(app["user_id"]))
                await msg.edit(embed=app_embed(s, form, app, author), view=None)
            except discord.HTTPException:
                pass
        if app.get("thread_id"):
            th = guild.get_thread(int(app["thread_id"]))
            if th:
                try:
                    await th.send(f"{STATUS_ICON[status]} **{STATUS_TEXT[status]}** — {interaction.user.mention}" + (f"\n> {reason}" if reason else ""),
                                  allowed_mentions=discord.AllowedMentions.none())
                    await th.edit(archived=True, locked=True)
                except discord.HTTPException:
                    pass
        log_ch = chan(guild, s.get("log_forms"))
        if log_ch and (not ch or log_ch.id != ch.id):
            e = make_embed(s, title=f"{STATUS_ICON[status]} Заявка #{app_id}: {STATUS_TEXT[status].lower()}",
                           desc=f"**Форма:** {form.get('title')}\n**Заявитель:** <@{app['user_id']}>\n**Рассмотрел:** {interaction.user.mention}"
                                + (f"\n**Комментарий:** {reason}" if reason else ""))
            await send_log(guild, s, "log_forms", e)
        await interaction.followup.send(f"Готово: заявка #{app_id} — {STATUS_TEXT[status].lower()}." + (f" ({'; '.join(notes)})" if notes else ""),
                                        ephemeral=True)


async def setup(bot):
    await bot.add_cog(Forms(bot))
