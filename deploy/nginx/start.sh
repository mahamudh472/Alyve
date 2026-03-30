#!/usr/bin/env sh
set -eu

HTTPS_CONF="/etc/nginx/custom/https.conf"
HTTP_CONF="/etc/nginx/custom/http.conf"
ACTIVE_CONF="/etc/nginx/conf.d/default.conf"
CERT="/etc/nginx/certs/fullchain.pem"
KEY="/etc/nginx/certs/privkey.pem"

if [ -f "$CERT" ] && [ -f "$KEY" ]; then
  cp "$HTTPS_CONF" "$ACTIVE_CONF"
  echo "[nginx] TLS certificates found; HTTPS config enabled"
else
  cp "$HTTP_CONF" "$ACTIVE_CONF"
  echo "[nginx] TLS certificates missing; HTTP-only fallback enabled"
fi

exec nginx -g 'daemon off;'
