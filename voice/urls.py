from django.urls import path, include
from . import views

urlpatterns = [

    path("lovedone/create/", views.lovedone_create),
    path("lovedone/list/", views.lovedone_list),
    path("lovedone/get/", views.lovedone_get),
    path("memory/add/", views.add_memory),
    path("voice/upload/", views.upload_voice_sample),
    path("chat/text/", views.voice_chat_text),
    path("chat/text/stream/", views.voice_chat_text_stream), 
]
