"""Схемы настроек и сущностей. По ним строится и веб-панель, и меню /setting.
Типы полей: text, textarea, bool, number, color, channel, role, roles, select, list.
Все ID хранятся СТРОКАМИ (snowflake не помещается в JS-число)."""

def F(key, label, type="text", default=None, **kw):
    d = {"key": key, "label": label, "type": type, "default": default}
    d.update(kw)
    return d


SETTINGS_SCHEMA = [
    {"group": "general", "title": "Основное", "icon": "⚙️", "fields": [
        F("bot_name", "Название проекта (в подписях)", "text", "RP Core"),
        F("brand_color", "Фирменный цвет", "color", "#B91C1C"),
        F("footer", "Подпись под сообщениями", "text", "RP Core"),
        F("staff_roles", "Роли администрации (доступ к командам, рассмотрение заявок)", "roles", []),
        F("admin_roles", "Роли с доступом к веб-панели (кроме админов сервера)", "roles", []),
        F("hr_roles", "Роли кадровиков (составы, личные дела, документы)", "roles", []),
    ]},
    {"group": "modules", "title": "Модули", "icon": "🧰", "fields": [
        F("mod_cabinet", "👤 Профили, заявки, личный кабинет", "bool", True),
        F("mod_hr", "🗂️ Кадры, составы, личные дела, документы", "bool", True),
        F("mod_budget", "💰 Бюджет", "bool", True),
        F("mod_moderation", "🛡️ Модерация (баны, муты, предупреждения)", "bool", True),
        F("mod_tickets", "🎫 Тикеты", "bool", True),
        F("mod_news", "📰 Новости", "bool", True),
        F("music_enabled", "🎵 Музыка", "bool", True),
        F("mod_fun", "🎲 Развлечения (кубики, аватар, инфо)", "bool", True),
    ]},
    {"group": "news", "title": "Новости", "icon": "📰", "fields": [
        F("news_channel", "Канал новостей по умолчанию", "channel", None),
        F("news_roles", "Роли, которым разрешено публиковать новости (кроме администрации)", "roles", []),
    ]},
    {"group": "profile", "title": "Профиль", "icon": "👤", "fields": [
        F("fio_regex", "Шаблон ФИО (regex)", "text", r"^[А-ЯЁA-Z][а-яёa-z\-]+(\s[А-ЯЁA-Z][а-яёa-z\-]+){1,2}$"),
        F("fio_hint", "Подсказка по ФИО при ошибке", "text", "Формат: Фамилия Имя Отчество, каждое слово с заглавной буквы."),
        F("static_regex", "Шаблон Статика (regex)", "text", r"^\d{1,8}$"),
        F("nickname_enabled", "Менять никнейм при одобрении профиля", "bool", False),
        F("nickname_format", "Формат никнейма", "text", "{fio} | {static}"),
        F("profile_unique_static", "Запретить одинаковый Статик у разных людей", "bool", True),
    ]},
    {"group": "hr", "title": "Составы и кадры", "icon": "🧑‍💼", "fields": [
        F("roster_channel", "Канал с автообновляемым списком составов", "channel", None),
        F("hr_channel", "Канал кадровых приказов (приём/перевод/увольнение)", "channel", None),
        F("hr_dm", "Уведомлять сотрудника в ЛС о кадровых решениях", "bool", True),
        F("docs_channel", "Канал для выданных документов (приказы, справки)", "channel", None),
        F("doc_kinds", "Виды документов (через запятую)", "text", "Приказ, Выговор, Справка, Характеристика, Благодарность"),
    ]},
    {"group": "budget", "title": "Бюджет", "icon": "💰", "fields": [
        F("budget_roles", "Роли, управляющие бюджетом", "roles", []),
        F("budget_channel", "Канал отчётов по бюджету", "channel", None),
        F("currency", "Валюта", "text", "₽"),
    ]},
    {"group": "logs", "title": "Логи", "icon": "📜", "fields": [
        F("log_messages", "Сообщения (удаление / правки)", "channel", None),
        F("log_members", "Участники (вход / выход / ники)", "channel", None),
        F("log_roles", "Выдача и снятие ролей", "channel", None),
        F("log_voice", "Голосовые каналы", "channel", None),
        F("log_mod", "Модерация (баны, муты, варны)", "channel", None),
        F("log_forms", "Заявки и обращения", "channel", None),
        F("log_tickets", "Тикеты и транскрипты", "channel", None),
    ]},
    {"group": "moderation", "title": "Модерация", "icon": "🛡️", "fields": [
        F("warn_limit", "Варнов до наказания (0 = выкл.)", "number", 3),
        F("warn_limit_action", "Наказание при лимите варнов", "select", "timeout",
          options=[["none", "Ничего"], ["timeout", "Тайм-аут 24ч"], ["kick", "Кик"], ["ban", "Бан"]]),
        F("automod_enabled", "Автомодерация включена", "bool", False),
        F("anti_invite", "Блокировать инвайты на другие серверы", "bool", True),
        F("anti_links", "Блокировать любые ссылки", "bool", False),
        F("link_whitelist", "Разрешённые домены (через запятую)", "text", "youtube.com, youtu.be, discord.com, tenor.com"),
        F("anti_caps", "Блокировать КАПС", "bool", False),
        F("caps_percent", "Порог капса, %", "number", 70),
        F("anti_spam", "Антиспам", "bool", True),
        F("spam_count", "Сообщений для спама", "number", 6),
        F("spam_seconds", "За сколько секунд", "number", 6),
        F("bad_words", "Запрещённые слова (по одному в строке)", "textarea", ""),
        F("automod_action", "Действие автомодерации", "select", "delete",
          options=[["delete", "Удалить сообщение"], ["warn", "Удалить + варн"], ["timeout", "Удалить + тайм-аут"]]),
        F("automod_timeout_min", "Длительность тайм-аута, мин", "number", 10),
        F("exempt_roles", "Роли-исключения из автомодерации", "roles", []),
    ]},
    {"group": "welcome", "title": "Приветствие", "icon": "👋", "fields": [
        F("welcome_enabled", "Приветствие включено", "bool", False),
        F("welcome_channel", "Канал приветствий", "channel", None),
        F("welcome_text", "Текст ({user} {server} {count})", "textarea", "Добро пожаловать, {user}! Ты участник №{count} на сервере {server}."),
        F("autorole", "Роли при входе", "roles", []),
        F("goodbye_enabled", "Прощание включено", "bool", False),
        F("goodbye_channel", "Канал прощаний", "channel", None),
        F("goodbye_text", "Текст прощания", "textarea", "{user} покинул сервер."),
    ]},
    {"group": "tickets", "title": "Тикеты", "icon": "🎫", "fields": [
        F("ticket_limit", "Открытых тикетов на человека", "number", 1),
        F("ticket_transcripts", "Сохранять транскрипт при закрытии", "bool", True),
        F("ticket_dm_transcript", "Отправлять транскрипт автору в ЛС", "bool", True),
    ]},
    {"group": "music", "title": "Музыка", "icon": "🎵", "fields": [
        F("dj_roles", "DJ-роли (пусто = все)", "roles", []),
        F("music_channel", "Ограничить командами в канале", "channel", None),
        F("max_queue", "Максимум треков в очереди", "number", 100),
        F("default_volume", "Громкость по умолчанию, %", "number", 50),
        F("idle_leave_min", "Выходить из канала при простое, мин", "number", 3),
    ]},
]

STYLE_OPTIONS = [["primary", "Синяя"], ["secondary", "Серая"], ["success", "Зелёная"], ["danger", "Красная"]]

ITEM_SCHEMAS = {
    "form": {
        "title": "Формы заявок", "icon": "📝", "single": "форму",
        "key_hint": "латиницей, напр. appeal",
        "fields": [
            F("title", "Название формы", "text", "Новая форма"),
            F("kind", "Тип", "select", "custom", options=[
                ["profile", "Создание профиля (ФИО + Статик)"],
                ["change", "Изменение профиля"],
                ["appeal", "Обращение граждан"],
                ["audit", "Кадровый аудит"],
                ["custom", "Произвольная заявка"]]),
            F("mode", "Режим", "select", "application", options=[
                ["application", "Заявка (рассматривается кнопками «Одобрить / Отклонить»)"],
                ["message", "Сообщение (сразу публикуется в канале, без рассмотрения)"]]),
            F("choices", "Варианты выбора перед формой (необязательно)", "list", [], max=25, item=[
                F("label", "Название", "text", "Вариант"),
                F("emoji", "Эмодзи", "text", ""),
                F("color", "Цвет сообщения", "color", ""),
            ]),
            F("enabled", "Включена", "bool", True),
            F("allowed_roles", "Кому доступна форма (пусто = всем)", "roles", []),
            F("require_profile", "Требовать заполненный профиль", "bool", False),
            F("review_channel", "Канал рассмотрения / публикации", "channel", None),
            F("reviewer_roles", "Роли, рассматривающие заявки", "roles", []),
            F("ping_roles", "Роли, которых пинговать при новой заявке", "roles", []),
            F("use_threads", "Создавать ветку обсуждения", "bool", True),
            F("give_roles", "Роли, выдаваемые при одобрении", "roles", []),
            F("remove_roles", "Роли, снимаемые при одобрении", "roles", []),
            F("accept_text", "Текст при одобрении", "textarea", "Ваша заявка одобрена."),
            F("deny_text", "Текст при отказе", "textarea", "Ваша заявка отклонена."),
            F("cooldown_min", "Пауза между заявками, мин", "number", 0),
            F("fields", "Поля формы (до 5)", "list", [], max=5, item=[
                F("key", "Ключ (латиница)", "text", "field"),
                F("label", "Название поля", "text", "Поле"),
                F("style", "Размер", "select", "short", options=[["short", "Строка"], ["long", "Абзац"]]),
                F("required", "Обязательное", "bool", True),
                F("placeholder", "Подсказка", "text", ""),
                F("max_length", "Макс. длина", "number", 100),
            ]),
        ],
    },
    "panel": {
        "title": "Панели (сообщения с кнопками)", "icon": "🧩", "single": "панель",
        "key_hint": "латиницей, напр. citizens",
        "fields": [
            F("channel", "Канал публикации", "channel", None),
            F("title", "Заголовок", "text", "Заголовок"),
            F("description", "Описание", "textarea", ""),
            F("color", "Цвет (пусто = фирменный)", "color", ""),
            F("image", "Картинка (URL)", "text", ""),
            F("thumbnail", "Миниатюра (URL)", "text", ""),
            F("footer", "Подпись (пусто = общая)", "text", ""),
            F("buttons", "Кнопки (до 25)", "list", [], max=25, item=[
                F("label", "Текст", "text", "Кнопка"),
                F("action", "Действие", "action", ""),
                F("style", "Цвет", "select", "primary", options=STYLE_OPTIONS),
                F("emoji", "Эмодзи", "text", ""),
            ]),
        ],
    },
    "position": {
        "title": "Должности и составы", "icon": "🏛️", "single": "должность",
        "key_hint": "латиницей, напр. cmd_chief",
        "fields": [
            F("name", "Название должности", "text", "Должность"),
            F("unit", "Состав / подразделение", "text", "Основной состав"),
            F("rank", "Ранг (больше = выше)", "number", 1),
            F("role", "Роль Discord", "role", None),
            F("extra_roles", "Доп. роли (выдаются вместе)", "roles", []),
            F("slots", "Количество мест (0 = без лимита)", "number", 0),
        ],
    },
    "ticket": {
        "title": "Категории тикетов", "icon": "🎫", "single": "категорию",
        "key_hint": "латиницей, напр. support",
        "fields": [
            F("name", "Название", "text", "Поддержка"),
            F("emoji", "Эмодзи", "text", "🎫"),
            F("enabled", "Включена", "bool", True),
            F("support_roles", "Роли поддержки (видят тикеты)", "roles", []),
            F("category", "Категория Discord для каналов", "channel", None),
            F("ping_support", "Пинговать поддержку", "bool", True),
            F("welcome", "Приветствие в тикете", "textarea", "Опишите вашу проблему, скоро с вами свяжутся."),
        ],
    },
}

ITEM_LABELS = {k: v["title"] for k, v in ITEM_SCHEMAS.items()}


def defaults_for(schema_fields):
    return {f["key"]: f.get("default") for f in schema_fields}


def settings_defaults():
    out = {}
    for g in SETTINGS_SCHEMA:
        out.update(defaults_for(g["fields"]))
    return out


def settings_types():
    return {f["key"]: f["type"] for g in SETTINGS_SCHEMA for f in g["fields"]}


def find_field(key):
    for g in SETTINGS_SCHEMA:
        for f in g["fields"]:
            if f["key"] == key:
                return g, f
    return None, None


# ---------- стартовые данные ----------
AUDIT_PANEL_TEXT = ("**Кадровый аудит**\n\nФиксация кадровых решений: принятие, повышение, понижение, увольнение.\n"
                    "Нажмите кнопку, выберите тип решения и заполните данные — сообщение сразу появится в канале аудита.")
OLD_AUDIT_PANEL_TEXT = "Обращение в кадровый аудит: проверка сотрудника, жалоба на действия кадров, запрос разбора ситуации.\n\nПрофиль должен быть заполнен."

AUDIT_FORM = {
    "title": "Кадровый аудит", "kind": "audit", "mode": "message", "enabled": True, "allowed_roles": [],
    "require_profile": True, "review_channel": None, "reviewer_roles": [], "ping_roles": [], "use_threads": False,
    "give_roles": [], "remove_roles": [], "cooldown_min": 0, "accept_text": "", "deny_text": "",
    "choices": [
        {"label": "Принятие", "emoji": "✅", "color": "#16A34A"},
        {"label": "Повышение", "emoji": "⬆️", "color": "#2563EB"},
        {"label": "Понижение", "emoji": "⬇️", "color": "#F59E0B"},
        {"label": "Увольнение", "emoji": "⛔", "color": "#DC2626"},
    ],
    "fields": [
        {"key": "target", "label": "Сотрудник (ФИО / Статик / @упоминание)", "style": "short", "required": True, "placeholder": "Иванов Иван Иванович | 123456", "max_length": 100},
        {"key": "position", "label": "Должность (было → стало)", "style": "short", "required": False, "placeholder": "Сотрудник → Старший сотрудник", "max_length": 120},
        {"key": "reason", "label": "Основание", "style": "long", "required": True, "placeholder": "Причина решения, номер приказа", "max_length": 1000},
    ],
}


def seed_items():
    forms = {
        "profile": {
            "title": "Создание профиля", "kind": "profile", "enabled": True, "require_profile": False,
            "review_channel": None, "reviewer_roles": [], "ping_roles": [], "use_threads": True,
            "give_roles": [], "remove_roles": [], "cooldown_min": 0,
            "accept_text": "Ваш профиль одобрен. Добро пожаловать!",
            "deny_text": "Заявка на профиль отклонена. Проверьте данные и подайте заново.",
            "fields": [
                {"key": "fio", "label": "ФИО", "style": "short", "required": True, "placeholder": "Иванов Иван Иванович", "max_length": 80},
                {"key": "static", "label": "Статик", "style": "short", "required": True, "placeholder": "123456", "max_length": 10},
            ]},
        "change": {
            "title": "Изменение данных профиля", "kind": "change", "enabled": True, "require_profile": True,
            "review_channel": None, "reviewer_roles": [], "ping_roles": [], "use_threads": True,
            "give_roles": [], "remove_roles": [], "cooldown_min": 10,
            "accept_text": "Изменения профиля применены.", "deny_text": "Изменение профиля отклонено.",
            "fields": [
                {"key": "fio", "label": "ФИО", "style": "short", "required": True, "placeholder": "", "max_length": 80},
                {"key": "static", "label": "Статик", "style": "short", "required": True, "placeholder": "", "max_length": 10},
                {"key": "reason", "label": "Причина изменения", "style": "long", "required": True, "placeholder": "", "max_length": 400},
            ]},
        "appeal": {
            "title": "Обращение", "kind": "appeal", "enabled": True, "require_profile": False,
            "review_channel": None, "reviewer_roles": [], "ping_roles": [], "use_threads": True,
            "give_roles": [], "remove_roles": [], "cooldown_min": 5,
            "accept_text": "Ваше обращение рассмотрено.", "deny_text": "Ваше обращение отклонено.",
            "fields": [
                {"key": "topic", "label": "Тема (вопрос / предложение / жалоба)", "style": "short", "required": True, "placeholder": "Вопрос", "max_length": 60},
                {"key": "text", "label": "Суть обращения", "style": "long", "required": True, "placeholder": "Опишите ситуацию подробно", "max_length": 1500},
                {"key": "proof", "label": "Ссылки / доказательства", "style": "long", "required": False, "placeholder": "", "max_length": 600},
            ]},
        "audit": AUDIT_FORM,
    }
    panels = {
        "welcome": {"channel": None, "title": "Добро пожаловать!", "color": "", "image": "", "thumbnail": "", "footer": "",
                    "description": "Для работы с системами сервера подайте заявку на создание единого профиля. ФИО и Статик будут использоваться заявками, кадрами, составами и документами после одобрения.",
                    "buttons": [{"label": "Заполнить данные", "action": "form:profile", "style": "primary", "emoji": "📝"}]},
        "profile": {"channel": None, "title": "👤 Профиль", "color": "", "image": "", "thumbnail": "", "footer": "",
                    "description": "Единый профиль используется составами, заявками, кадрами, бюджетом и другими модулями.\n\nВ **личном кабинете** доступны ваши данные, обращения и уведомления.\n\nЕсли профиль ещё не создан — нажмите **Заполнить профиль**. Изменение данных выполняется только после одобрения.",
                    "buttons": [{"label": "Личный кабинет", "action": "cab:home", "style": "secondary", "emoji": "👤"},
                                {"label": "Заполнить профиль", "action": "form:profile", "style": "primary", "emoji": "📝"},
                                {"label": "Запросить изменение", "action": "form:change", "style": "secondary", "emoji": "✏️"}]},
        "citizens": {"channel": None, "title": "Обращения граждан", "color": "", "image": "", "thumbnail": "", "footer": "",
                     "description": "**Официальный канал для вопросов и предложений**\n\nУважаемые граждане! Если у вас возник вопрос или есть идея — направьте официальное обращение.\n\n**Как это сделать:**\n• Нажмите кнопку ниже.\n• Заполните все поля формы.\n• Подробно опишите суть и отправьте.\n\n**Для быстрого рассмотрения:**\n• Укажите тему: вопрос / предложение / жалоба.\n• Опишите ситуацию подробно и по делу.\n• Приложите ссылки или доказательства.\n\n*Срок рассмотрения — до 3 рабочих дней.*",
                     "buttons": [{"label": "Создать обращение", "action": "form:appeal", "style": "primary", "emoji": "✉️"}]},
        "audit": {"channel": None, "title": "Кадровый аудит", "color": "", "image": "", "thumbnail": "", "footer": "",
                  "description": AUDIT_PANEL_TEXT,
                  "buttons": [{"label": "Отправить кадровый аудит", "action": "form:audit", "style": "danger", "emoji": "🔎"}]},
        "tickets": {"channel": None, "title": "🎫 Поддержка", "color": "", "image": "", "thumbnail": "", "footer": "",
                    "description": "Нужна помощь администрации? Выберите категорию — откроется приватный канал.",
                    "buttons": [{"label": "Открыть тикет", "action": "ticket:support", "style": "success", "emoji": "🎫"}]},
        "hr": {"channel": None, "title": "🗂️ Личное дело", "color": "", "image": "", "thumbnail": "", "footer": "",
               "description": "Просмотр личного дела: должность, история кадровых изменений, выданные документы.",
               "buttons": [{"label": "Моё личное дело", "action": "file:me", "style": "primary", "emoji": "🗂️"},
                           {"label": "Состав", "action": "roster:show", "style": "secondary", "emoji": "🏛️"}]},
    }
    tickets = {"support": {"name": "Поддержка", "emoji": "🎫", "enabled": True, "support_roles": [], "category": None,
                           "ping_support": True, "welcome": "Опишите вашу проблему, скоро с вами свяжутся."}}
    return {"form": forms, "panel": panels, "ticket": tickets, "position": {}}


# ---------- нормализация значений из веб-панели ----------
import re as _re

_HEX = _re.compile(r"^#?[0-9a-fA-F]{6}$")


def _sid(v):
    v = str(v).strip() if v is not None else ""
    return v if v.isdigit() else None


def normalize_value(field: dict, v):
    t = field["type"]
    if t in ("channel", "role"):
        return _sid(v)
    if t == "roles":
        return [s for s in (_sid(x) for x in (v or [])) if s]
    if t == "bool":
        return bool(v)
    if t == "number":
        try:
            n = float(v)
            return int(n) if n == int(n) else n
        except (TypeError, ValueError):
            return field.get("default") or 0
    if t == "color":
        v = str(v or "").strip()
        if not v:
            return ""
        return ("#" + v.lstrip("#")).upper() if _HEX.match(v) else (field.get("default") or "")
    if t == "select":
        allowed = {o[0] for o in field.get("options", [])}
        return v if v in allowed else field.get("default")
    if t == "list":
        out = []
        for row in (v or [])[: field.get("max", 25)]:
            if isinstance(row, dict):
                out.append({f["key"]: normalize_value(f, row.get(f["key"], f.get("default"))) for f in field["item"]})
        return out
    if t == "action":
        return str(v or "").strip()[:95]
    return str(v if v is not None else "")[:4000]


def normalize_item(kind: str, data: dict, old: dict | None = None) -> dict:
    fields = ITEM_SCHEMAS[kind]["fields"]
    out = {f["key"]: normalize_value(f, (data or {}).get(f["key"], f.get("default"))) for f in fields}
    for k, v in (old or {}).items():  # служебные поля (_msg и т.п.) сохраняем
        if k.startswith("_"):
            out[k] = v
    return out


def normalize_settings(patch: dict) -> dict:
    types = {f["key"]: f for g in SETTINGS_SCHEMA for f in g["fields"]}
    return {k: normalize_value(types[k], v) for k, v in (patch or {}).items() if k in types}


def valid_key(key: str) -> bool:
    return bool(_re.fullmatch(r"[a-z0-9_\-]{1,32}", key or ""))


# ---------- модули (вкл/выкл) ----------
MODULES = [("mod_cabinet", "Профили и заявки", "👤"), ("mod_hr", "Кадры и документы", "🗂️"), ("mod_budget", "Бюджет", "💰"),
           ("mod_moderation", "Модерация", "🛡️"), ("mod_tickets", "Тикеты", "🎫"), ("mod_news", "Новости", "📰"),
           ("music_enabled", "Музыка", "🎵"), ("mod_fun", "Развлечения", "🎲")]
MODULE_NAME = {k: n for k, n, _ in MODULES}
COMMAND_MODULE = {}
for _mod, _cmds in {
    "mod_cabinet": ["cabinet"],
    "mod_hr": ["hr", "doc", "roster", "file"],
    "mod_budget": ["budget"],
    "mod_moderation": ["ban", "unban", "kick", "timeout", "untimeout", "warn", "warns", "unwarn", "cases", "purge", "slowmode", "lock", "unlock"],
    "mod_tickets": ["ticket"],
    "mod_news": ["news"],
    "music_enabled": ["play", "pause", "skip", "stop", "leave", "queue", "nowplaying", "volume", "loop", "shuffle", "remove"],
    "mod_fun": ["roll", "coin", "choose", "avatar", "userinfo", "serverinfo"],
}.items():
    for _c in _cmds:
        COMMAND_MODULE[_c] = _mod
ACTION_MODULE = {"form": "mod_cabinet", "cab": "mod_cabinet", "file": "mod_hr", "roster": "mod_hr", "ticket": "mod_tickets"}
