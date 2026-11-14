"""Резервные копии базы данных в приватный канал Discord.
Бесплатные хостинги часто стирают диск при перезапуске: бот сам кладёт сжатую копию базы в канал
и при старте с пустым диском восстанавливает последнюю."""
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
KEEP = 6


async def restore_if_missing(bot) -> bool:
    if not config.BACKUP_CHANNEL_ID:
        return False
    p = Path(config.DB_PATH)
    if p.exists() and p.stat().st_size > 4096:
        return False
    try:
        ch = await bot.fetch_channel(config.BACKUP_CHANNEL_ID)
        async for m in ch.history(limit=200):
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
                    log.info("База восстановлена из резервной копии %s", a.filename)
                    return True
        log.info("Резервных копий в канале пока нет — начинаю с чистой базы")
    except Exception:
        log.exception("Не удалось восстановить базу из Discord (проверьте BACKUP_CHANNEL_ID и права бота)")
    return False


async def make_backup(bot, note: str = "авто") -> str | None:
    """Возвращает ссылку на сообщение с копией или None."""
    if not config.BACKUP_CHANNEL_ID:
        return None
    tmp = Path(tempfile.mkdtemp()) / "snap.db"
    await bot.db.conn.execute("VACUUM INTO ?", (str(tmp),))
    gz = gzip.compress(tmp.read_bytes(), 6)
    tmp.unlink(missing_ok=True)
    if len(gz) > 24 * 1024 * 1024:
        log.error("Копия базы слишком большая для Discord (%s МБ)", len(gz) // 1048576)
        return None
    ch = await bot.fetch_channel(config.BACKUP_CHANNEL_ID)
    name = f"{PREFIX}-{time.strftime('%Y%m%d-%H%M', time.gmtime())}.db.gz"
    msg = await ch.send(f"💾 Резервная копия базы ({note})", file=discord.File(io.BytesIO(gz), filename=name))
    n = 0
    async for m in ch.history(limit=200):
        if m.author.id == bot.user.id and any(a.filename.startswith(PREFIX) for a in m.attachments):
            n += 1
            if n > KEEP:
                try:
                    await m.delete()
                except discord.HTTPException:
                    pass
    return msg.jump_url
