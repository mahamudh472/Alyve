from django.urls import path
from . import views

urlpatterns = [

    path("lovedone/create/", views.lovedone_create),
    path("lovedone/list/", views.lovedone_list),
    path("lovedone/get/", views.lovedone_get),
    path("memory/add/", views.add_memory),
    path("voice/upload/", views.upload_voice_sample),
]
