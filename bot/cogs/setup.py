"""/setup create — бот сам создаёт каналы и настраивает их; /setup undo — удаляет созданное, чтобы сделать по-своему."""
import discord
from discord import app_commands
from discord.ext import commands

from .. import autosetup
from ..util import has_any_role, is_admin, make_embed


class SetupView(discord.ui.View):
    def __init__(self, cog, owner_id):
        super().__init__(timeout=300)
        self.cog, self.owner_id = cog, owner_id
        self.keys = list(autosetup.SECTION_KEYS)
        info = autosetup.plan_info()
        sel = discord.ui.Select(placeholder="Что создать?", min_values=1, max_values=len(info), options=[
            discord.SelectOption(label=s["title"], value=s["key"], emoji=s["icon"], default=True) for s in info])

        async def on_sel(i: discord.Interaction):
            self.keys = list(sel.values)
            await i.response.defer()
        sel.callback = on_sel
        self.add_item(sel)

    async def interaction_check(self, interaction):
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="Создать каналы", style=discord.ButtonStyle.success, row=1)
    async def go(self, interaction: discord.Interaction, b):
        await interaction.response.edit_message(content="⏳ Создаю каналы, это займёт до минуты…", embed=None, view=None)
        res = await autosetup.run(self.cog.bot, interaction.guild, self.keys)
        s = await self.cog.bot.db.settings(interaction.guild_id)
        e = make_embed(s, title="✅ Автонастройка завершена", desc=autosetup.format_result(res)[:4000])
        e.set_footer(text="Не нравится — /setup undo удалит созданное, дальше можно создавать каналы самому и выбирать их в настройках")
        await interaction.edit_original_response(content=None, embed=e)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, b):
        await interaction.response.edit_message(content="Отменено. Ничего не создано.", embed=None, view=None)


class UndoView(discord.ui.View):
    def __init__(self, cog, owner_id):
        super().__init__(timeout=120)
        self.cog, self.owner_id = cog, owner_id

    async def interaction_check(self, interaction):
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="Да, удалить созданное ботом", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, b):
        await interaction.response.edit_message(content="⏳ Удаляю…", view=None)
        r = await autosetup.undo(self.cog.bot, interaction.guild)
        await interaction.edit_original_response(content=f"Удалено каналов и категорий: **{r['deleted']}**." + (f"\nНе удалось: {', '.join(r['errors'])}" if r["errors"] else ""))


class Setup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    setup = app_commands.Group(name="setup", description="Автоматическая настройка каналов", guild_only=True)

    async def _ok(self, interaction) -> bool:
        s = await self.bot.db.settings(interaction.guild_id)
        if is_admin(interaction.user) or has_any_role(interaction.user, s.get("admin_roles") or []):
            return True
        await interaction.response.send_message("Только для администраторов сервера.", ephemeral=True)
        return False

    @setup.command(name="create", description="Бот сам создаст нужные каналы, привяжет их и опубликует панели")
    async def create(self, interaction: discord.Interaction):
        if not await self._ok(interaction):
            return
        s = await self.bot.db.settings(interaction.guild_id)
        lines = []
        for sec in autosetup.plan_info():
            names = [c["name"] for cat in sec["categories"] for c in cat["channels"]]
            lines.append(f"{sec['icon']} **{sec['title']}**\n" + (", ".join(f"`{n}`" for n in names) or "категория для тикетов"))
        e = make_embed(s, title="Автонастройка каналов",
                       desc="Бот создаст категории и каналы, выберет их в настройках, формах и панелях и опубликует панели.\n"
                            "Уже выбранные вами каналы он **не заменит**, а одноимённые существующие использует повторно.\n\n" + "\n\n".join(lines))
        await interaction.response.send_message(embed=e, view=SetupView(self, interaction.user.id), ephemeral=True)

    @setup.command(name="undo", description="Удалить каналы, которые создал бот (чтобы настроить самому)")
    async def undo(self, interaction: discord.Interaction):
        if not await self._ok(interaction):
            return
        lst = await autosetup.created_list(self.bot, interaction.guild)
        if not lst:
            return await interaction.response.send_message("Бот ничего не создавал — удалять нечего.", ephemeral=True)
        names = "\n".join(f"• {c['name']}" + ("" if c["exists"] else " (уже удалён)") for c in lst)
        await interaction.response.send_message(f"Будут удалены **только созданные ботом** каналы и категории:\n{names[:1800]}\n\nЭто нельзя отменить.",
                                                view=UndoView(self, interaction.user.id), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Setup(bot))
