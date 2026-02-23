from __future__ import annotations

from django.conf import settings
from django.db import models


class ConversationSession(models.Model):
    """
    A single voice/text session between a user (optional) and a LovedOne.
    Stores overall metadata and links messages via ConversationMessage.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversation_sessions",
    )


    loved_one = models.ForeignKey(
        "voice.LovedOne",
        on_delete=models.CASCADE,
        related_name="conversation_sessions",
    )

    last_activity_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["loved_one"]),
            models.Index(fields=["last_activity_at"]),
        ]

    def __str__(self) -> str:
        lo = getattr(self.loved_one, "name", "") or f"loved_one:{self.loved_one_id}"
        return f"Session#{self.id} ({self.profile_id}) ↔ {lo}"


class ConversationMessage(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_SYSTEM = "system"

    ROLE_CHOICES = [
        (ROLE_USER, "User"),
        (ROLE_ASSISTANT, "Assistant"),
        (ROLE_SYSTEM, "System"),
    ]

    session = models.ForeignKey(
        ConversationSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )

    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content = models.TextField()

    # Increasing sequence within a session (helps stable ordering)
    seq = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["session", "seq"]),
            models.Index(fields=["session", "created_at"]),
        ]
        ordering = ["seq", "created_at"]

    def __str__(self) -> str:
        return f"Msg#{self.id} s={self.session_id} role={self.role} seq={self.seq}"
