"""Новости: команда /news и публикация из веб-панели (с кнопками-ссылками и пингом ролей)."""
import discord
from discord import app_commands
from discord.ext import commands

from ..db import now
from ..schema import normalize_value
from ..util import chan, staff_only, trim
from .panels import panel_embed, panel_view

STYLES = {"news": ("📰", "Новость", ""), "announce": ("📢", "Объявление", ""),
          "update": ("🚀", "Обновление", ""), "important": ("⚠️", "Важно", "#DC2626")}
_COLOR = {"type": "color", "default": ""}


def _url(v) -> str:
    v = str(v or "").strip()
    return v if v.startswith(("http://", "https://")) else ""


def parse_buttons(text: str) -> list[dict]:
    """Строки вида «Название | https://ссылка» -> кнопки-ссылки."""
    out = []
    for line in (text or "").splitlines():
        label, _, url = line.partition("|")
        if _url(url) and label.strip():
            out.append({"label": label.strip()[:80], "url": url.strip()})
    return out[:25]


def build_payload(body: dict) -> dict:
    """Приводит данные из веб-панели к безопасному виду."""
    btns = []
    for b in (body.get("buttons") or [])[:25]:
        if isinstance(b, dict) and _url(b.get("url")) and str(b.get("label") or "").strip():
            btns.append({"label": str(b["label"]).strip()[:80], "url": _url(b["url"]), "emoji": str(b.get("emoji") or "").strip()[:60]})
    return {
        "channel": str(body.get("channel") or "").strip(),
        "title": str(body.get("title") or "").strip()[:256],
        "description": str(body.get("description") or "").strip()[:4000],
        "color": normalize_value(_COLOR, body.get("color")),
        "image": _url(body.get("image")), "thumbnail": _url(body.get("thumbnail")),
        "footer": str(body.get("footer") or "").strip()[:200],
        "buttons": btns,
        "ping_roles": [r for r in (str(x) for x in body.get("ping_roles") or []) if r.isdigit()],
    }


async def post_news(bot, guild: discord.Guild, channel, author_id: int, d: dict, file: discord.File | None = None):
    s = await bot.db.settings(guild.id)
    panel = {**d, "buttons": [{"label": b["label"], "action": b["url"], "emoji": b.get("emoji", "")} for b in d.get("buttons", [])]}
    embed, view = panel_embed(s, panel), panel_view(panel)
    if file:  # загруженную картинку прикрепляем к сообщению: ссылки Discord CDN со временем протухают
        embed.set_image(url=f"attachment://{file.filename}")
    pings = " ".join(f"<@&{r}>" for r in d.get("ping_roles", []))
    kw = {"view": view} if len(view.children) else {}
    if file:
        kw["file"] = file
    msg = await channel.send(content=pings or None, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True), **kw)
    await bot.db.run("INSERT INTO news(guild_id,channel_id,message_id,title,body,author_id,ts) VALUES(?,?,?,?,?,?,?)",
                     (str(guild.id), str(channel.id), str(msg.id), d.get("title"), d.get("description"), str(author_id), now()))
    return msg


class NewsModal(discord.ui.Modal, title="Новость"):
    heading = discord.ui.TextInput(label="Заголовок", max_length=200, required=True)
    text = discord.ui.TextInput(label="Текст новости", style=discord.TextStyle.paragraph, max_length=3900, required=True)
    buttons = discord.ui.TextInput(label="Кнопки-ссылки (по одной в строке)", style=discord.TextStyle.paragraph, required=False,
                                   max_length=800, placeholder="Название | https://ссылка")

    def __init__(self, cog, channel, style, ping, image):  # image: (bytes, имя файла) или None
        super().__init__(timeout=900)
        self.cog, self.channel, self.style, self.ping, self.image = cog, channel, style, ping, image

    async def on_submit(self, interaction: discord.Interaction):
        emoji, _, color = STYLES[self.style]
        d = build_payload({"title": f"{emoji} {self.heading.value}", "description": self.text.value, "color": color,
                           "buttons": parse_buttons(self.buttons.value),
                           "ping_roles": [self.ping.id] if self.ping else []})
        await interaction.response.defer(ephemeral=True)
        try:
            import io
            f = discord.File(io.BytesIO(self.image[0]), filename=self.image[1]) if self.image else None
            msg = await post_news(self.cog.bot, interaction.guild, self.channel, interaction.user.id, d, f)
        except discord.Forbidden:
            return await interaction.followup.send(f"⚠️ У бота нет прав писать в {self.channel.mention}.", ephemeral=True)
        except discord.HTTPException as e:
            return await interaction.followup.send(f"⚠️ Discord отклонил сообщение: {trim(e.text, 200)}", ephemeral=True)
        await interaction.followup.send(f"✅ Новость опубликована: {msg.jump_url}", ephemeral=True)


class News(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="news", description="Опубликовать новость от имени бота")
    @app_commands.describe(style="Тип сообщения", channel="Куда отправить (по умолчанию — канал новостей из настроек)",
                           ping="Какую роль упомянуть", image="Картинка к новости")
    @app_commands.choices(style=[app_commands.Choice(name=f"{v[0]} {v[1]}", value=k) for k, v in STYLES.items()])
    @app_commands.guild_only()
    @staff_only("news_roles")
    async def news(self, interaction: discord.Interaction, style: app_commands.Choice[str] | None = None,
                   channel: discord.TextChannel | None = None, ping: discord.Role | None = None,
                   image: discord.Attachment | None = None):
        s = await self.bot.db.settings(interaction.guild_id)
        target = channel or chan(interaction.guild, s.get("news_channel"))
        if not target or not hasattr(target, "send"):
            return await interaction.response.send_message(
                "Не выбран канал новостей: укажите его в команде или в настройках (веб-панель → Новости).", ephemeral=True)
        img = None
        if image and (image.content_type or "").startswith("image/") and image.size <= 8 * 1024 * 1024:
            img = (await image.read(), image.filename)
        await interaction.response.send_modal(NewsModal(self, target, style.value if style else "news", ping, img))


async def setup(bot):
    await bot.add_cog(News(bot))
