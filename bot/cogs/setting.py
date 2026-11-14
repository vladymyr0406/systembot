"""/setting — настройка бота прямо в Discord (дублирует основные разделы веб-панели)."""
import discord
from discord import app_commands
from discord.ext import commands

from .. import config
from ..schema import SETTINGS_SCHEMA, find_field, normalize_value
from ..util import has_any_role, is_admin, make_embed, trim
from .panels import publish_panel


def fmt_value(field: dict, v) -> str:
    t = field["type"]
    if v in (None, "", []):
        return "—"
    if t == "bool":
        return "✅ включено" if v else "❌ выключено"
    if t == "channel":
        return f"<#{v}>"
    if t == "role":
        return f"<@&{v}>"
    if t == "roles":
        return " ".join(f"<@&{r}>" for r in v)[:300]
    if t == "select":
        return next((o[1] for o in field.get("options", []) if o[0] == v), str(v))
    if t == "color":
        return f"`{v}`"
    return f"`{trim(str(v).replace(chr(10), ' ⏎ '), 90)}`"


class ValueModal(discord.ui.Modal):
    def __init__(self, cog, gkey, field, current):
        super().__init__(title=field["label"][:45], timeout=600)
        self.cog, self.gkey, self.field = cog, gkey, field
        long = field["type"] == "textarea"
        self.inp = discord.ui.TextInput(label="Значение"[:45], style=discord.TextStyle.paragraph if long else discord.TextStyle.short,
                                        default=(str(current) if current not in (None, []) else "")[:4000], required=False,
                                        max_length=4000 if long else 400)
        self.add_item(self.inp)

    async def on_submit(self, interaction: discord.Interaction):
        v = self.inp.value
        if self.field["type"] == "number":
            try:
                float(v)
            except ValueError:
                return await interaction.response.send_message("Нужно число.", ephemeral=True)
        await self.cog.save(interaction, self.field, v)
        await self.cog.render(interaction, self.gkey, None, note=f"Сохранено: **{self.field['label']}**")


class SView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=900)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id


class Setting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def allowed(self, member, s) -> bool:
        return is_admin(member) or has_any_role(member, s.get("admin_roles") or [])

    async def save(self, interaction, field, value):
        await self.bot.db.set_setting(interaction.guild_id, field["key"], normalize_value(field, value))

    @app_commands.command(name="setting", description="Настройка бота")
    @app_commands.guild_only()
    async def setting(self, interaction: discord.Interaction):
        s = await self.bot.db.settings(interaction.guild_id)
        if not await self.allowed(interaction.user, s):
            return await interaction.response.send_message("Настройки доступны только администраторам сервера.", ephemeral=True)
        await self.bot.db.ensure_seed(interaction.guild_id)
        await self.render(interaction, None, None, fresh=True)

    async def render(self, interaction, gkey, fkey, note=None, fresh=False):
        db, guild = self.bot.db, interaction.guild
        s = await db.settings(guild.id)
        view = SView(interaction.user.id)
        e = make_embed(s, title="⚙️ Настройка бота")
        web = discord.ui.Button(label="Веб-панель", url=config.BASE_URL, row=4)

        if gkey is None:
            e.description = ("Выберите раздел. Формы, панели, должности и категории тикетов (добавление и удаление элементов) "
                             "настраиваются в **веб-панели**.")
            sel = discord.ui.Select(placeholder="Раздел настроек…", options=[
                discord.SelectOption(label=g["title"], value=g["group"], emoji=g["icon"]) for g in SETTINGS_SCHEMA
            ] + [discord.SelectOption(label="Панели (публикация)", value="__panels", emoji="🧩")])

            async def on_group(i: discord.Interaction):
                await self.render(i, sel.values[0], None)
            sel.callback = on_group
            view.add_item(sel)
        elif gkey == "__panels":
            e.title = "🧩 Панели"
            items = await db.items(guild.id, "panel")
            e.description = (note + "\n\n" if note else "") + "Выберите панель — она будет опубликована (или обновлена) в своём канале. " \
                "Если канал не задан, — в текущем.\n\n" + "\n".join(
                    f"• **{v['title']}** (`{k}`) · {('<#' + v['_msg']['channel'] + '>') if v.get('_msg') else 'не опубликована'}" for k, v in items.items())
            if items:
                sel = discord.ui.Select(placeholder="Какую панель опубликовать?", options=[
                    discord.SelectOption(label=f"{v['title']}"[:100], value=k, description=k) for k, v in list(items.items())[:25]])

                async def on_panel(i: discord.Interaction):
                    await i.response.defer()
                    msg, err = await publish_panel(self.bot, i.guild, sel.values[0], None if (items[sel.values[0]].get("channel")) else i.channel)
                    await self.render(i, "__panels", None, note=f"❌ {err}" if err else f"✅ Опубликовано: {msg.jump_url}")
                sel.callback = on_panel
                view.add_item(sel)
            self._back(view)
        else:
            group = next(g for g in SETTINGS_SCHEMA if g["group"] == gkey)
            if fkey:
                field = find_field(fkey)[1]
                e.title = f"{group['icon']} {group['title']} · {field['label'][:200]}"
                e.description = f"Сейчас: {fmt_value(field, s.get(fkey))}"
                self._editor(view, gkey, field, s)
            else:
                e.title = f"{group['icon']} {group['title']}"
                e.description = (note + "\n\n" if note else "") + "\n".join(
                    f"**{f['label']}**\n{fmt_value(f, s.get(f['key']))}" for f in group["fields"])
                if len(e.description) > 3900:
                    e.description = e.description[:3900] + "…"
                sel = discord.ui.Select(placeholder="Что изменить?", options=[
                    discord.SelectOption(label=f["label"][:100], value=f["key"],
                                         description=trim(fmt_value(f, s.get(f["key"])).replace("`", ""), 90)[:100]) for f in group["fields"][:25]])

                async def on_field(i: discord.Interaction):
                    f = find_field(sel.values[0])[1]
                    if f["type"] == "bool":
                        cur = (await self.bot.db.settings(i.guild_id)).get(f["key"])
                        await self.bot.db.set_setting(i.guild_id, f["key"], not cur)
                        return await self.render(i, gkey, None, note=f"Переключено: **{f['label']}**")
                    if f["type"] in ("text", "textarea", "number", "color"):
                        cur = (await self.bot.db.settings(i.guild_id)).get(f["key"])
                        return await i.response.send_modal(ValueModal(self, gkey, f, cur))
                    await self.render(i, gkey, f["key"])
                sel.callback = on_field
                view.add_item(sel)
                b = discord.ui.Button(label="« Назад", row=3)

                async def home(i): await self.render(i, None, None)
                b.callback = home
                view.add_item(b)
        view.add_item(web)
        if fresh:
            await interaction.response.send_message(embed=e, view=view, ephemeral=True)
        elif interaction.response.is_done():
            await interaction.edit_original_response(embed=e, view=view)
        else:
            await interaction.response.edit_message(embed=e, view=view)

    def _back(self, view, gkey=None):
        b = discord.ui.Button(label="« Назад", row=3)

        async def cb(i):
            await self.render(i, gkey, None)
        b.callback = cb
        view.add_item(b)

    def _editor(self, view, gkey, field, s):
        t, key = field["type"], field["key"]

        async def done(i, value):
            await self.save(i, field, value)
            await self.render(i, gkey, None, note=f"Сохранено: **{field['label']}**")

        if t in ("channel",):
            sel = discord.ui.ChannelSelect(placeholder="Выберите канал", min_values=1, max_values=1,
                                           channel_types=[discord.ChannelType.text, discord.ChannelType.news])

            async def cb(i): await done(i, sel.values[0].id)
            sel.callback = cb
            view.add_item(sel)
        elif t in ("role", "roles"):
            sel = discord.ui.RoleSelect(placeholder="Выберите роль" + ("и" if t == "roles" else ""), min_values=0 if t == "roles" else 1,
                                        max_values=25 if t == "roles" else 1)

            async def cb(i):
                ids = [r.id for r in sel.values]
                await done(i, ids if t == "roles" else (ids[0] if ids else None))
            sel.callback = cb
            view.add_item(sel)
        elif t == "select":
            sel = discord.ui.Select(options=[discord.SelectOption(label=o[1], value=o[0], default=s.get(key) == o[0]) for o in field["options"]])

            async def cb(i): await done(i, sel.values[0])
            sel.callback = cb
            view.add_item(sel)
        if t in ("channel", "role", "roles"):
            clr = discord.ui.Button(label="Очистить", style=discord.ButtonStyle.danger, row=2)

            async def c(i): await done(i, [] if t == "roles" else None)
            clr.callback = c
            view.add_item(clr)
        self._back(view, gkey)


async def setup(bot):
    await bot.add_cog(Setting(bot))
