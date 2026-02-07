from __future__ import annotations

import os
import requests

from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from django.conf import settings

from .models import Memory, VoiceSample, LovedOne
from .rag_factory import get_rag


_rag = get_rag()


def _maybe_clone_eleven_voice(lo: LovedOne, sample_paths: list[str]) -> str:
    """
    Create an ElevenLabs cloned voice if LovedOne.eleven_voice_id is empty.
    Returns the existing/new voice_id, or "" if ELEVENLABS_API_KEY is not set.

    Necessary change: allow passing multiple audio samples when creating the voice,
    and only clone when enough samples exist (quality/stability).
    """
    api_key = settings.VOICE_APP.get("ELEVENLABS_API_KEY") or os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        return lo.eleven_voice_id or ""

    if lo.eleven_voice_id:
        return lo.eleven_voice_id

    base_url = (settings.VOICE_APP.get("ELEVENLABS_BASE_URL") or os.getenv("ELEVENLABS_BASE_URL", "")).rstrip("/")
    if not base_url:
        raise RuntimeError("ELEVENLABS_BASE_URL must be set")

    url = f"{base_url}/v1/voices/add"
    headers = {"xi-api-key": api_key}

    name = (lo.name or "").strip() or f"lovedone-{lo.id}"
    data = {
        "name": name,
        "description": f"Cloned voice for LovedOne id={lo.id}",
    }

    files = []
    for p in sample_paths:
        try:
            fp = open(p, "rb")
        except OSError:
            continue
        files.append(("files", fp))

    if not files:
        return lo.eleven_voice_id or ""

    try:
        r = requests.post(url, headers=headers, data=data, files=files, timeout=90)
    finally:
        for _, fp in files:
            try:
                fp.close()
            except Exception:
                pass

    if r.status_code >= 400:
        raise RuntimeError(f"ElevenLabs clone failed: {r.status_code} {r.text[:400]}")

    j = r.json()
    voice_id = (j.get("voice_id") or "").strip()
    if not voice_id:
        raise RuntimeError("ElevenLabs clone returned no voice_id")

    lo.eleven_voice_id = voice_id
    lo.save(update_fields=["eleven_voice_id"])
    return voice_id


@api_view(["POST"])
@parser_classes([JSONParser])
def lovedone_create(request):
    profile_id = (request.data.get("profile_id") or "default").strip()
    name = (request.data.get("name") or "").strip()
    relationship = (request.data.get("relationship") or "").strip()
    nickname_for_user = (request.data.get("nickname_for_user") or "").strip()
    speaking_style = (request.data.get("speaking_style") or "").strip()

    lo = LovedOne.objects.create(
        profile_id=profile_id,
        name=name,
        relationship=relationship,
        nickname_for_user=nickname_for_user,
        speaking_style=speaking_style,
    )
    return Response({"ok": True, "loved_one_id": lo.id})


@api_view(["GET"])
def lovedone_list(request):
    profile_id = (request.query_params.get("profile_id") or "default").strip()
    items = LovedOne.objects.filter(profile_id=profile_id).order_by("-created_at")
    data = [
        {
            "id": lo.id,
            "name": lo.name,
            "relationship": lo.relationship,
            "nickname_for_user": lo.nickname_for_user,
            "speaking_style": lo.speaking_style,
            "eleven_voice_id": getattr(lo, "eleven_voice_id", ""),
            "created_at": lo.created_at.isoformat(),
        }
        for lo in items
    ]
    return Response({"ok": True, "items": data})


@api_view(["GET"])
def lovedone_get(request):
    profile_id = (request.query_params.get("profile_id") or "default").strip()
    loved_one_id = request.query_params.get("loved_one_id")
    if not loved_one_id:
        return Response({"error": "loved_one_id is required"}, status=400)

    lo = LovedOne.objects.filter(profile_id=profile_id, id=loved_one_id).first()
    if not lo:
        return Response({"error": "not_found"}, status=404)

    return Response(
        {
            "ok": True,
            "item": {
                "id": lo.id,
                "name": lo.name,
                "relationship": lo.relationship,
                "nickname_for_user": lo.nickname_for_user,
                "speaking_style": lo.speaking_style,
                "eleven_voice_id": getattr(lo, "eleven_voice_id", ""),
                "created_at": lo.created_at.isoformat(),
            },
        }
    )


@api_view(["POST"])
@parser_classes([JSONParser])
def add_memory(request):
    profile_id = (request.data.get("profile_id") or "default").strip()
    loved_one_id = request.data.get("loved_one_id")
    text = (request.data.get("text") or "").strip()

    if not loved_one_id:
        return Response({"error": "loved_one_id is required"}, status=400)
    if not text:
        return Response({"error": "text is required"}, status=400)

    lo = LovedOne.objects.filter(profile_id=profile_id, id=loved_one_id).first()
    if not lo:
        return Response({"error": "loved_one not found"}, status=404)

    m = Memory.objects.create(loved_one=lo, text=text)

    indexed_ids = _rag.add_memory(
        profile_id=profile_id,
        loved_one_id=int(lo.id),
        text=text,
        memory_id=str(m.id),
    )

    return Response({"ok": True, "memory_id": m.id, "indexed_ids": indexed_ids})


@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
def upload_voice_sample(request):
    profile_id = (request.data.get("profile_id") or "default").strip()
    loved_one_id = request.data.get("loved_one_id")
    f = request.FILES.get("file")
    force_reclone = request.data.get("force_reclone")

    if not loved_one_id:
        return Response({"error": "loved_one_id is required"}, status=400)
    if not f:
        return Response({"error": "file is required"}, status=400)

    lo = LovedOne.objects.filter(profile_id=profile_id, id=loved_one_id).first()
    if not lo:
        return Response({"error": "loved_one not found"}, status=404)

    # Optional: force re-clone (useful if the first clone was made from too-little audio).
    # Pass force_reclone=1 (or true/yes/on) to reset existing eleven_voice_id before cloning.
    fr = str(force_reclone or "").strip().lower()
    if fr in ("1", "true", "yes", "y", "on"):
        if getattr(lo, "eleven_voice_id", ""):
            lo.eleven_voice_id = ""
            lo.save(update_fields=["eleven_voice_id"])

    # VoiceSample has no profile_id field (keep your original fix)
    vs = VoiceSample.objects.create(loved_one=lo, file=f)

    # Clone gating (env-driven)
    min_samples = int(settings.VOICE_APP.get("ELEVENLABS_MIN_SAMPLES_FOR_CLONE", 1) or 1)
    max_files = int(settings.VOICE_APP.get("ELEVENLABS_MAX_FILES_FOR_CLONE", 5) or 5)

    qs = VoiceSample.objects.filter(loved_one=lo).order_by("id")
    samples_count = qs.count()

    voice_id = getattr(lo, "eleven_voice_id", "") or ""

    if (not voice_id) and (samples_count >= min_samples):
        sample_paths = [s.file.path for s in qs[:max_files] if s.file]
        try:
            voice_id = _maybe_clone_eleven_voice(lo, sample_paths)
        except Exception as e:
            return Response(
                {
                    "ok": True,
                    "voice_sample_id": vs.id,
                    "warning": f"clone_failed: {type(e).__name__}: {e}",
                    "samples_count": samples_count,
                    "min_samples_for_clone": min_samples,
                }
            )

    return Response(
        {
            "ok": True,
            "voice_sample_id": vs.id,
            "eleven_voice_id": voice_id,
            "samples_count": samples_count,
            "min_samples_for_clone": min_samples,
            "has_cloned_voice": bool(voice_id),
        }
    )
