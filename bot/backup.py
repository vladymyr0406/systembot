"""Резервные копии базы данных в приватный канал Discord.
Бесплатные хостинги стирают диск при перезапуске: бот сам кладёт сжатую копию базы в канал
и при старте с пустым диском восстанавливает последнюю. Канал бот находит сам (по метке в описании)
или создаёт; переменная BACKUP_CHANNEL_ID нужна только если хотите указать канал вручную."""
import gzip
import io
import logging
import tempfile
import time
from pathlib import Path

import discord

from . import config

log = logging.getLogger("rpbot.backup")
PREFIX = "rpbot-db"
MARKER = "rpbot-backup"
KEEP = 30
CHANNEL_NAME = "💾｜бэкапы-бота"

_channel_id: int = config.BACKUP_CHANNEL_ID
STATE = {"blocked": False, "last": 0.0, "restored": False, "error": ""}


def channel_id() -> int:
    return _channel_id


async def find_channel(bot) -> int:
    """Ищет канал с резервными копиями: из BACKUP_CHANNEL_ID либо по метке в описании на любом сервере бота."""
    global _channel_id
    if _channel_id:
        return _channel_id
    try:
        async for g in bot.fetch_guilds(limit=100):
            for c in await g.fetch_channels():
                if isinstance(c, discord.TextChannel) and ((c.topic and MARKER in c.topic) or c.name == CHANNEL_NAME.lower()):
                    _channel_id = c.id
                    log.info("Найден канал резервных копий: %s (%s)", c.name, c.id)
                    return _channel_id
    except Exception:
        log.exception("Не удалось найти канал резервных копий")
    return 0


async def restore_if_missing(bot) -> bool:
    """Если файла базы нет (диск стёрт) — вернуть последнюю копию из Discord."""
    p = Path(config.DB_PATH)
    if p.exists() and p.stat().st_size > 4096:
        return False
    cid = await find_channel(bot)
    if not cid:
        log.info("Резервных копий нет (канал не найден) — начинаю с чистой базы")
        return False
    last_err = None
    for attempt in range(3):
        try:
            ch = await bot.fetch_channel(cid)
            async for m in ch.history(limit=300):
                if m.author.id != bot.user.id:
                    continue
                for a in m.attachments:
                    if a.filename.startswith(PREFIX):
                        raw = await a.read()
                        data = gzip.decompress(raw) if a.filename.endswith(".gz") else raw
                        p.parent.mkdir(parents=True, exist_ok=True)
                        for ext in ("-wal", "-shm"):
                            Path(str(p) + ext).unlink(missing_ok=True)
                        p.write_bytes(data)
                        STATE["restored"] = True
                        log.info("База восстановлена из резервной копии %s", a.filename)
                        return True
            log.info("В канале резервных копий пока пусто — начинаю с чистой базы")
            return False
        except Exception as e:
            last_err = e
            log.warning("Попытка восстановления %s не удалась: %s", attempt + 1, e)
    # Копии есть, но прочитать не смогли: не затираем их пустой базой
    STATE["blocked"] = True
    STATE["error"] = str(last_err)
    log.error("Не удалось восстановить базу из Discord — резервное копирование приостановлено, чтобы не затереть копии: %s", last_err)
    return False


async def ensure_channel(bot) -> int:
    """Если канала для копий нет — создаёт его (закрытый) на самом большом сервере бота."""
    global _channel_id
    if _channel_id or not bot.guilds:
        return _channel_id
    guild = max(bot.guilds, key=lambda g: g.member_count or 0)
    P = discord.PermissionOverwrite
    try:
        ch = await guild.create_text_channel(
            CHANNEL_NAME, topic=f"Резервные копии базы бота. Не удаляйте этот канал и не пишите в него. [{MARKER}]",
            overwrites={guild.default_role: P(view_channel=False),
                        guild.me: P(view_channel=True, send_messages=True, attach_files=True, manage_messages=True, read_message_history=True)},
            reason="Канал для резервных копий базы бота")
        _channel_id = ch.id
        log.info("Создан канал резервных копий: %s", ch.id)
    except discord.HTTPException as e:
        STATE["error"] = f"не удалось создать канал резервных копий на сервере «{guild.name}»: {e}"
        log.error(STATE["error"])
    return _channel_id


async def make_backup(bot, note: str = "авто") -> str | None:
    """Возвращает ссылку на сообщение с копией или None."""
    cid = channel_id()
    if not cid or STATE["blocked"]:
        return None
    tmp = Path(tempfile.mkdtemp()) / "snap.db"
    await bot.db.conn.execute("VACUUM INTO ?", (str(tmp),))
    gz = gzip.compress(tmp.read_bytes(), 6)
    tmp.unlink(missing_ok=True)
    if len(gz) > 24 * 1024 * 1024:
        log.error("Копия базы слишком большая для Discord (%s МБ)", len(gz) // 1048576)
        return None
    ch = await bot.fetch_channel(cid)
    name = f"{PREFIX}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.db.gz"
    msg = await ch.send(f"💾 Резервная копия базы ({note})", file=discord.File(io.BytesIO(gz), filename=name))
    STATE["last"] = time.time()
    n = 0
    async for m in ch.history(limit=300):
        if m.author.id == bot.user.id and any(a.filename.startswith(PREFIX) for a in m.attachments):
            n += 1
            if n > KEEP:
                try:
                    await m.delete()
                except discord.HTTPException:
                    pass
    return msg.jump_url
