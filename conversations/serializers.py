from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import serializers
from .models import ConversationSession, ConversationMessage


def _user_display(user) -> str:
    if not user:
        return ""
    # Prefer full name, fallback to username/email
    full = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    if full:
        return full
    username = (getattr(user, "username", "") or "").strip()
    if username:
        return username
    email = (getattr(user, "email", "") or "").strip()
    return email


class ConversationSessionSerializer(serializers.ModelSerializer):
    loved_one_name = serializers.CharField(source="loved_one.name", read_only=True)
    loved_one_relationship = serializers.CharField(source="loved_one.relationship", read_only=True)

    # User-facing display name:
    # - if authenticated user exists -> full name / username / email
    # - else fallback to profile_id
    user_display = serializers.SerializerMethodField()

    class Meta:
        model = ConversationSession
        fields = [
            "id",
            "user",
            "user_display",
            "loved_one",
            "loved_one_name",
            "loved_one_relationship",
            "last_activity_at",
        ]
        read_only_fields = ["id", "last_activity_at"]

    def get_user_display(self, obj: ConversationSession) -> str:
        name = _user_display(getattr(obj, "user", None))
        return name or "User"


class ConversationMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversationMessage
        fields = ["id", "session", "role", "content", "seq", "created_at"]
        read_only_fields = ["id", "created_at"]


class ChatRequestSerializer(serializers.Serializer):
    loved_one_id = serializers.IntegerField()
    message = serializers.CharField(allow_blank=False, trim_whitespace=True)
    session_id = serializers.IntegerField(required=False)
    save_to_rag = serializers.BooleanField(required=False, default=True)


class ChatResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    session_id = serializers.IntegerField()
    assistant_message_id = serializers.IntegerField()
    assistant = serializers.CharField()
    rag_used = serializers.IntegerField()
