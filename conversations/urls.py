from django.urls import path
from . import views
from . import chat_views

urlpatterns = [
    path("sessions/", views.session_list),
    path("messages/", views.message_list),
    path("sessions/end/", views.session_end),
    path("chat/", chat_views.chat),
]
