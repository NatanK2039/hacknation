#!/usr/bin/env bash
# Instalacja Asystenta UDIP na czystym Ubuntu 22.04/24.04 (uruchom jako root w katalogu z plikami aplikacji)
# Użycie: sudo bash install.sh <domena-lub-IP> <login> <hasło>
set -euo pipefail
DOMAIN="${1:?Podaj domenę (np. udip.itmagic.pl) albo IP}"
USER_="${2:-jury}"
PASS="${3:?Podaj hasło dostępu do aplikacji}"
APP_DIR=/opt/asystent-udip

echo ">> Aktualizacja systemu i pakiety bazowe"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y && apt-get upgrade -y
apt-get install -y ca-certificates curl ufw fail2ban unattended-upgrades

echo ">> Docker"
if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi
systemctl enable --now docker

echo ">> Firewall (22, 80, 443)"
ufw allow OpenSSH; ufw allow 80/tcp; ufw allow 443/tcp; ufw --force enable
systemctl enable --now fail2ban

echo ">> Pliki aplikacji -> $APP_DIR"
mkdir -p "$APP_DIR"
SRC="$(cd "$(dirname "$0")" && pwd)"
if [ "$SRC" != "$APP_DIR" ]; then cp -r "$SRC"/. "$APP_DIR"/; fi
cd "$APP_DIR"
mkdir -p db
[ -f .env ] || cp .env.example .env
chmod 600 .env

echo ">> Caddy (HTTPS + hasło)"
HASH=$(docker run --rm caddy:2 caddy hash-password --plaintext "$PASS")
if [[ "$DOMAIN" =~ ^[0-9.]+$ ]]; then SITE=":80"; else SITE="$DOMAIN"; fi
cat > Caddyfile <<CADDY
$SITE {
    encode gzip
    basic_auth {
        $USER_ $HASH
    }
    request_body {
        max_size 50MB
    }
    reverse_proxy app:8000 {
        transport http {
            read_timeout 180s
        }
    }
}
CADDY

echo ">> Start"
docker compose up -d --build
echo
echo "GOTOWE. Wpisz klucz API:  nano $APP_DIR/.env   (OPENAI_API_KEY=...)  potem:  cd $APP_DIR && docker compose up -d"
if [ "$SITE" = ":80" ]; then URL="http://$DOMAIN"; else URL="https://$DOMAIN"; fi
echo "Adres: $URL   login: $USER_"
