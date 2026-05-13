from django.urls import re_path

# DEPRECATED: Old WebSocket-based Realtime API has been replaced.
# 
# Use new REST API endpoints instead:
#   - POST /api/voice/chat/text/ (non-streaming)
#   - POST /api/voice/chat/text/stream/ (streaming, Server-Sent Events)
#
# consumers.py can be safely deleted - all functionality is now in voice/views.py

from . import consumers

websocket_urlpatterns = [
    re_path(r"ws/voice/chat/text/stream/(?P<loved_one_id>\d+)/$", consumers.VoiceChatConsumer.as_asgi()),
]
