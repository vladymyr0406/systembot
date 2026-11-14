import os
from pathlib import Path

try:
    from dotenv import load_dotenv  # необязательно
    load_dotenv()
except Exception:
    env = Path(".env")
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

TOKEN = os.getenv("DISCORD_TOKEN", "")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8080").rstrip("/")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
PORT = int(os.getenv("PORT", "8080"))
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").replace(" ", "").split(",") if x.isdigit()}
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID") or 0)
DB_PATH = os.getenv("DB_PATH", "data/bot.db")
# Резервные копии базы в Discord (чтобы данные не пропадали на хостингах со стираемым диском)
BACKUP_CHANNEL_ID = int(os.getenv("BACKUP_CHANNEL_ID") or 0)
BACKUP_MINUTES = max(5, int(os.getenv("BACKUP_MINUTES") or 30))
# Периодически обращаться к собственному адресу, чтобы бесплатный хостинг не «засыпал»
KEEPALIVE = os.getenv("KEEPALIVE", "1") != "0"
