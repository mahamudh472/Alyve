"""
Channels middleware that authenticates WebSocket connections
via ``?token=`` or ``?access_token=`` query-string parameters or Authorization headers.

Usage (in asgi.py):
    from voice.token_auth import TokenAuthMiddleware
    ...
    "websocket": TokenAuthMiddleware(URLRouter(...))
"""

import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser

from main.auth import get_user_from_token

logger = logging.getLogger(__name__)


@database_sync_to_async
def _get_user(token: str):
    """Resolve a JWT access token to a User (sync DB hit wrapped for async)."""
    try:
        user = get_user_from_token(token)
        if user:
            return user
    except Exception as e:
        logger.error(f"[TokenAuthMiddleware] Error resolving user from token: {e}")
    return AnonymousUser()


class TokenAuthMiddleware(BaseMiddleware):
    """
    Reads ``?token=<jwt>``, ``?access_token=<jwt>`` or Authorization headers from the WebSocket
    connection and sets ``scope["user"]`` before the consumer runs.

    Falls back to ``AnonymousUser`` when the token is missing or invalid.
    """

    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode("utf-8")
        params = parse_qs(query_string)

        # Accept ?token= or ?access_token= or ?bearer=
        token = (
            params.get("token")
            or params.get("access_token")
            or params.get("bearer")
            or [None]
        )[0]

        # If not in query params, inspect Authorization header
        if not token:
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode("utf-8")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
            elif auth_header:
                token = auth_header.strip()

        if token:
            user = await _get_user(token)
            scope["user"] = user
            if user and getattr(user, "is_authenticated", False):
                logger.info(f"[TokenAuthMiddleware] Authenticated user {user.email} (id={user.id}) for WebSocket")
            else:
                logger.warning(f"[TokenAuthMiddleware] Invalid or expired JWT token provided for WebSocket (prefix: {token[:12]}...)")
        else:
            scope["user"] = AnonymousUser()
            logger.warning("[TokenAuthMiddleware] No token found in WebSocket query parameters or headers.")

        return await super().__call__(scope, receive, send)
