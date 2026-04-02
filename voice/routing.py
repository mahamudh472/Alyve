from django.urls import re_path

# DEPRECATED: Old WebSocket-based Realtime API has been replaced.
# 
# Use new REST API endpoints instead:
#   - POST /api/voice/chat/text/ (non-streaming)
#   - POST /api/voice/chat/text/stream/ (streaming, Server-Sent Events)
#
# consumers.py can be safely deleted - all functionality is now in voice/views.py

websocket_urlpatterns = []
