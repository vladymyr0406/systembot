import json
import time
import aiosqlite
from pathlib import Path

from .schema import settings_defaults, seed_items, AUDIT_FORM, AUDIT_PANEL_TEXT, OLD_AUDIT_PANEL_TEXT

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(guild_id TEXT, key TEXT, value TEXT, PRIMARY KEY(guild_id,key));
CREATE TABLE IF NOT EXISTS items(guild_id TEXT, kind TEXT, key TEXT, data TEXT, PRIMARY KEY(guild_id,kind,key));
CREATE TABLE IF NOT EXISTS profiles(guild_id TEXT, user_id TEXT, fio TEXT, static TEXT, status TEXT DEFAULT 'active',
    created_at INTEGER, updated_at INTEGER, PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT, user_id TEXT, form_key TEXT,
    data TEXT, status TEXT DEFAULT 'pending', reason TEXT, reviewer_id TEXT, channel_id TEXT, message_id TEXT, thread_id TEXT,
    created_at INTEGER, decided_at INTEGER);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT, user_id TEXT, title TEXT, body TEXT,
    created_at INTEGER);
CREATE TABLE IF NOT EXISTS roster(guild_id TEXT, user_id TEXT, position_key TEXT, since INTEGER, PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS hr_log(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT, user_id TEXT, action TEXT, from_pos TEXT,
    to_pos TEXT, by_id TEXT, reason TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT, user_id TEXT, kind TEXT, title TEXT,
    content TEXT, author_id TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS budget(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT, amount REAL, kind TEXT, category TEXT,
    comment TEXT, author_id TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS cases(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT, user_id TEXT, mod_id TEXT, type TEXT,
    reason TEXT, duration INTEGER, ts INTEGER, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT, channel_id TEXT, user_id TEXT,
    cat_key TEXT, status TEXT DEFAULT 'open', created_at INTEGER, closed_at INTEGER);
CREATE TABLE IF NOT EXISTS news(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT, channel_id TEXT, message_id TEXT, title TEXT,
    body TEXT, author_id TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS seeded(guild_id TEXT PRIMARY KEY);
CREATE INDEX IF NOT EXISTS idx_app_user ON applications(guild_id,user_id);
CREATE INDEX IF NOT EXISTS idx_cases_user ON cases(guild_id,user_id);
"""


def now() -> int:
    return int(time.time())


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self._cache: dict[str, dict] = {}

    async def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()
        await self.migrate()

    async def migrate(self):
        """Обновление данных старых версий: «Кадровый аудит» из заявки превращается в сообщение с выбором решения."""
        for r in await self.all("SELECT guild_id,data FROM items WHERE kind='form' AND key='audit'"):
            d = json.loads(r["data"])
            if "mode" in d:
                continue
            new = json.loads(json.dumps(AUDIT_FORM))
            for k in ("review_channel", "ping_roles", "enabled"):
                if k in d:
                    new[k] = d[k]
            await self.save_item(r["guild_id"], "form", "audit", new)
        for r in await self.all("SELECT guild_id,data FROM items WHERE kind='panel' AND key='audit'"):
            d = json.loads(r["data"])
            if d.get("description") == OLD_AUDIT_PANEL_TEXT:
                d["description"] = AUDIT_PANEL_TEXT
                for b in d.get("buttons", []):
                    if b.get("action") == "form:audit":
                        b["label"] = "Отправить кадровый аудит"
                await self.save_item(r["guild_id"], "panel", "audit", d)

    async def close(self):
        if self.conn:
            await self.conn.close()

    async def run(self, q, args=()):
        cur = await self.conn.execute(q, args)
        await self.conn.commit()
        return cur.lastrowid

    async def one(self, q, args=()):
        cur = await self.conn.execute(q, args)
        row = await cur.fetchone()
        return dict(row) if row else None

    async def all(self, q, args=()):
        cur = await self.conn.execute(q, args)
        return [dict(r) for r in await cur.fetchall()]

    # ---------- настройки ----------
    async def settings(self, gid) -> dict:
        gid = str(gid)
        if gid in self._cache:
            return self._cache[gid]
        out = settings_defaults()
        for r in await self.all("SELECT key,value FROM settings WHERE guild_id=?", (gid,)):
            try:
                out[r["key"]] = json.loads(r["value"])
            except Exception:
                pass
        self._cache[gid] = out
        return out

    async def set_setting(self, gid, key, value):
        gid = str(gid)
        await self.run("INSERT INTO settings(guild_id,key,value) VALUES(?,?,?) ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value",
                       (gid, key, json.dumps(value, ensure_ascii=False)))
        self._cache.pop(gid, None)

    # ---------- элементы (формы, панели, должности, категории тикетов) ----------
    async def items(self, gid, kind) -> dict:
        rows = await self.all("SELECT key,data FROM items WHERE guild_id=? AND kind=? ORDER BY rowid", (str(gid), kind))
        return {r["key"]: json.loads(r["data"]) for r in rows}

    async def item(self, gid, kind, key):
        r = await self.one("SELECT data FROM items WHERE guild_id=? AND kind=? AND key=?", (str(gid), kind, key))
        return json.loads(r["data"]) if r else None

    async def save_item(self, gid, kind, key, data):
        await self.run("INSERT INTO items(guild_id,kind,key,data) VALUES(?,?,?,?) ON CONFLICT(guild_id,kind,key) DO UPDATE SET data=excluded.data",
                       (str(gid), kind, key, json.dumps(data, ensure_ascii=False)))

    async def del_item(self, gid, kind, key):
        await self.run("DELETE FROM items WHERE guild_id=? AND kind=? AND key=?", (str(gid), kind, key))

    async def ensure_seed(self, gid):
        gid = str(gid)
        if await self.one("SELECT 1 FROM seeded WHERE guild_id=?", (gid,)):
            return
        for kind, items in seed_items().items():
            for key, data in items.items():
                await self.save_item(gid, kind, key, data)
        await self.run("INSERT INTO seeded(guild_id) VALUES(?)", (gid,))

    # ---------- профили / уведомления ----------
    async def profile(self, gid, uid):
        return await self.one("SELECT * FROM profiles WHERE guild_id=? AND user_id=?", (str(gid), str(uid)))

    async def upsert_profile(self, gid, uid, fio, static, status="active"):
        t = now()
        await self.run("""INSERT INTO profiles(guild_id,user_id,fio,static,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(guild_id,user_id) DO UPDATE SET fio=excluded.fio,static=excluded.static,status=excluded.status,updated_at=excluded.updated_at""",
                       (str(gid), str(uid), fio, static, status, t, t))

    async def notify(self, gid, uid, title, body):
        await self.run("INSERT INTO notifications(guild_id,user_id,title,body,created_at) VALUES(?,?,?,?,?)",
                       (str(gid), str(uid), title, body, now()))
