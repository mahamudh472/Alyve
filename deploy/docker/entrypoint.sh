#!/usr/bin/env sh
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec uvicorn config.asgi:application --host 0.0.0.0 --port 8001 --reload