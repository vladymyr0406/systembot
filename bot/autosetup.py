"""Автонастройка: бот сам создаёт категории и каналы, привязывает их к настройкам, формам и панелям,
публикует панели. Всё созданное запоминается — одной командой можно удалить и настроить вручную."""
import logging

import discord

from .cogs.panels import publish_panel
from .schema import SETTINGS_SCHEMA

log = logging.getLogger("rpbot.autosetup")
CREATED_KEY = "_setup_created"


def ch(emoji, name, topic="", bind=(), readonly=False):
    return {"name": f"{emoji}｜{name}", "topic": topic, "bind": list(bind), "readonly": readonly}


# bind: ("setting", ключ) | ("panel", ключ) | ("form", ключ формы) | ("ticketcat", ключ категории)
PLAN = [
    {"key": "info", "title": "Информация и граждане", "icon": "📢", "categories": [
        {"name": "ИНФОРМАЦИЯ", "private": False, "channels": [
            ch("📰", "новости", "Официальные новости", [("setting", "news_channel")], True),
            ch("👋", "приветствие", "Добро пожаловать", [("setting", "welcome_channel"), ("panel", "welcome")], True),
            ch("👤", "профиль", "Профиль и личный кабинет", [("panel", "profile"), ("panel", "hr")], True),
            ch("🏛️", "составы", "Актуальные составы", [("setting", "roster_channel")], True),
            ch("✉️", "обращения-граждан", "Вопросы, предложения, жалобы", [("panel", "citizens")], True),
        ]}]},
    {"key": "tickets", "title": "Поддержка и тикеты", "icon": "🎫", "categories": [
        {"name": "ПОДДЕРЖКА", "private": False, "channels": [
            ch("🎫", "поддержка", "Нужна помощь администрации — откройте тикет", [("panel", "tickets")], True)]},
        {"name": "ТИКЕТЫ", "private": True, "channels": [], "bind_category": ("ticketcat", "support")}]},
    {"key": "staff", "title": "Администрация и кадры (закрытые)", "icon": "🛡️", "categories": [
        {"name": "АДМИНИСТРАЦИЯ", "private": True, "channels": [
            ch("📨", "заявки", "Рассмотрение заявок и обращений", [("form", "profile"), ("form", "change"), ("form", "appeal")]),
            ch("🔎", "кадровый-аудит", "Кадровые решения: принятие, повышение, понижение, увольнение", [("form", "audit"), ("panel", "audit")]),
            ch("🗂️", "кадровые-приказы", "Приказы по /hr", [("setting", "hr_channel")]),
            ch("📄", "документы", "Выданные документы", [("setting", "docs_channel")]),
            ch("💰", "бюджет", "Отчёты по бюджету", [("setting", "budget_channel")]),
        ]}]},
    {"key": "logs", "title": "Логи (закрытые)", "icon": "📜", "categories": [
        {"name": "ЛОГИ", "private": True, "channels": [
            ch("🗑️", "логи-сообщений", "", [("setting", "log_messages")]),
            ch("📥", "логи-участников", "", [("setting", "log_members")]),
            ch("🎭", "логи-ролей", "", [("setting", "log_roles")]),
            ch("🔊", "логи-голосовых", "", [("setting", "log_voice")]),
            ch("🔨", "логи-модерации", "", [("setting", "log_mod")]),
            ch("📋", "логи-заявок", "", [("setting", "log_forms")]),
            ch("🎟️", "логи-тикетов", "", [("setting", "log_tickets")]),
            ch("💾", "бэкапы-бота", "Сюда бот складывает резервные копии базы", []),
        ]}]},
]
SECTION_KEYS = [s["key"] for s in PLAN]
_CHANNEL_SETTINGS = {f["key"] for g in SETTINGS_SCHEMA for f in g["fields"] if f["type"] == "channel"}


def plan_info() -> list[dict]:
    return [{"key": s["key"], "title": s["title"], "icon": s["icon"],
             "categories": [{"name": c["name"], "private": c["private"], "channels": [{"name": x["name"], "topic": x["topic"]} for x in c["channels"]]}
                            for c in s["categories"]]} for s in PLAN]


def _norm(n: str) -> str:
    return n.lower().replace(" ", "-")


def _overwrites(guild, staff, private, readonly):
    P = discord.PermissionOverwrite
    d = {guild.me: P(view_channel=True, send_messages=True, embed_links=True, attach_files=True, manage_messages=True,
                     create_public_threads=True, send_messages_in_threads=True, read_message_history=True)}
    if private:
        d[guild.default_role] = P(view_channel=False)
        for r in staff:
            d[r] = P(view_channel=True, send_messages=True, read_message_history=True)
    elif readonly:
        d[guild.default_role] = P(view_channel=True, send_messages=False, create_public_threads=False, add_reactions=True, read_message_history=True)
        for r in staff:
            d[r] = P(send_messages=True)
    return d


async def run(bot, guild: discord.Guild, keys, publish: bool = True, overwrite: bool = False) -> dict:
    db = bot.db
    await db.ensure_seed(guild.id)
    s = await db.settings(guild.id)
    staff = [r for r in (guild.get_role(int(i)) for i in list(s.get("staff_roles") or []) + list(s.get("hr_roles") or [])) if r]
    res = {"created": [], "reused": [], "bound": [], "published": [], "errors": [], "staff_missing": not staff}
    created = list(s.get(CREATED_KEY) or [])
    to_publish: list[str] = []

    async def bind(kind, key, ch_id):
        cur = await db.settings(guild.id)
        if kind == "setting":
            if overwrite or not cur.get(key):
                await db.set_setting(guild.id, key, str(ch_id))
                res["bound"].append(f"настройка «{key}»")
        else:
            item_kind, field = {"panel": ("panel", "channel"), "form": ("form", "review_channel"), "ticketcat": ("ticket", "category")}[kind]
            it = await db.item(guild.id, item_kind, key)
            if it is not None and (overwrite or not it.get(field)):
                it[field] = str(ch_id)
                await db.save_item(guild.id, item_kind, key, it)
                res["bound"].append(f"{item_kind} «{key}»")
                if kind == "panel":
                    to_publish.append(key)

    for sec in PLAN:
        if sec["key"] not in keys:
            continue
        for cat_spec in sec["categories"]:
            cat_name = cat_spec["name"]
            cat = discord.utils.find(lambda c: c.name.lower() == cat_name.lower(), guild.categories)
            try:
                if cat:
                    res["reused"].append(cat_name)
                else:
                    cat = await guild.create_category(cat_name, overwrites=_overwrites(guild, staff, cat_spec["private"], False), reason="Автонастройка бота")
                    created.append({"id": str(cat.id), "name": cat_name, "kind": "category"})
                    res["created"].append(cat_name)
                if cat_spec.get("bind_category"):
                    await bind(*cat_spec["bind_category"], cat.id)
            except discord.HTTPException as e:
                res["errors"].append(f"Категория «{cat_name}»: {'нет права «Управление каналами»' if isinstance(e, discord.Forbidden) else e.text}")
                continue
            for c in cat_spec["channels"]:
                exp = _norm(c["name"])
                existing = discord.utils.find(lambda x: x.name == exp, cat.text_channels)
                try:
                    if existing:
                        res["reused"].append(c["name"])
                        target = existing
                    else:
                        target = await guild.create_text_channel(
                            c["name"], category=cat, topic=c["topic"] or None, reason="Автонастройка бота",
                            overwrites=_overwrites(guild, staff, cat_spec["private"], c["readonly"]))
                        created.append({"id": str(target.id), "name": c["name"], "kind": "channel"})
                        res["created"].append(c["name"])
                except discord.HTTPException as e:
                    res["errors"].append(f"Канал «{c['name']}»: {'нет права «Управление каналами»' if isinstance(e, discord.Forbidden) else e.text}")
                    continue
                for kind, key in c["bind"]:
                    await bind(kind, key, target.id)
                if c["name"].endswith("бэкапы-бота"):
                    res["backup_channel_id"] = str(target.id)
    await db.set_setting(guild.id, CREATED_KEY, created)
    if publish:
        for key in dict.fromkeys(to_publish):
            msg, err = await publish_panel(bot, guild, key)
            (res["errors"].append(f"Панель «{key}»: {err}") if err else res["published"].append(key))
    return res


async def created_list(bot, guild: discord.Guild) -> list[dict]:
    s = await bot.db.settings(guild.id)
    return [{**c, "exists": guild.get_channel(int(c["id"])) is not None} for c in s.get(CREATED_KEY) or []]


async def undo(bot, guild: discord.Guild) -> dict:
    """Удаляет только то, что создал бот, и очищает привязки."""
    db = bot.db
    s = await db.settings(guild.id)
    created = list(s.get(CREATED_KEY) or [])
    gone, errors = set(), []
    for c in sorted(created, key=lambda c: c["kind"] == "category"):  # сначала каналы, потом категории
        obj = guild.get_channel(int(c["id"]))
        if obj:
            try:
                await obj.delete(reason="Откат автонастройки")
            except discord.HTTPException:
                errors.append(c["name"])
                continue
        gone.add(c["id"])
    for key in _CHANNEL_SETTINGS:
        if str(s.get(key)) in gone:
            await db.set_setting(guild.id, key, None)
    for kind, field in (("panel", "channel"), ("form", "review_channel"), ("ticket", "category")):
        for k, it in (await db.items(guild.id, kind)).items():
            changed = False
            if str(it.get(field)) in gone:
                it[field], changed = None, True
            if kind == "panel" and it.get("_msg") and it["_msg"].get("channel") in gone:
                it.pop("_msg")
                changed = True
            if changed:
                await db.save_item(guild.id, kind, k, it)
    await db.set_setting(guild.id, CREATED_KEY, [c for c in created if c["id"] not in gone])
    return {"deleted": len(gone), "errors": errors}


def format_result(res: dict) -> str:
    lines = []
    if res["created"]:
        lines.append(f"**Создано ({len(res['created'])}):** " + ", ".join(res["created"]))
    if res["reused"]:
        lines.append(f"**Уже были (не тронуты):** " + ", ".join(res["reused"]))
    if res["published"]:
        lines.append(f"**Опубликованы панели:** " + ", ".join(res["published"]))
    if res["bound"]:
        lines.append(f"**Привязано к настройкам:** {len(res['bound'])}")
    if res.get("backup_channel_id"):
        lines.append(f"💾 Для резервных копий впишите в переменную `BACKUP_CHANNEL_ID` значение `{res['backup_channel_id']}`.")
    if res["staff_missing"]:
        lines.append("ℹ️ Роли администрации не выбраны, поэтому закрытые каналы пока видят только администраторы сервера. "
                     "Выберите роли в настройках и дайте им доступ в правах этих каналов.")
    if res["errors"]:
        lines.append("⚠️ **Ошибки:**\n" + "\n".join("• " + e for e in res["errors"]))
    return "\n\n".join(lines) or "Ничего делать не пришлось — всё уже настроено."
