#!/usr/bin/env bash
# Бесплатный HTTPS для панели через Caddy.  Использование:  bash deploy/https.sh имя.duckdns.org
set -e
DOMAIN="$1"
[ -z "$DOMAIN" ] && { echo "Укажите домен: bash deploy/https.sh имя.duckdns.org"; exit 1; }
cd "$(dirname "$0")/.."

sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
sudo apt-get update -y && sudo apt-get install -y caddy

for p in 80 443; do
  sudo iptables -C INPUT -p tcp --dport $p -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 1 -p tcp --dport $p -j ACCEPT
done
sudo netfilter-persistent save >/dev/null

echo "$DOMAIN { reverse_proxy localhost:8080 }" | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl restart caddy

if grep -q '^BASE_URL=' .env; then sed -i "s|^BASE_URL=.*|BASE_URL=https://$DOMAIN|" .env; else echo "BASE_URL=https://$DOMAIN" >> .env; fi
sudo systemctl restart rpbot
echo "Готово. Панель: https://$DOMAIN  (не забудьте добавить https://$DOMAIN/callback в Redirects в Discord Developer Portal)"
