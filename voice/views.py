from __future__ import annotations

import os
import re
import requests
import uuid
import asyncio
from threading import Thread

from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.http import StreamingHttpResponse

from .models import LovedOne
from .rag_factory import get_rag
from .prompting import build_system_prompt, PromptContext
from .memory_auto import extract_memories_via_openai, heuristic_gate


_rag = get_rag()


def _split_into_sentences(text: str) -> list[str]:
    """
    Split text into sentences for streaming.
    Handles common abbreviations and edge cases.
    """
    if not text:
        return []
    
    # Replace common abbreviations to avoid splitting on them
    text = text.replace("Dr.", "Dr_ABBR_").replace("Mr.", "Mr_ABBR_").replace("Mrs.", "Mrs_ABBR_")
    text = text.replace("Ms.", "Ms_ABBR_").replace("Prof.", "Prof_ABBR_").replace("etc.", "etc_ABBR_")
    
    # Split on sentence-ending punctuation
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    # Restore abbreviations and clean up
    result = []
    for sent in sentences:
        sent = sent.replace("Dr_ABBR_", "Dr.").replace("Mr_ABBR_", "Mr.")
        sent = sent.replace("Mrs_ABBR_", "Mrs.").replace("Ms_ABBR_", "Ms.")
        sent = sent.replace("Prof_ABBR_", "Prof.").replace("etc_ABBR_", "etc.")
        sent = sent.strip()
        if sent:
            # Add back the ending punctuation in case it was stripped
            if not sent[-1] in ".!?":
                sent = sent + "."
            result.append(sent)
    
    return result if result else [text.strip()]


def _extract_and_save_memories(
    profile_id: str,
    loved_one_id: int,
    user_text: str,
    assistant_text: str,
) -> int:
    """
    Extract memories from conversation and save to DB + RAG.
    Returns count of memories saved.
    
    Note: This runs in a background thread to avoid blocking the response.
    """
    # Check if memory extraction is enabled
    if not settings.VOICE_APP.get("AUTO_MEMORY_ENABLED", True):
        return 0
    
    # Apply heuristic gate (conservative extraction)
    if not heuristic_gate(user_text):
        return 0
    
    # Get API key and model settings
    api_key = settings.VOICE_APP.get("OPENAI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    model = settings.VOICE_APP.get("OPENAI_MEMORY_MODEL", "gpt-4o-mini")
    max_items = int(settings.VOICE_APP.get("MEMORY_EXTRACT_MAX_ITEMS", 3))
    
    if not api_key:
        return 0
    
    try:
        # Call async function in a new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            memories = loop.run_until_complete(
                extract_memories_via_openai(
                    api_key=api_key,
                    model=model,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    max_items=max_items,
                )
            )
        finally:
            loop.close()
    except Exception:
        return 0
    
    if not memories:
        return 0
    
    # Check for duplicates against recent RAG results
    saved_count = 0
    try:
        recent = _rag.query(
            profile_id=profile_id,
            loved_one_id=int(loved_one_id),
            query_text=user_text,
            k=10,
        ).docs or []
        existing = set((d or "").strip().lower() for d in recent)
    except Exception:
        existing = set()
    
    # Save each memory to DB and RAG
    for memory in memories:
        text = (memory.text or "").strip()
        if not text or text.lower() in existing:
            continue
        
        try:
            # Save to LovedOne.core_memories
            lo = LovedOne.objects.filter(user_id=profile_id, id=loved_one_id).first()
            if lo:
                existing_mem = (getattr(lo, "core_memories", "") or "").strip()
                lo.core_memories = (
                    (existing_mem + "\n" + text).strip()
                    if existing_mem else text
                )
                lo.save(update_fields=["core_memories"])
                
                # Save to RAG
                memory_id = uuid.uuid4().hex
                _rag.add_memory(
                    profile_id=profile_id,
                    loved_one_id=int(loved_one_id),
                    text=text,
                    memory_id=memory_id,
                )
                saved_count += 1
                existing.add(text.lower())
        except Exception:
            continue
    
    return saved_count


def _auto_memory_background(profile_id: str, loved_one_id: int, user_text: str, assistant_text: str):
    """
    Run auto-memory extraction in a background thread (non-blocking).
    """
    try:
        _extract_and_save_memories(profile_id, loved_one_id, user_text, assistant_text)
    except Exception:
        pass  # Silently fail to avoid blocking response


def _lo_queryset_for_profile(profile_id: str, request=None):
    """
    New models.py uses LovedOne.user (FK) instead of profile_id.
    Backward-compat:
      - if request.user authenticated => use that
      - else if profile_id is numeric => treat as user_id
      - else => user is NULL (default/anonymous)
    Returns: (queryset, profile_key_for_rag)
    """
    if request is not None:
        u = getattr(request, "user", None)
        if u is not None and getattr(u, "is_authenticated", False):
            return LovedOne.objects.filter(user=u)

    return LovedOne.objects.none()

def _maybe_clone_eleven_voice(lo: LovedOne, sample_paths: list[str]) -> str:
    """
    Create an ElevenLabs cloned voice if LovedOne.eleven_voice_id is empty.
    Returns the existing/new voice_id, or "" if ELEVENLABS_API_KEY is not set.
    """
    api_key = settings.VOICE_APP.get("ELEVENLABS_API_KEY") or os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        return getattr(lo, "eleven_voice_id", "") or ""

    existing = getattr(lo, "eleven_voice_id", "") or ""
    if existing:
        return existing

    base_url = (settings.VOICE_APP.get("ELEVENLABS_BASE_URL") or os.getenv("ELEVENLABS_BASE_URL", "")).rstrip("/")
    if not base_url:
        raise RuntimeError("ELEVENLABS_BASE_URL must be set")

    url = f"{base_url}/v1/voices/add"
    headers = {"xi-api-key": api_key}

    name = (getattr(lo, "name", "") or "").strip() or f"lovedone-{lo.id}"
    data = {
        "name": name,
        "description": f"Cloned voice for LovedOne id={lo.id}",
    }

    print(f"file paths for cloning: {sample_paths}")
    files = []
    for p in sample_paths:
        try:
            fp = open(p, "rb")
        except OSError:
            continue
        files.append(("files", fp))

    if not files:
        return existing

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

    if hasattr(lo, "eleven_voice_id"):
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

    # NEW (user-based): decide which user to attach, if any
    user = None
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        user = request.user

    lo = LovedOne.objects.create(
        user=user,  # None => anonymous/default
        name=name,
        relationship=relationship,
        nickname_for_user=nickname_for_user,
        speaking_style=speaking_style,
    )
    return Response({"ok": True, "loved_one_id": lo.id})


@api_view(["GET"])
def lovedone_list(request):
    profile_id = (request.query_params.get("profile_id") or "default").strip()
    qs = _lo_queryset_for_profile(profile_id, request=request)

    items = qs.order_by("-created_at")
    data = []
    for lo in items:
        data.append(
            {
                "id": lo.id,
                "name": lo.name,
                "relationship": lo.relationship,
                "nickname_for_user": lo.nickname_for_user,
                "speaking_style": lo.speaking_style,
                "eleven_voice_id": getattr(lo, "eleven_voice_id", "") or "",
                "created_at": lo.created_at.isoformat() if getattr(lo, "created_at", None) else None,
                # New fields (safe to include; won't break old clients)
                "catch_phrase": getattr(lo, "catch_phrase", "") or "",
                "description": getattr(lo, "description", "") or "",
                "core_memories": getattr(lo, "core_memories", "") or "",
                "last_conversation_at": lo.last_conversation_at.isoformat()
                if getattr(lo, "last_conversation_at", None)
                else None,
                "voice_file": lo.voice_file.url if getattr(lo, "voice_file", None) else None,
            }
        )
    return Response({"ok": True, "items": data})


@api_view(["GET"])
def lovedone_get(request):
    profile_id = (request.query_params.get("profile_id") or "default").strip()
    loved_one_id = request.query_params.get("loved_one_id")
    if not loved_one_id:
        return Response({"error": "loved_one_id is required"}, status=400)

    qs = _lo_queryset_for_profile(profile_id, request=request)
    print(f"Debug: lovedone_get qs={qs}")
    print(f"Debug: lovedone_get filter id={loved_one_id}")
    lo = qs.filter(id=loved_one_id).first()
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
                "eleven_voice_id": getattr(lo, "eleven_voice_id", "") or "",
                "created_at": lo.created_at.isoformat() if getattr(lo, "created_at", None) else None,
                "catch_phrase": getattr(lo, "catch_phrase", "") or "",
                "description": getattr(lo, "description", "") or "",
                "core_memories": getattr(lo, "core_memories", "") or "",
                "last_conversation_at": lo.last_conversation_at.isoformat()
                if getattr(lo, "last_conversation_at", None)
                else None,
                "voice_file": lo.voice_file.url if getattr(lo, "voice_file", None) else None,
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

    qs = _lo_queryset_for_profile(profile_id, request=request)
    profile_key = str(profile_id)
    lo = qs.filter(id=loved_one_id).first()
    if not lo:
        return Response({"error": "loved_one not found"}, status=404)

    # NEW: Memory model removed -> append into LovedOne.core_memories
    existing = (getattr(lo, "core_memories", "") or "").strip()
    lo.core_memories = (existing + "\n" + text).strip() if existing else text
    lo.save(update_fields=["core_memories"])

    memory_id = uuid.uuid4().hex

    indexed_ids = _rag.add_memory(
        profile_id=profile_key,
        loved_one_id=int(lo.id),
        text=text,
        memory_id=memory_id,
    )

    return Response({"ok": True, "memory_id": memory_id, "indexed_ids": indexed_ids})


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

    qs = _lo_queryset_for_profile(profile_id, request=request)
    lo = qs.filter(id=loved_one_id).first()
    if not lo:
        return Response({"error": "loved_one not found"}, status=404)

    # Optional: force re-clone (reset existing eleven_voice_id before cloning).
    fr = str(force_reclone or "").strip().lower()
    if fr in ("1", "true", "yes", "y", "on"):
        if hasattr(lo, "eleven_voice_id") and (getattr(lo, "eleven_voice_id", "") or ""):
            lo.eleven_voice_id = ""
            lo.save(update_fields=["eleven_voice_id"])

    # NEW: VoiceSample model removed -> store file directly on LovedOne.voice_file
    lo.voice_file = f
    lo.save(update_fields=["voice_file"])

    voice_id = getattr(lo, "eleven_voice_id", "") or ""

    # Clone gating (env-driven)
    min_samples = int(settings.VOICE_APP.get("ELEVENLABS_MIN_SAMPLES_FOR_CLONE", 1) or 1)
    max_files = int(settings.VOICE_APP.get("ELEVENLABS_MAX_FILES_FOR_CLONE", 5) or 5)

    # With the new schema we typically have only one file, but keep the same gating contract.
    samples_count = 1

    sample_paths = []
    try:
        if getattr(lo, "voice_file", None) and hasattr(lo.voice_file, "path"):
            sample_paths = [lo.voice_file.path][:max_files]
    except Exception:
        sample_paths = []

    if (not voice_id) and (samples_count >= min_samples):
        try:
            voice_id = _maybe_clone_eleven_voice(lo, sample_paths)
        except Exception as e:
            return Response(
                {
                    "ok": True,
                    "voice_file_saved": True,
                    "warning": f"clone_failed: {type(e).__name__}: {e}",
                    "samples_count": samples_count,
                    "min_samples_for_clone": min_samples,
                }
            )

    return Response(
        {
            "ok": True,
            "voice_file_saved": True,
            "eleven_voice_id": voice_id,
            "samples_count": samples_count,
            "min_samples_for_clone": min_samples,
            "has_cloned_voice": bool(voice_id),
        }
    )


@api_view(["POST"])
@parser_classes([JSONParser])
def voice_chat_text(request):
    """
    New endpoint for text-based voice chat (frontend handles STT, backend only does LLM).
    
    Request:
      {
        "text": "what is your name?",
        "loved_one_id": 123,
        "session_id": 456  (optional - will create new if not provided)
      }
    
    Response:
      {
        "ok": true,
        "response": "I am your loved one...",
        "session_id": 456,
        "loved_one_name": "Grandma"
      }
    """
    text = (request.data.get("text") or "").strip()
    loved_one_id = request.data.get("loved_one_id")
    session_id = request.data.get("session_id")
    
    # Validation
    if not text:
        return Response({"error": "text is required"}, status=400)
    if not loved_one_id:
        return Response({"error": "loved_one_id is required"}, status=400)
    
    # Get authenticated user if available
    user = getattr(request, "user", None) if getattr(request, "user", None) and request.user.is_authenticated else None
    profile_id = str(user.id) if user else "default"
    
    # Load LovedOne
    qs = _lo_queryset_for_profile(profile_id, request=request)
    lo = qs.filter(id=loved_one_id).first()
    if not lo:
        return Response({"error": "loved_one not found"}, status=404)
    
    try:
        # Create or get conversation session
        from conversations.models import ConversationSession, ConversationMessage
        
        with transaction.atomic():
            if session_id:
                conv_session = ConversationSession.objects.filter(id=session_id, loved_one_id=loved_one_id).first()
                if not conv_session:
                    return Response({"error": "session not found"}, status=404)
            else:
                conv_session = ConversationSession.objects.create(
                    user=user,
                    loved_one_id=int(loved_one_id),
                    channel=ConversationSession.CHANNEL_VOICE,
                )
            
            # Save user message
            last_seq = (
                ConversationMessage.objects.filter(session_id=conv_session.id)
                .order_by("-seq")
                .values_list("seq", flat=True)
                .first()
            )
            seq = int(last_seq or 0) + 1
            
            ConversationMessage.objects.create(
                session_id=conv_session.id,
                role="user",
                content=text,
                seq=seq,
            )
            
            # Update conversation timestamps
            ConversationSession.objects.filter(id=conv_session.id).update(last_activity_at=timezone.now())
            LovedOne.objects.filter(id=int(loved_one_id)).update(last_conversation_at=timezone.now())
            
            # Get recent conversation history (for context)
            recent_msgs = ConversationMessage.objects.filter(
                session_id=conv_session.id
            ).order_by("-seq")[:28]  # Get last 28 (14 turns)
            
            recent_msgs_list = list(reversed(list(recent_msgs.values("role", "content"))))
            
            # Build chat history for OpenAI
            history_messages = [
                {"role": msg["role"], "content": msg["content"]}
                for msg in recent_msgs_list
            ]
            
            # Get RAG context
            profile_key = profile_id
            rag_docs = []
            try:
                rag_result = _rag.query(
                    profile_id=profile_key,
                    loved_one_id=int(loved_one_id),
                    query_text=text,
                    k=6,
                )
                rag_docs = rag_result.docs or []
            except Exception:
                rag_docs = []
            
            # Format RAG context
            rag_context = ""
            if rag_docs:
                picked = []
                total_chars = 0
                max_total = 1400
                for doc in rag_docs:
                    doc_str = (doc or "").strip()
                    if not doc_str:
                        continue
                    doc_str = (doc_str[:320] if len(doc_str) > 320 else doc_str)
                    if total_chars + len(doc_str) > max_total:
                        break
                    picked.append(doc_str)
                    total_chars += len(doc_str)
                
                if picked:
                    rag_context = "CONTEXT (relevant memories):\n" + "\n".join(f"- {x}" for x in picked) + "\n"
            
            # Build persona block
            persona_parts = []
            if lo.name:
                persona_parts.append(f"Name: {lo.name}")
            if lo.relationship:
                persona_parts.append(f"Relationship: {lo.relationship}")
            if lo.speaking_style:
                persona_parts.append(f"Speaking style: {lo.speaking_style}")
            if lo.catch_phrase:
                persona_parts.append(f"Catch phrase: {lo.catch_phrase}")
            if lo.description:
                persona_parts.append(f"Description: {lo.description}")
            
            persona_block = "\n".join(persona_parts) if persona_parts else "(no persona data)"
            
            # Build memories block
            memories_block = ""
            if lo.core_memories:
                memories_block = lo.core_memories
            if rag_context:
                memories_block = (memories_block + "\n\n" + rag_context).strip() if memories_block else rag_context
            
            # Build system prompt using PromptContext
            prompt_ctx = PromptContext(
                profile_id=profile_key,
                loved_one_id=int(loved_one_id),
                persona_block=persona_block,
                memories_block=memories_block,
            )
            system_prompt = build_system_prompt(prompt_ctx)
            
            # Call OpenAI
            from conversations.openai_client import generate_reply
            
            try:
                result = generate_reply(system_prompt=system_prompt, user_text=text)
                assistant_response = result.text
            except Exception as e:
                return Response(
                    {"error": f"openai_failed: {type(e).__name__}: {str(e)[:200]}"},
                    status=500,
                )
            
            # Save assistant response
            seq += 1
            ConversationMessage.objects.create(
                session_id=conv_session.id,
                role="assistant",
                content=assistant_response,
                seq=seq,
            )
            
            ConversationSession.objects.filter(id=conv_session.id).update(last_activity_at=timezone.now())
        
        return Response(
            {
                "ok": True,
                "response": assistant_response,
                "session_id": conv_session.id,
                "loved_one_name": lo.name or "",
                "loved_one_id": int(loved_one_id),
            }
        )
    
    except Exception as e:
        return Response(
            {"error": f"unexpected_error: {type(e).__name__}: {str(e)[:200]}"},
            status=500,
        )


@api_view(["POST"])
@parser_classes([JSONParser])
def voice_chat_text_stream(request):
    """
    Streaming endpoint for text-based voice chat with sentence-wise streaming.
    Uses Server-Sent Events (SSE) to stream response sentence by sentence.
    
    Request:
      {
        "text": "what is your name?",
        "loved_one_id": 123,
        "session_id": 456  (optional - will create new if not provided)
      }
    
    Response (SSE stream):
      event: stream.start
      data: {"session_id": 456, "loved_one_name": "Grandma"}
      
      event: stream.sentence
      data: {"sentence": "I am your loved one.", "index": 0}
      
      event: stream.complete
      data: {"total_sentences": 3}
    """
    text = (request.data.get("text") or "").strip()
    loved_one_id = request.data.get("loved_one_id")
    session_id = request.data.get("session_id")
    
    # Validation
    if not text:
        return Response({"error": "text is required"}, status=400)
    if not loved_one_id:
        return Response({"error": "loved_one_id is required"}, status=400)
    
    # Get authenticated user if available
    user = getattr(request, "user", None) if getattr(request, "user", None) and request.user.is_authenticated else None
    profile_id = str(user.id) if user else "default"
    
    # Load LovedOne
    qs = _lo_queryset_for_profile(profile_id, request=request)
    lo = qs.filter(id=loved_one_id).first()
    if not lo:
        return Response({"error": "loved_one not found"}, status=404)
    
    def stream_response():
        """Generator function for SSE streaming"""
        conv_session = None
        assistant_response = None
        
        try:
            from conversations.models import ConversationSession, ConversationMessage
            
            with transaction.atomic():
                # Create or get conversation session
                if session_id:
                    conv_session = ConversationSession.objects.filter(id=session_id, loved_one_id=loved_one_id).first()
                    if not conv_session:
                        yield f"event: error\ndata: {{'error': 'session not found'}}\n\n"
                        return
                else:
                    conv_session = ConversationSession.objects.create(
                        user=user,
                        loved_one_id=int(loved_one_id),
                        channel=ConversationSession.CHANNEL_VOICE,
                    )
                
                # Save user message
                last_seq = (
                    ConversationMessage.objects.filter(session_id=conv_session.id)
                    .order_by("-seq")
                    .values_list("seq", flat=True)
                    .first()
                )
                seq = int(last_seq or 0) + 1
                
                ConversationMessage.objects.create(
                    session_id=conv_session.id,
                    role="user",
                    content=text,
                    seq=seq,
                )
                
                # Update conversation timestamps
                ConversationSession.objects.filter(id=conv_session.id).update(last_activity_at=timezone.now())
                LovedOne.objects.filter(id=int(loved_one_id)).update(last_conversation_at=timezone.now())
                
                # Get recent conversation history
                recent_msgs = ConversationMessage.objects.filter(
                    session_id=conv_session.id
                ).order_by("-seq")[:28]
                
                recent_msgs_list = list(reversed(list(recent_msgs.values("role", "content"))))
                
                # Get RAG context
                profile_key = profile_id
                rag_docs = []
                try:
                    rag_result = _rag.query(
                        profile_id=profile_key,
                        loved_one_id=int(loved_one_id),
                        query_text=text,
                        k=6,
                    )
                    rag_docs = rag_result.docs or []
                except Exception:
                    rag_docs = []
                
                # Format RAG context
                rag_context = ""
                if rag_docs:
                    picked = []
                    total_chars = 0
                    max_total = 1400
                    for doc in rag_docs:
                        doc_str = (doc or "").strip()
                        if not doc_str:
                            continue
                        doc_str = (doc_str[:320] if len(doc_str) > 320 else doc_str)
                        if total_chars + len(doc_str) > max_total:
                            break
                        picked.append(doc_str)
                        total_chars += len(doc_str)
                    
                    if picked:
                        rag_context = "CONTEXT (relevant memories):\n" + "\n".join(f"- {x}" for x in picked) + "\n"
                
                # Build persona block
                persona_parts = []
                if lo.name:
                    persona_parts.append(f"Name: {lo.name}")
                if lo.relationship:
                    persona_parts.append(f"Relationship: {lo.relationship}")
                if lo.speaking_style:
                    persona_parts.append(f"Speaking style: {lo.speaking_style}")
                if lo.catch_phrase:
                    persona_parts.append(f"Catch phrase: {lo.catch_phrase}")
                if lo.description:
                    persona_parts.append(f"Description: {lo.description}")
                
                persona_block = "\n".join(persona_parts) if persona_parts else "(no persona data)"
                
                # Build memories block
                memories_block = ""
                if lo.core_memories:
                    memories_block = lo.core_memories
                if rag_context:
                    memories_block = (memories_block + "\n\n" + rag_context).strip() if memories_block else rag_context
                
                # Build system prompt
                prompt_ctx = PromptContext(
                    profile_id=profile_key,
                    loved_one_id=int(loved_one_id),
                    persona_block=persona_block,
                    memories_block=memories_block,
                )
                system_prompt = build_system_prompt(prompt_ctx)

                from conversations.openai_client import generate_reply, stream_reply
                
                # Send stream start event
                loved_one_name = (lo.name or '').replace('"', '\\"')
                yield f'event: stream.start\ndata: {{"session_id": {conv_session.id}, "loved_one_name": "{loved_one_name}"}}\n\n'

            # --- LLM STREAMING (OUTSIDE TRANSACTION) ---
            full_assistant_text = ""
            sentence_buffer = ""
            sentence_idx = 0
            
            # Simple incremental sentence splitting:
            # We yield as soon as we see a terminator followed by space or end of stream.
            for delta in stream_reply(system_prompt=system_prompt, user_text=text):
                full_assistant_text += delta
                sentence_buffer += delta
                
                # Check if we have a sentence terminator
                # We look for . ! ? followed by a space or if it's the very end
                # (but we only know it's the end after the loop)
                if any(t in sentence_buffer for t in ('. ', '! ', '? ', '.\n', '!\n', '?\n')):
                    # Use the existing splitter on what we have so far
                    parts = _split_into_sentences(sentence_buffer)
                    # If we have at least 2 parts, the first ones are definitely complete sentences
                    if len(parts) > 1:
                        for i in range(len(parts) - 1):
                            s = parts[i].strip()
                            if s:
                                escaped_s = s.replace('"', '\\"')
                                yield f"event: stream.sentence\ndata: {{\"sentence\": \"{escaped_s}\", \"index\": {sentence_idx}}}\n\n"
                                sentence_idx += 1
                        # Keep the last part as the new buffer
                        sentence_buffer = parts[-1]
            
            # Flush the remaining buffer
            if sentence_buffer.strip():
                parts = _split_into_sentences(sentence_buffer)
                for s in parts:
                    s = s.strip()
                    if s:
                        escaped_s = s.replace('"', '\\"')
                        yield f"event: stream.sentence\ndata: {{\"sentence\": \"{escaped_s}\", \"index\": {sentence_idx}}}\n\n"
                        sentence_idx += 1

            # Send completion event
            yield f"event: stream.complete\ndata: {{\"total_sentences\": {sentence_idx}}}\n\n"
            
            # --- POST-STREAM UPDATES ---
            if full_assistant_text:
                # Save full response to database
                from conversations.models import ConversationMessage
                
                last_seq_val = (
                    ConversationMessage.objects.filter(session_id=conv_session.id)
                    .order_by("-seq")
                    .values_list("seq", flat=True)
                    .first()
                )
                final_seq = int(last_seq_val or 0) + 1
                
                ConversationMessage.objects.create(
                    session_id=conv_session.id,
                    role="assistant",
                    content=full_assistant_text,
                    seq=final_seq,
                )
                
                # Update timestamps
                from conversations.models import ConversationSession
                ConversationSession.objects.filter(id=conv_session.id).update(last_activity_at=timezone.now())
                
                # Extract and save memories in background
                mem_thread = Thread(
                    target=_auto_memory_background,
                    args=(profile_key, int(loved_one_id), text, full_assistant_text),
                    daemon=True,
                )
                mem_thread.start()
        except Exception as e:
            yield f"event: error\ndata: {{\"error\": \"unexpected_error: {type(e).__name__}: {str(e)[:100]}\"}}\n\n"
    
    # Return streaming response with SSE content type
    response = StreamingHttpResponse(stream_response(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # Disable proxy buffering
    return response
