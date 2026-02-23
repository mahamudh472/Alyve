from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import ConversationSession, ConversationMessage


@admin.register(ConversationSession)
class ConversationSessionAdmin(ModelAdmin):
    list_display = ("id", "user", "loved_one", "last_activity_at")
    search_fields = ("title", "user__email", "loved_one__name")


@admin.register(ConversationMessage)
class ConversationMessageAdmin(ModelAdmin):
    list_display = ("id", "session", "seq", "role", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("content",)
