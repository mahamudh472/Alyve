from django.db import models


class LovedOne(models.Model):
    profile_id = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=128, blank=True, default="")
    relationship = models.CharField(max_length=128, blank=True, default="")
    nickname_for_user = models.CharField(max_length=128, blank=True, default="")
    speaking_style = models.CharField(max_length=256, blank=True, default="")
    eleven_voice_id = models.CharField(max_length=128, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["profile_id", "created_at"]),
        ]


class Memory(models.Model):
    loved_one = models.ForeignKey(LovedOne, on_delete=models.CASCADE, related_name="memories")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["loved_one", "created_at"]),
        ]


class VoiceSample(models.Model):
    loved_one = models.ForeignKey(LovedOne, on_delete=models.CASCADE, related_name="voice_samples")
    file = models.FileField(upload_to="voice_samples/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["loved_one", "created_at"]),
        ]
