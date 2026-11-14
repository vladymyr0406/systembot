"""Панели — сообщения с кнопками. Кнопки «живут» после перезапуска (DynamicItem),
а действие кнопки задаётся строкой: form:<ключ> | ticket:<ключ> | cab:home | file:me | roster:show | https://… (ссылка)."""
import discord
from discord import app_commands
from discord.ext import commands

from ..schema import ACTION_MODULE, MODULE_NAME
from ..util import make_embed, parse_color, staff_only, to_int

STYLES = {"primary": discord.ButtonStyle.primary, "secondary": discord.ButtonStyle.secondary,
          "success": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger}


async def dispatch(interaction: discord.Interaction, action: str):
    bot = interaction.client
    kind, _, arg = action.partition(":")
    if not interaction.guild:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    await bot.db.ensure_seed(interaction.guild_id)
    mod = ACTION_MODULE.get(kind)
    if mod and not (await bot.db.settings(interaction.guild_id)).get(mod, True):
        return await interaction.response.send_message(f"Модуль «{MODULE_NAME[mod]}» отключён администрацией.", ephemeral=True)
    cog = {"form": "Forms", "cab": "Cabinet", "ticket": "Tickets", "file": "HR", "roster": "HR"}.get(kind)
    handler = bot.get_cog(cog) if cog else None
    if not handler:
        return await interaction.response.send_message("Это действие недоступно.", ephemeral=True)
    if kind == "form":
        await handler.open_form(interaction, arg)
    elif kind == "cab":
        await handler.show(interaction, arg or "home", fresh=True)
    elif kind == "ticket":
        await handler.open_ticket(interaction, arg)
    elif kind == "file":
        await handler.show_file(interaction, interaction.user)
    elif kind == "roster":
        await handler.show_roster(interaction)


class ActionButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rp:(?P<a>.+)"):
    def __init__(self, action: str, label: str | None = None, style=discord.ButtonStyle.secondary, emoji: str | None = None):
        super().__init__(discord.ui.Button(label=label, style=style, emoji=emoji or None, custom_id=f"rp:{action}"))
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["a"])

    async def callback(self, interaction: discord.Interaction):
        await dispatch(interaction, self.action)


def panel_embed(s: dict, p: dict) -> discord.Embed:
    e = discord.Embed(title=p.get("title") or None, description=p.get("description") or None,
                      colour=parse_color(p.get("color") or s.get("brand_color")))
    e.set_footer(text=p.get("footer") or s.get("footer") or s.get("bot_name") or "")
    if p.get("image"):
        e.set_image(url=p["image"])
    if p.get("thumbnail"):
        e.set_thumbnail(url=p["thumbnail"])
    return e


def panel_view(p: dict) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    for b in (p.get("buttons") or [])[:25]:
        act = (b.get("action") or "").strip()
        label = (b.get("label") or "")[:80] or None
        emoji = (b.get("emoji") or "").strip() or None
        if act.startswith(("http://", "https://")):
            v.add_item(discord.ui.Button(label=label or "Открыть", url=act, emoji=emoji))
        elif act:
            v.add_item(ActionButton(act, label, STYLES.get(b.get("style"), discord.ButtonStyle.secondary), emoji))
    return v


async def publish_panel(bot, guild: discord.Guild, key: str, channel=None):
    """Публикует панель или обновляет ранее опубликованное сообщение. Возвращает (message, ошибка)."""
    p = await bot.db.item(guild.id, "panel", key)
    if not p:
        return None, "Панель не найдена."
    s = await bot.db.settings(guild.id)
    target = channel or guild.get_channel(to_int(p.get("channel")) or 0)
    if not target or not hasattr(target, "send"):
        return None, "Не выбран канал публикации."
    embed, view = panel_embed(s, p), panel_view(p)
    old = p.get("_msg") or {}
    try:
        msg = None
        if old.get("id") and str(old.get("channel")) == str(target.id):
            try:
                msg = await target.fetch_message(int(old["id"]))
                await msg.edit(embed=embed, view=view, content=None)
            except discord.NotFound:
                msg = None
        if msg is None:
            msg = await target.send(embed=embed, view=view)
    except discord.Forbidden:
        return None, f"У бота нет прав писать в {target.mention}."
    except discord.HTTPException as e:
        return None, f"Discord отклонил сообщение: {e.text[:200]} (проверьте эмодзи и ссылки в кнопках)."
    p["_msg"] = {"channel": str(target.id), "id": str(msg.id)}
    await bot.db.save_item(guild.id, "panel", key, p)
    return msg, None


class Panels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_dynamic_items(ActionButton)

    panel = app_commands.Group(name="panel", description="Панели с кнопками", guild_only=True)

    async def key_autocomplete(self, interaction: discord.Interaction, current: str):
        items = await self.bot.db.items(interaction.guild_id, "panel")
        return [app_commands.Choice(name=f"{v.get('title', k)[:60]} ({k})", value=k)
                for k, v in items.items() if current.lower() in k.lower() or current.lower() in str(v.get("title", "")).lower()][:25]

    @panel.command(name="publish", description="Опубликовать или обновить панель")
    @app_commands.describe(key="Какая панель", channel="Канал (по умолчанию — из настроек панели)")
    @app_commands.autocomplete(key=key_autocomplete)
    @staff_only()
    async def publish(self, interaction: discord.Interaction, key: str, channel: discord.TextChannel | None = None):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.ensure_seed(interaction.guild_id)
        msg, err = await publish_panel(self.bot, interaction.guild, key, channel)
        await interaction.followup.send(err or f"Панель опубликована: {msg.jump_url}", ephemeral=True)

    @panel.command(name="list", description="Список панелей")
    @staff_only()
    async def list_(self, interaction: discord.Interaction):
        await self.bot.db.ensure_seed(interaction.guild_id)
        items = await self.bot.db.items(interaction.guild_id, "panel")
        lines = []
        for k, v in items.items():
            m = v.get("_msg")
            where = f"<#{m['channel']}>" if m else "не опубликована"
            lines.append(f"• `{k}` — **{v.get('title')}** · {where}")
        await interaction.response.send_message("\n".join(lines) or "Панелей нет.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Panels(bot))
