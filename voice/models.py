from django.db import models


class LovedOne(models.Model):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="loved_ones", blank=True, null=True)
    name = models.CharField(max_length=128, blank=True, null=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    relationship = models.CharField(max_length=128, blank=True, null=True)
    nickname_for_user = models.CharField(max_length=128, blank=True, null=True)
    speaking_style = models.CharField(max_length=256, blank=True, null=True)
    eleven_voice_id = models.CharField(max_length=128, blank=True, null=True)
    catch_phrase = models.CharField(max_length=120, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    core_memories = models.TextField(blank=True, null=True)
    last_conversation_at = models.DateTimeField(blank=True, null=True)
    voice_file = models.FileField(upload_to="voice_files/", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),
        ]


class Quote(models.Model):
    class QuoteScope(models.TextChoices):
        GLOBAL = "global", "Global"
        PERSONAL = "personal", "Personal"

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="quotes",
        blank=True,
        null=True,
    )
    loved_one = models.ForeignKey(
        "voice.LovedOne",
        on_delete=models.CASCADE,
        related_name="quotes",
        blank=True,
        null=True,
    )
    quote_type = models.CharField(max_length=16, choices=QuoteScope.choices, default=QuoteScope.GLOBAL)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["quote_type", "created_at"]),
            models.Index(fields=["user", "quote_type"]),
            models.Index(fields=["loved_one", "quote_type"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return self.content[:80]

