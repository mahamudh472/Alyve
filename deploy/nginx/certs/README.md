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
