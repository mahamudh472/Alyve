import logging
import os
import requests
from threading import Thread

from django.conf import settings
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import LovedOne, Quote
from .quote_generation import generate_personal_quotes_for_loved_one

logger = logging.getLogger(__name__)


@receiver(post_save, sender=LovedOne)
def create_personal_quotes_for_loved_one(sender, instance: LovedOne, created: bool, **kwargs):
    if Quote.objects.filter(loved_one=instance, quote_type=Quote.QuoteScope.PERSONAL).exists():
        return

    generate_personal_quotes_for_loved_one(instance)


def delete_elevenlabs_voice(voice_id: str):
    if not voice_id:
        return
    api_key = settings.VOICE_APP.get("ELEVENLABS_API_KEY") or os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        logger.warning(f"ELEVENLABS_API_KEY not configured. Skipping deletion of ElevenLabs voice_id {voice_id}")
        return
    base_url = (settings.VOICE_APP.get("ELEVENLABS_BASE_URL") or os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io")).rstrip("/")
    if not base_url:
        logger.warning("ELEVENLABS_BASE_URL not configured. Skipping voice deletion.")
        return

    url = f"{base_url}/v1/voices/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "accept": "application/json"
    }
    
    try:
        r = requests.delete(url, headers=headers, timeout=15)
        if r.status_code >= 400:
            logger.error(f"ElevenLabs voice deletion failed for voice_id {voice_id}: {r.status_code} {r.text[:400]}")
        else:
            logger.info(f"Successfully deleted ElevenLabs voice_id {voice_id}")
    except Exception as e:
        logger.error(f"Error deleting ElevenLabs voice_id {voice_id}: {e}")


def delete_elevenlabs_voice_background(voice_id: str):
    thread = Thread(target=delete_elevenlabs_voice, args=(voice_id,), daemon=True)
    thread.start()


@receiver(post_delete, sender=LovedOne)
def delete_loved_one_voice(sender, instance: LovedOne, **kwargs):
    voice_id = getattr(instance, "eleven_voice_id", "")
    if voice_id:
        delete_elevenlabs_voice_background(voice_id)