"""Личный кабинет: данные профиля, обращения, уведомления (виден только владельцу)."""
import discord
from discord.ext import commands

from ..util import STATUS_ICON, STATUS_TEXT, fmt_ts, make_embed, trim

PAGES = ("home", "profile", "apps", "notifs")


class CabinetView(discord.ui.View):
    def __init__(self, cog, owner_id: int, page: str, has_profile: bool):
        super().__init__(timeout=600)
        self.cog, self.owner_id, self.page = cog, owner_id, page
        top = discord.ui.Button(label="Изменить данные" if has_profile else "Заполнить профиль",
                                style=discord.ButtonStyle.primary, row=0)
        top.callback = self.on_edit
        self.add_item(top)
        for i, (key, label) in enumerate((("profile", "Профиль"), ("apps", "Мои обращения"), ("notifs", "Уведомления"))):
            b = discord.ui.Button(label=label, row=1,
                                  style=discord.ButtonStyle.primary if page == key else discord.ButtonStyle.secondary)
            b.callback = self._nav(key)
            self.add_item(b)

    async def interaction_check(self, interaction):
        return interaction.user.id == self.owner_id

    def _nav(self, key):
        async def cb(interaction: discord.Interaction):
            await self.cog.show(interaction, key)
        return cb

    async def on_edit(self, interaction: discord.Interaction):
        db = self.cog.bot.db
        profile = await db.profile(interaction.guild_id, interaction.user.id)
        forms = await db.items(interaction.guild_id, "form")
        want = "change" if profile else "profile"
        key = next((k for k, f in forms.items() if f.get("kind") == want and f.get("enabled")), None)
        if not key:
            return await interaction.response.send_message("Форма сейчас недоступна.", ephemeral=True)
        await self.cog.bot.get_cog("Forms").open_form(interaction, key)


class Cabinet(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def show(self, interaction: discord.Interaction, page: str = "home", fresh: bool = False):
        db, gid, user = self.bot.db, interaction.guild_id, interaction.user
        s = await db.settings(gid)
        profile = await db.profile(gid, user.id)
        e = make_embed(s, title="Личный кабинет")
        if page == "home":
            e.description = "Ваши данные, обращения и уведомления на этом сервере."
            if profile:
                e.description += (f"\n\n**ФИО:** {profile['fio']}\n**Статик:** {profile['static']}"
                                  f"\n**Статус профиля:** {'Активен' if profile['status'] == 'active' else profile['status']}"
                                  "\n\nИзменения данных вступают в силу после одобрения заявки.")
            else:
                e.description += "\n\nПрофиль ещё не создан — нажмите **Заполнить профиль**."
        elif page == "profile":
            e.title = "Личный кабинет · Профиль"
            if not profile:
                e.description = "Профиль ещё не создан."
            else:
                pos = await db.one("SELECT * FROM roster WHERE guild_id=? AND user_id=?", (str(gid), str(user.id)))
                position = "—"
                if pos:
                    p = await db.item(gid, "position", pos["position_key"])
                    position = f"{p['name']} ({p['unit']})" if p else pos["position_key"]
                docs = await db.one("SELECT COUNT(*) c FROM docs WHERE guild_id=? AND user_id=?", (str(gid), str(user.id)))
                warns = await db.one("SELECT COUNT(*) c FROM cases WHERE guild_id=? AND user_id=? AND type='warn' AND active=1",
                                     (str(gid), str(user.id)))
                e.add_field(name="ФИО", value=profile["fio"])
                e.add_field(name="Статик", value=profile["static"])
                e.add_field(name="Должность", value=position, inline=False)
                e.add_field(name="Профиль создан", value=fmt_ts(profile["created_at"]))
                e.add_field(name="Обновлён", value=fmt_ts(profile["updated_at"]))
                e.add_field(name="Документов", value=str(docs["c"]))
                e.add_field(name="Активных предупреждений", value=str(warns["c"]))
        elif page == "apps":
            e.title = "Личный кабинет · Мои обращения"
            rows = await db.all("SELECT * FROM applications WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10",
                                (str(gid), str(user.id)))
            forms = await db.items(gid, "form")
            lines = []
            for r in rows:
                title = forms.get(r["form_key"], {}).get("title", r["form_key"])
                lines.append(f"{STATUS_ICON[r['status']]} **#{r['id']}** · {title} · {STATUS_TEXT[r['status']].lower()} · {fmt_ts(r['created_at'], 'd')}")
            e.description = "\n".join(lines) or "Обращений пока нет."
        else:
            e.title = "Личный кабинет · Уведомления"
            rows = await db.all("SELECT * FROM notifications WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 8",
                                (str(gid), str(user.id)))
            e.description = "\n\n".join(f"**{r['title']}** · {fmt_ts(r['created_at'], 'R')}\n{trim(r['body'], 200)}" for r in rows) \
                or "Уведомлений нет."
        view = CabinetView(self, user.id, page, bool(profile))
        if fresh or interaction.response.is_done():
            await interaction.response.send_message(embed=e, view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(embed=e, view=view)

    @discord.app_commands.command(name="cabinet", description="Личный кабинет: профиль, обращения, уведомления")
    @discord.app_commands.guild_only()
    async def cabinet(self, interaction: discord.Interaction):
        await self.bot.db.ensure_seed(interaction.guild_id)
        await self.show(interaction, "home", fresh=True)


async def setup(bot):
    await bot.add_cog(Cabinet(bot))
