from django.db import models


class LovedOne(models.Model):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="loved_ones", blank=True, null=True)
    name = models.CharField(max_length=128, blank=True, default="")
    relationship = models.CharField(max_length=128, blank=True, default="")
    nickname_for_user = models.CharField(max_length=128, blank=True, default="")
    speaking_style = models.CharField(max_length=256, blank=True, default="")
    eleven_voice_id = models.CharField(max_length=128, blank=True, default="")
    catch_phrase = models.CharField(max_length=120, blank=True, null=True)
    description = models.TextField(blank=True, default="")
    core_memories = models.TextField(blank=True, default="")
    last_conversation_at = models.DateTimeField(blank=True, null=True)
    voice_file = models.FileField(upload_to="voice_files/", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),
        ]

