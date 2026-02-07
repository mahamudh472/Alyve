from django.urls import re_path
from .consumers import RealtimeVoiceConsumer

websocket_urlpatterns = [
    re_path(r"ws/voice/$", RealtimeVoiceConsumer.as_asgi()),
]
