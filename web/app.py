"""Веб-админ-панель: вход через Discord OAuth2, JSON API и статический фронтенд."""
import json
import re
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

import discord
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer

from bot import config
from bot import autosetup
from bot.cogs.news import build_payload, post_news
from bot.cogs.panels import publish_panel
from bot.cogs.service import collect_status
from bot.schema import (ITEM_SCHEMAS, SETTINGS_SCHEMA, normalize_item, normalize_settings, valid_key)
from bot.util import has_any_role

STATIC = Path(__file__).parent / "static"
API = "https://discord.com/api/v10"
SESSION_TTL = 7 * 24 * 3600
ACTION_PREFIXES = [
    ["form", "Открыть форму"], ["ticket", "Открыть тикет"], ["cab", "Личный кабинет"],
    ["file", "Моё личное дело"], ["roster", "Показать составы"], ["url", "Ссылка (URL)"]]

# таблица -> (SQL, поле-идентификатор для удаления, как удалять)
TABLES = {
    "applications": ("SELECT id,user_id,form_key,data,status,reason,reviewer_id,created_at,decided_at FROM applications WHERE guild_id=?{extra} ORDER BY id DESC LIMIT 300", "id"),
    "profiles": ("SELECT user_id,fio,static,status,created_at,updated_at FROM profiles WHERE guild_id=? ORDER BY updated_at DESC LIMIT 500", "user_id"),
    "cases": ("SELECT id,user_id,mod_id,type,reason,duration,ts,active FROM cases WHERE guild_id=? ORDER BY id DESC LIMIT 300", "id"),
    "roster": ("SELECT r.user_id,r.position_key,r.since,p.fio,p.static FROM roster r LEFT JOIN profiles p ON p.guild_id=r.guild_id AND p.user_id=r.user_id WHERE r.guild_id=? ORDER BY r.since DESC", "user_id"),
    "hr_log": ("SELECT id,user_id,action,from_pos,to_pos,by_id,reason,ts FROM hr_log WHERE guild_id=? ORDER BY id DESC LIMIT 300", "id"),
    "docs": ("SELECT id,user_id,kind,title,author_id,ts FROM docs WHERE guild_id=? ORDER BY id DESC LIMIT 300", "id"),
    "budget": ("SELECT id,amount,kind,category,comment,author_id,ts FROM budget WHERE guild_id=? ORDER BY id DESC LIMIT 300", "id"),
    "tickets": ("SELECT id,channel_id,user_id,cat_key,status,created_at,closed_at FROM tickets WHERE guild_id=? ORDER BY id DESC LIMIT 300", "id"),
}
DELETE_SQL = {
    "applications": "DELETE FROM applications WHERE guild_id=? AND id=?",
    "profiles": "DELETE FROM profiles WHERE guild_id=? AND user_id=?",
    "cases": "UPDATE cases SET active=0 WHERE guild_id=? AND id=?",
    "roster": "DELETE FROM roster WHERE guild_id=? AND user_id=?",
    "hr_log": "DELETE FROM hr_log WHERE guild_id=? AND id=?",
    "docs": "DELETE FROM docs WHERE guild_id=? AND id=?",
    "budget": "DELETE FROM budget WHERE guild_id=? AND id=?",
    "tickets": "DELETE FROM tickets WHERE guild_id=? AND id=?",
}


def create_app(bot) -> FastAPI:
    app = FastAPI(title="RP Core Panel", docs_url=None, redoc_url=None, openapi_url=None)
    signer = URLSafeTimedSerializer(config.SECRET_KEY, salt="oauth-state")
    sessions: dict[str, dict] = {}
    app.state.sessions = sessions
    secure = config.BASE_URL.startswith("https")

    # ---------- авторизация ----------
    def current_session(request: Request) -> dict:
        sid = request.cookies.get("rp_sid")
        s = sessions.get(sid or "")
        if not s or s["exp"] < time.time():
            raise HTTPException(401, "Нужен вход")
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-csrf") != s["csrf"]:
            raise HTTPException(403, "CSRF")
        return s

    async def can_access(guild: discord.Guild, uid: int) -> bool:
        if uid in config.OWNER_IDS or uid == guild.owner_id:
            return True
        m = guild.get_member(uid)
        if m is None:
            try:
                m = await guild.fetch_member(uid)
            except discord.HTTPException:
                return False
        s = await bot.db.settings(guild.id)
        p = m.guild_permissions
        return p.administrator or p.manage_guild or has_any_role(m, s.get("admin_roles") or [])

    async def guild_ctx(gid: int, sess: dict = Depends(current_session)) -> discord.Guild:
        g = bot.get_guild(gid)
        if not g or not await can_access(g, int(sess["uid"])):
            raise HTTPException(403, "Нет доступа к серверу")
        return g

    # ---------- страницы ----------
    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    async def health():
        return {"ok": True, "guilds": len(bot.guilds)}

    @app.get("/login")
    async def login():
        if not config.CLIENT_ID or not config.CLIENT_SECRET:
            return JSONResponse({"error": "Не заданы DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET"}, 500)
        state = signer.dumps(secrets.token_urlsafe(8))
        url = "https://discord.com/oauth2/authorize?" + urlencode({
            "client_id": config.CLIENT_ID, "response_type": "code", "scope": "identify",
            "redirect_uri": f"{config.BASE_URL}/callback", "state": state, "prompt": "none"})
        r = RedirectResponse(url)
        r.set_cookie("rp_state", state, max_age=600, httponly=True, samesite="lax", secure=secure)
        return r

    @app.get("/callback")
    async def callback(request: Request, code: str = "", state: str = ""):
        try:
            signer.loads(state, max_age=600)
        except BadSignature:
            raise HTTPException(400, "Неверный state, попробуйте войти заново")
        if state != request.cookies.get("rp_state"):
            raise HTTPException(400, "State не совпал")
        async with httpx.AsyncClient(timeout=15) as c:
            tok = await c.post(f"{API}/oauth2/token", data={
                "client_id": config.CLIENT_ID, "client_secret": config.CLIENT_SECRET, "grant_type": "authorization_code",
                "code": code, "redirect_uri": f"{config.BASE_URL}/callback"})
            if tok.status_code != 200:
                raise HTTPException(400, "Discord отклонил вход (проверьте Redirect URI и секрет)")
            me = await c.get(f"{API}/users/@me", headers={"Authorization": f"Bearer {tok.json()['access_token']}"})
        u = me.json()
        sid = secrets.token_urlsafe(32)
        sessions[sid] = {"uid": u["id"], "name": u.get("global_name") or u["username"], "avatar": u.get("avatar"),
                         "csrf": secrets.token_urlsafe(16), "exp": time.time() + SESSION_TTL}
        r = RedirectResponse("/")
        r.set_cookie("rp_sid", sid, max_age=SESSION_TTL, httponly=True, samesite="lax", secure=secure)
        r.delete_cookie("rp_state")
        return r

    @app.get("/logout")
    async def logout(request: Request):
        sessions.pop(request.cookies.get("rp_sid", ""), None)
        r = RedirectResponse("/")
        r.delete_cookie("rp_sid")
        return r

    # ---------- API ----------
    @app.get("/api/me")
    async def me(sess: dict = Depends(current_session)):
        uid = int(sess["uid"])
        guilds = []
        for g in bot.guilds:
            if await can_access(g, uid):
                guilds.append({"id": str(g.id), "name": g.name, "icon": g.icon.url if g.icon else None, "members": g.member_count})
        av = f"https://cdn.discordapp.com/avatars/{sess['uid']}/{sess['avatar']}.png?size=64" if sess["avatar"] else None
        invite = f"https://discord.com/oauth2/authorize?client_id={config.CLIENT_ID}&scope=bot+applications.commands&permissions=8"
        return {"user": {"id": sess["uid"], "name": sess["name"], "avatar": av, "owner": uid in config.OWNER_IDS},
                "guilds": guilds, "csrf": sess["csrf"], "invite": invite, "bot": {"name": str(bot.user), "latency": (round(bot.latency * 1000) if bot.latency == bot.latency and bot.latency != float("inf") else 0)}}

    @app.get("/api/g/{gid}/meta")
    async def meta(gid: int, g: discord.Guild = Depends(guild_ctx)):
        await bot.db.ensure_seed(g.id)
        chans = []
        for c in sorted(g.channels, key=lambda c: (c.category.position if getattr(c, "category", None) else -1, c.position)):
            kind = ("category" if isinstance(c, discord.CategoryChannel) else "voice" if isinstance(c, (discord.VoiceChannel, discord.StageChannel))
                    else "text" if isinstance(c, (discord.TextChannel, discord.ForumChannel)) else None)
            if kind:
                chans.append({"id": str(c.id), "name": c.name, "type": kind, "parent": c.category.name if getattr(c, "category", None) else ""})
        roles = [{"id": str(r.id), "name": r.name, "color": f"#{r.color.value:06x}" if r.color.value else "", "managed": r.managed}
                 for r in sorted(g.roles, key=lambda r: -r.position) if not r.is_default()]
        forms = await bot.db.items(g.id, "form")
        tickets = await bot.db.items(g.id, "ticket")
        actions = [{"value": f"form:{k}", "label": f"Форма: {v.get('title', k)}"} for k, v in forms.items()]
        actions += [{"value": f"ticket:{k}", "label": f"Тикет: {v.get('name', k)}"} for k, v in tickets.items()]
        actions += [{"value": "cab:home", "label": "Личный кабинет"}, {"value": "file:me", "label": "Моё личное дело"},
                    {"value": "roster:show", "label": "Показать составы"}]
        return {"forms": [{"value": k, "label": v.get("title") or k} for k, v in forms.items()],
                "tickets": [{"value": k, "label": v.get("name") or k} for k, v in tickets.items()],
                "channels": chans, "roles": roles, "settings_schema": SETTINGS_SCHEMA, "item_schemas": ITEM_SCHEMAS,
                "actions": actions, "guild": {"id": str(g.id), "name": g.name, "icon": g.icon.url if g.icon else None}}

    @app.get("/api/g/{gid}/overview")
    async def overview(gid: int, g: discord.Guild = Depends(guild_ctx)):
        db, k = bot.db, str(g.id)
        q = lambda sql: db.one(sql, (k,))
        s = await db.settings(g.id)
        stats = {
            "members": g.member_count,
            "pending": (await q("SELECT COUNT(*) c FROM applications WHERE guild_id=? AND status='pending'"))["c"],
            "profiles": (await q("SELECT COUNT(*) c FROM profiles WHERE guild_id=?"))["c"],
            "roster": (await q("SELECT COUNT(*) c FROM roster WHERE guild_id=?"))["c"],
            "tickets": (await q("SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND status='open'"))["c"],
            "warns": (await q("SELECT COUNT(*) c FROM cases WHERE guild_id=? AND type='warn' AND active=1"))["c"],
        }
        forms = await db.items(g.id, "form")
        todo = []
        if not s.get("staff_roles"):
            todo.append(["Укажите роли администрации", "general"])
        if not any(f.get("review_channel") for f in forms.values()) and not s.get("log_forms"):
            todo.append(["Не задан канал рассмотрения заявок (в формах или в логах)", "form"])
        if not s.get("log_mod"):
            todo.append(["Не выбран канал логов модерации", "logs"])
        panels = await db.items(g.id, "panel")
        if not any(p.get("_msg") for p in panels.values()):
            todo.append(["Ни одна панель ещё не опубликована", "panel"])
        return {"stats": stats, "todo": todo, "status": await collect_status(bot, g)}

    @app.get("/api/g/{gid}/settings")
    async def get_settings(gid: int, g: discord.Guild = Depends(guild_ctx)):
        s = await bot.db.settings(g.id)
        return {k: v for k, v in s.items() if not k.startswith("_")}

    @app.put("/api/g/{gid}/settings")
    async def put_settings(gid: int, request: Request, g: discord.Guild = Depends(guild_ctx)):
        patch = normalize_settings(await request.json())
        for key in ("fio_regex", "static_regex"):
            if key in patch:
                try:
                    re.compile(patch[key])
                except re.error as e:
                    raise HTTPException(400, f"Неверное регулярное выражение ({key}): {e}")
        for k, v in patch.items():
            await bot.db.set_setting(g.id, k, v)
        cog = bot.get_cog("HR")
        if cog and "roster_channel" in patch:
            await cog.refresh_roster(g)
        return {"ok": True, "saved": list(patch)}

    # --- элементы: формы, панели, должности, категории тикетов ---
    def _kind(kind: str):
        if kind not in ITEM_SCHEMAS:
            raise HTTPException(404, "Неизвестный раздел")

    def _post(kind: str, data: dict) -> dict:
        if kind == "form":
            seen, fields = set(), []
            for i, f in enumerate(data["fields"][:5]):
                key = re.sub(r"[^a-z0-9_]", "", str(f["key"]).lower()) or f"field{i + 1}"
                while key in seen:
                    key += "_"
                seen.add(key)
                f["key"] = key
                f["max_length"] = max(1, min(int(f["max_length"] or 100), 4000))
                f["label"] = f["label"][:45] or "Поле"
                fields.append(f)
            data["fields"] = fields
        if kind == "position":
            data["rank"], data["slots"] = int(data["rank"] or 0), max(0, int(data["slots"] or 0))
        return data

    @app.get("/api/g/{gid}/items/{kind}")
    async def list_items(gid: int, kind: str, g: discord.Guild = Depends(guild_ctx)):
        _kind(kind)
        await bot.db.ensure_seed(g.id)
        return await bot.db.items(g.id, kind)

    @app.put("/api/g/{gid}/items/{kind}/{key}")
    async def put_item(gid: int, kind: str, key: str, request: Request, g: discord.Guild = Depends(guild_ctx)):
        _kind(kind)
        if not valid_key(key):
            raise HTTPException(400, "Ключ: латиница, цифры, _ и -, до 32 символов")
        old = await bot.db.item(g.id, kind, key)
        data = _post(kind, normalize_item(kind, await request.json(), old))
        await bot.db.save_item(g.id, kind, key, data)
        if kind == "position":
            cog = bot.get_cog("HR")
            if cog:
                await cog.refresh_roster(g)
        if kind == "panel" and data.get("_msg"):  # панель уже в Discord — обновляем её сразу
            _, err = await publish_panel(bot, g, key)
            data = await bot.db.item(g.id, kind, key) or data
            if err:
                data["_publish_error"] = err
        return data

    @app.delete("/api/g/{gid}/items/{kind}/{key}")
    async def del_item(gid: int, kind: str, key: str, delete_message: bool = False, g: discord.Guild = Depends(guild_ctx)):
        _kind(kind)
        old = await bot.db.item(g.id, kind, key)
        if kind == "panel" and delete_message and old and old.get("_msg"):
            ch = g.get_channel(int(old["_msg"]["channel"]))
            try:
                if ch:
                    await (await ch.fetch_message(int(old["_msg"]["id"]))).delete()
            except discord.HTTPException:
                pass
        await bot.db.del_item(g.id, kind, key)
        return {"ok": True}

    @app.post("/api/g/{gid}/panels/{key}/publish")
    async def publish(gid: int, key: str, g: discord.Guild = Depends(guild_ctx)):
        msg, err = await publish_panel(bot, g, key)
        if err:
            raise HTTPException(400, err)
        return {"ok": True, "url": msg.jump_url}

    # --- данные ---
    async def _enrich(g, rows):
        for r in rows:
            for k in ("user_id", "mod_id", "reviewer_id", "author_id", "by_id"):
                if r.get(k):
                    m = g.get_member(int(r[k]))
                    r[k.replace("_id", "_name")] = (m.display_name if m else None)
            if isinstance(r.get("data"), str):
                try:
                    r["data"] = json.loads(r["data"])
                except ValueError:
                    pass
        return rows

    @app.get("/api/g/{gid}/data/{table}")
    async def data(gid: int, table: str, status: str = "", g: discord.Guild = Depends(guild_ctx)):
        if table not in TABLES:
            raise HTTPException(404)
        sql, _ = TABLES[table]
        args = [str(g.id)]
        extra = ""
        if table == "applications" and status in ("pending", "approved", "denied"):
            extra, args = " AND status=?", args + [status]
        rows = await bot.db.all(sql.format(extra=extra) if "{extra}" in sql else sql, tuple(args))
        return await _enrich(g, rows)

    @app.delete("/api/g/{gid}/data/{table}/{rid}")
    async def data_delete(gid: int, table: str, rid: str, g: discord.Guild = Depends(guild_ctx)):
        if table not in DELETE_SQL:
            raise HTTPException(404)
        await bot.db.run(DELETE_SQL[table], (str(g.id), rid))
        if table == "roster" and bot.get_cog("HR"):
            await bot.get_cog("HR").refresh_roster(g)
        return {"ok": True}

    @app.put("/api/g/{gid}/data/profiles/{uid}")
    async def profile_put(gid: int, uid: str, request: Request, g: discord.Guild = Depends(guild_ctx)):
        b = await request.json()
        fio, static = " ".join(str(b.get("fio", "")).split())[:100], str(b.get("static", "")).strip()[:20]
        if not uid.isdigit() or not fio or not static:
            raise HTTPException(400, "Нужны ФИО и Статик")
        await bot.db.upsert_profile(g.id, uid, fio, static)
        return {"ok": True}

    # --- новости ---
    @app.get("/api/g/{gid}/news")
    async def news_list(gid: int, g: discord.Guild = Depends(guild_ctx)):
        rows = await bot.db.all("SELECT id,channel_id,message_id,title,author_id,ts FROM news WHERE guild_id=? ORDER BY id DESC LIMIT 100", (str(g.id),))
        for r in rows:
            r["jump"] = f"https://discord.com/channels/{g.id}/{r['channel_id']}/{r['message_id']}"
        return await _enrich(g, rows)

    @app.post("/api/g/{gid}/news")
    async def news_post(gid: int, request: Request, sess: dict = Depends(current_session), g: discord.Guild = Depends(guild_ctx)):
        d = build_payload(await request.json())
        s = await bot.db.settings(g.id)
        ch = g.get_channel(int(d["channel"] or s.get("news_channel") or 0))
        if not ch or not hasattr(ch, "send"):
            raise HTTPException(400, "Выберите канал для публикации")
        if not d["title"] and not d["description"]:
            raise HTTPException(400, "Заполните заголовок или текст")
        try:
            msg = await post_news(bot, g, ch, int(sess["uid"]), d)
        except discord.Forbidden:
            raise HTTPException(400, f"У бота нет прав писать в #{ch.name}")
        except discord.HTTPException as e:
            raise HTTPException(400, f"Discord отклонил сообщение: {e.text[:200]}")
        return {"ok": True, "url": msg.jump_url}

    @app.delete("/api/g/{gid}/news/{nid}")
    async def news_delete(gid: int, nid: int, delete_message: bool = True, g: discord.Guild = Depends(guild_ctx)):
        row = await bot.db.one("SELECT * FROM news WHERE id=? AND guild_id=?", (nid, str(g.id)))
        if not row:
            raise HTTPException(404)
        if delete_message:
            ch = g.get_channel(int(row["channel_id"]))
            try:
                if ch:
                    await (await ch.fetch_message(int(row["message_id"]))).delete()
            except discord.HTTPException:
                pass
        await bot.db.run("DELETE FROM news WHERE id=?", (nid,))
        return {"ok": True}

    # --- автонастройка каналов ---
    @app.get("/api/g/{gid}/setup")
    async def setup_info(gid: int, g: discord.Guild = Depends(guild_ctx)):
        return {"sections": autosetup.plan_info(), "created": await autosetup.created_list(bot, g)}

    @app.post("/api/g/{gid}/setup")
    async def setup_run(gid: int, request: Request, g: discord.Guild = Depends(guild_ctx)):
        b = await request.json()
        keys = [k for k in b.get("sections") or [] if k in autosetup.SECTION_KEYS]
        if not keys:
            raise HTTPException(400, "Выберите хотя бы один раздел")
        res = await autosetup.run(bot, g, keys, publish=bool(b.get("publish", True)), overwrite=bool(b.get("overwrite", False)))
        return {**res, "text": autosetup.format_result(res)}

    @app.post("/api/g/{gid}/setup/undo")
    async def setup_undo(gid: int, g: discord.Guild = Depends(guild_ctx)):
        return await autosetup.undo(bot, g)

    # --- экспорт / импорт ---
    @app.get("/api/g/{gid}/export")
    async def export(gid: int, g: discord.Guild = Depends(guild_ctx)):
        s = await bot.db.settings(g.id)
        items = {k: {key: {a: b for a, b in d.items() if not a.startswith("_")} for key, d in (await bot.db.items(g.id, k)).items()}
                 for k in ITEM_SCHEMAS}
        body = {"version": 1, "settings": {k: v for k, v in s.items() if not k.startswith("_")}, "items": items}
        return JSONResponse(body, headers={"Content-Disposition": f'attachment; filename="rpbot-{g.id}.json"'})

    @app.post("/api/g/{gid}/import")
    async def import_(gid: int, request: Request, replace: bool = False, g: discord.Guild = Depends(guild_ctx)):
        body = await request.json()
        for k, v in normalize_settings(body.get("settings") or {}).items():
            await bot.db.set_setting(g.id, k, v)
        for kind, items in (body.get("items") or {}).items():
            if kind not in ITEM_SCHEMAS or not isinstance(items, dict):
                continue
            if replace:
                for key in list(await bot.db.items(g.id, kind)):
                    if key not in items:
                        await bot.db.del_item(g.id, kind, key)
            for key, d in items.items():
                if valid_key(key) and isinstance(d, dict):
                    old = await bot.db.item(g.id, kind, key)
                    await bot.db.save_item(g.id, kind, key, _post(kind, normalize_item(kind, d, old)))
        return {"ok": True}

    @app.get("/api/backup")
    async def backup(sess: dict = Depends(current_session)):
        if int(sess["uid"]) not in config.OWNER_IDS:
            raise HTTPException(403, "Только владелец бота (OWNER_IDS)")
        tmp = Path(tempfile.mkdtemp()) / "bot-backup.db"
        await bot.db.conn.execute("VACUUM INTO ?", (str(tmp),))
        return FileResponse(tmp, filename="bot-backup.db")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
