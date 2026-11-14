import re
import discord
from discord import app_commands


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_color(v, default="#B91C1C") -> discord.Colour:
    v = (v or default).lstrip("#")
    try:
        return discord.Colour(int(v, 16))
    except ValueError:
        return discord.Colour(int(default.lstrip("#"), 16))


def make_embed(s: dict, title=None, desc=None, color=None, footer=True, **kw) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, colour=parse_color(color or s.get("brand_color")), **kw)
    if footer:
        e.set_footer(text=s.get("footer") or s.get("bot_name") or "RP Core")
    return e


def role_ids(s, key) -> set[int]:
    return {int(x) for x in (s.get(key) or []) if str(x).isdigit()}


def has_any_role(member: discord.Member, ids) -> bool:
    ids = {int(i) for i in ids if str(i).isdigit()}
    return any(r.id in ids for r in member.roles)


def is_admin(member: discord.Member) -> bool:
    p = member.guild_permissions
    return p.administrator or p.manage_guild


def is_staff(member: discord.Member, s: dict, extra_key: str | None = None) -> bool:
    if is_admin(member) or has_any_role(member, s.get("staff_roles") or []):
        return True
    return bool(extra_key and has_any_role(member, s.get(extra_key) or []))


def chan(guild: discord.Guild, v):
    i = to_int(v)
    return guild.get_channel(i) if i else None


async def send_log(guild: discord.Guild, s: dict, key: str, embed: discord.Embed, file=None):
    ch = chan(guild, s.get(key))
    if not ch or not hasattr(ch, "send"):
        return None
    try:
        return await ch.send(embed=embed, file=file, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        return None


def fmt_ts(ts, style="f") -> str:
    return f"<t:{int(ts)}:{style}>" if ts else "—"


def trim(text, n=1000) -> str:
    text = str(text or "—")
    return text if len(text) <= n else text[: n - 1] + "…"


def parse_duration(text: str) -> int | None:
    """'10m', '2h', '1d', '30s' -> секунды"""
    m = re.fullmatch(r"\s*(\d+)\s*([smhdwсмчд]?)\s*", text.lower())
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2) or "m"
    mult = {"s": 1, "с": 1, "m": 60, "м": 60, "h": 3600, "ч": 3600, "d": 86400, "д": 86400, "w": 604800}
    return n * mult[u]


def human_duration(sec: int) -> str:
    if sec % 86400 == 0:
        return f"{sec // 86400} д."
    if sec % 3600 == 0:
        return f"{sec // 3600} ч."
    if sec % 60 == 0:
        return f"{sec // 60} мин."
    return f"{sec} сек."


def staff_only(extra_key: str | None = None):
    """Декоратор slash-команд: только администрация (staff_roles / админы / extra_key)."""
    async def pred(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        s = await interaction.client.db.settings(interaction.guild_id)
        return is_staff(interaction.user, s, extra_key)
    return app_commands.check(pred)


def settings_admin_only():
    async def pred(interaction: discord.Interaction) -> bool:
        return bool(interaction.guild) and isinstance(interaction.user, discord.Member) and is_admin(interaction.user)
    return app_commands.check(pred)


def can_manage(guild: discord.Guild, actor: discord.Member, target: discord.Member) -> bool:
    """Иерархия ролей: актор может действовать на цель."""
    if target.id == guild.owner_id or target.id == actor.id:
        return False
    if actor.id == guild.owner_id:
        return True
    return actor.top_role > target.top_role


STATUS_ICON = {"pending": "🟡", "approved": "🟢", "denied": "🔴", "sent": "📨"}
STATUS_TEXT = {"pending": "На рассмотрении", "approved": "Одобрена", "denied": "Отклонена", "sent": "Отправлено"}


async def dm(user: discord.abc.User, embed: discord.Embed) -> bool:
    try:
        await user.send(embed=embed)
        return True
    except discord.HTTPException:
        return False
