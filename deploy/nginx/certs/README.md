Place TLS certificate files here for Nginx:
- fullchain.pem
- privkey.pem

If these files are not present, the stack will still run and Nginx will serve HTTP only.

Quick local self-signed cert (valid 365 days):

openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout deploy/nginx/certs/privkey.pem \
  -out deploy/nginx/certs/fullchain.pem \
  -days 365 \
  -subj "/CN=localhost"

For production, replace with real certs (Let's Encrypt or your CA).

## Let's Encrypt renewal (production)

Nginx only reads the files in this directory, not Certbot's `live/` symlinks, so a
deploy hook copies renewed certs here and reloads Nginx.

The webroot plugin validates over HTTP-01 through `/var/www/certbot` (mounted at
`/etc/nginx/certbot` in the nginx container, see docker-compose.yml). It requires
no downtime, unlike the standalone plugin, which cannot bind port 80 while the
nginx container holds it.

One-time setup:

    sudo mkdir -p /var/www/certbot
    sudo certbot certonly --webroot -w /var/www/certbot -d example.com \
      --cert-name example.com

`/etc/letsencrypt/renewal-hooks/deploy/alyve-nginx.sh` then handles every renewal:

    #!/bin/sh
    set -e
    install -m 644 /etc/letsencrypt/live/example.com/fullchain.pem \
      /root/Alyve/deploy/nginx/certs/fullchain.pem
    install -m 600 /etc/letsencrypt/live/example.com/privkey.pem \
      /root/Alyve/deploy/nginx/certs/privkey.pem
    docker kill -s HUP alyve-nginx

Verify with `sudo certbot renew --dry-run`. The timer runs twice daily via
`certbot.timer`; confirm it with `systemctl list-timers | grep certbot`.
