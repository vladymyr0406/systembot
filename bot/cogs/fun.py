"""Справка, информация и лёгкие развлечения."""
import random

import discord
from discord import app_commands
from discord.ext import commands

from ..util import fmt_ts, make_embed


class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Что умеет бот")
    @app_commands.guild_only()
    async def help(self, interaction: discord.Interaction):
        s = await self.bot.db.settings(interaction.guild_id)
        e = make_embed(s, title=f"{s.get('bot_name') or 'Бот'} · команды")
        e.add_field(name="👤 Игроки", value="`/cabinet` — личный кабинет\n`/file` — личное дело\n`/roster` — составы", inline=False)
        e.add_field(name="🛡️ Администрация", value="`/ban` `/kick` `/timeout` `/warn` `/warns` `/cases` `/purge` `/slowmode` `/lock`", inline=False)
        e.add_field(name="🗂️ Кадры и документы", value="`/hr hire|transfer|fire|history`\n`/doc issue|list|delete`\n`/budget income|expense|report`", inline=False)
        e.add_field(name="🎵 Музыка", value="`/play` `/pause` `/skip` `/stop` `/queue` `/nowplaying` `/volume` `/loop` `/shuffle`", inline=False)
        e.add_field(name="🎲 Развлечения", value="`/roll` `/coin` `/choose` `/avatar` `/userinfo` `/serverinfo`", inline=False)
        e.add_field(name="⚙️ Настройка", value="`/setup create` — бот сам создаст каналы · `/setting` · `/status` · `/news` · `/panel publish` · веб-панель", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="ping", description="Задержка бота")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🏓 {round(self.bot.latency * 1000)} мс", ephemeral=True)

    @app_commands.command(name="roll", description="Бросок кубика, например 2d6 или 20")
    async def roll(self, interaction: discord.Interaction, dice: str = "1d6"):
        try:
            n, _, f = dice.lower().partition("d") if "d" in dice.lower() else ("1", "d", dice)
            n, f = int(n or 1), int(f)
            if not (1 <= n <= 20 and 2 <= f <= 1000):
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("Формат: `2d6`, `1d20` или просто `100`.", ephemeral=True)
        rolls = [random.randint(1, f) for _ in range(n)]
        await interaction.response.send_message(f"🎲 {interaction.user.mention}: **{sum(rolls)}**" + (f" ({', '.join(map(str, rolls))})" if n > 1 else ""))

    @app_commands.command(name="coin", description="Подбросить монетку")
    async def coin(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🪙 {random.choice(['Орёл', 'Решка'])}")

    @app_commands.command(name="choose", description="Выбрать из вариантов (через запятую)")
    async def choose(self, interaction: discord.Interaction, options: str):
        opts = [o.strip() for o in options.split(",") if o.strip()]
        if len(opts) < 2:
            return await interaction.response.send_message("Нужно минимум два варианта через запятую.", ephemeral=True)
        await interaction.response.send_message(f"🤔 Выбираю: **{random.choice(opts)}**")

    @app_commands.command(name="avatar", description="Аватар участника")
    async def avatar(self, interaction: discord.Interaction, user: discord.User | None = None):
        u = user or interaction.user
        e = discord.Embed(title=f"Аватар {u.display_name}").set_image(url=u.display_avatar.url)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="userinfo", description="Информация об участнике")
    @app_commands.guild_only()
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member | None = None):
        m = user or interaction.user
        s = await self.bot.db.settings(interaction.guild_id)
        p = await self.bot.db.profile(interaction.guild_id, m.id)
        e = make_embed(s, title=str(m)).set_thumbnail(url=m.display_avatar.url)
        e.add_field(name="ID", value=str(m.id))
        e.add_field(name="Аккаунт создан", value=fmt_ts(m.created_at.timestamp(), "D"))
        e.add_field(name="Зашёл на сервер", value=fmt_ts(m.joined_at.timestamp(), "D") if m.joined_at else "—")
        if p:
            e.add_field(name="Профиль", value=f"{p['fio']} · {p['static']}", inline=False)
        e.add_field(name="Роли", value=" ".join(r.mention for r in m.roles[:0:-1][:15]) or "—", inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="serverinfo", description="Информация о сервере")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction):
        g = interaction.guild
        s = await self.bot.db.settings(g.id)
        e = make_embed(s, title=g.name)
        if g.icon:
            e.set_thumbnail(url=g.icon.url)
        e.add_field(name="Участников", value=str(g.member_count)).add_field(name="Каналов", value=str(len(g.channels)))
        e.add_field(name="Ролей", value=str(len(g.roles))).add_field(name="Создан", value=fmt_ts(g.created_at.timestamp(), "D"))
        e.add_field(name="Владелец", value=f"<@{g.owner_id}>")
        await interaction.response.send_message(embed=e)


async def setup(bot):
    await bot.add_cog(Fun(bot))
