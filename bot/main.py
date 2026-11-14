import asyncio
import contextlib
import logging
import sys

import discord
from discord.ext import commands

from . import config
from .backup import restore_if_missing
from .db import DB
from .schema import COMMAND_MODULE, MODULE_NAME

log = logging.getLogger("rpbot")

COGS = ["panels", "forms", "cabinet", "hr", "news", "moderation", "logs", "tickets", "music", "setting", "setup", "service", "fun"]


class ModuleDisabled(discord.app_commands.CheckFailure):
    pass


class RPTree(discord.app_commands.CommandTree):
    """Выключенный модуль: его команды отвечают «модуль отключён»."""
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        cmd = interaction.command
        if not cmd or not interaction.guild_id:
            return True
        root = cmd.root_parent.name if getattr(cmd, "root_parent", None) else cmd.name
        mod = COMMAND_MODULE.get(root)
        if not mod:
            return True
        s = await interaction.client.db.settings(interaction.guild_id)
        if s.get(mod, True):
            return True
        await interaction.response.send_message(f"Модуль «{MODULE_NAME[mod]}» отключён администрацией сервера.", ephemeral=True)
        raise ModuleDisabled()


class RPBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents,
                         help_command=None, tree_cls=RPTree, allowed_mentions=discord.AllowedMentions(everyone=False))
        self.db = DB(config.DB_PATH)
        self._web_server = None
        self._web_task = None

    async def setup_hook(self):
        await restore_if_missing(self)
        await self.db.connect()
        for name in COGS:
            try:
                await self.load_extension(f"bot.cogs.{name}")
            except Exception:
                log.exception("Не удалось загрузить модуль %s", name)
        if config.DEV_GUILD_ID:
            g = discord.Object(config.DEV_GUILD_ID)
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        else:
            await self.tree.sync()
        await self.start_web()

    async def start_web(self):
        import uvicorn
        from web.app import create_app

        class _Server(uvicorn.Server):
            def install_signal_handlers(self):
                pass

            @contextlib.contextmanager
            def capture_signals(self):
                yield

        app = create_app(self)
        cfg = uvicorn.Config(app, host="0.0.0.0", port=config.PORT, log_level="warning", access_log=False)
        self._web_server = _Server(cfg)
        self._web_task = asyncio.create_task(self._web_server.serve())
        log.info("Веб-панель: %s (порт %s)", config.BASE_URL, config.PORT)

    async def on_ready(self):
        for g in self.guilds:
            await self.db.ensure_seed(g.id)
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="за порядком • /help"))
        log.info("Запущен как %s, серверов: %s", self.user, len(self.guilds))

    async def on_guild_join(self, guild):
        await self.db.ensure_seed(guild.id)

    async def close(self):
        if self._web_server:
            self._web_server.should_exit = True
        await super().close()
        await self.db.close()

    async def on_tree_error(self, interaction: discord.Interaction, error):
        pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.TOKEN:
        print("Не задан DISCORD_TOKEN (см. .env.example)")
        sys.exit(1)
    import signal
    try:  # хостинг останавливает процесс сигналом SIGTERM — завершаемся штатно (с резервной копией)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    except (ValueError, AttributeError):
        pass
    bot = RPBot()

    @bot.tree.error
    async def _err(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, ModuleDisabled):
            return
        msg = "Недостаточно прав." if isinstance(error, discord.app_commands.CheckFailure) else f"Ошибка: {error}"
        if not isinstance(error, discord.app_commands.CheckFailure):
            log.exception("Ошибка команды", exc_info=error)
        with contextlib.suppress(discord.HTTPException):
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

    bot.run(config.TOKEN, log_handler=None)
