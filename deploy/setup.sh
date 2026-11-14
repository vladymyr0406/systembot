#!/usr/bin/env bash
# Установка бота на Ubuntu (Oracle Cloud). Запуск из папки проекта:  bash deploy/setup.sh
set -e
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"

echo "==> Устанавливаю пакеты (Python, ffmpeg)…"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip ffmpeg libopus0 iptables-persistent

echo "==> Открываю порт 8080 в системном файрволе…"
sudo iptables -C INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 1 -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save >/dev/null

echo "==> Ставлю зависимости бота…"
python3 -m venv venv
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt python-dotenv -q

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Создан файл .env. Заполните его командой:  nano .env"
  echo "(сохранить: Ctrl+O, Enter; выйти: Ctrl+X), затем снова запустите:  bash deploy/setup.sh"
  exit 0
fi

echo "==> Создаю службу, чтобы бот работал всегда и запускался после перезагрузки…"
sudo tee /etc/systemd/system/rpbot.service >/dev/null <<UNIT
[Unit]
Description=RP Core Discord bot
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now rpbot
sleep 3
sudo systemctl --no-pager status rpbot | head -n 12
echo
echo "Готово. Логи:  journalctl -u rpbot -f      Перезапуск:  sudo systemctl restart rpbot"
