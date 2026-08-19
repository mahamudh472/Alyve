"""
DEPRECATED: Old WebSocket-based Realtime Voice API

This module is NO LONGER IN USE. The old architecture has been replaced with REST API endpoints:
  - POST /api/voice/chat/text/ (non-streaming)
  - POST /api/voice/chat/text/stream/ (streaming via Server-Sent Events)

The new architecture:
  - Frontend handles: STT (speech-to-text) and TTS (text-to-speech)
  - Backend handles: LLM responses, RAG context, conversation history, auto-memory

All functionality from this consumer (RAG context, memory extraction, prompt building, etc.)
is now integrated into the new REST endpoints in voice/views.py

TO CLEAN UP:
1. Delete this file (voice/consumers.py)
2. Delete voice/consumer_helpers.py (helper functions)
3. Update voice/routing.py to remove the import (already done)
4. Remove related settings from config/settings.py (already done)

The code is left here for reference only.
"""

from __future__ import annotations

import asyncio
import aiohttp
import base64
import json
import os
import re
import audioop
from dataclasses import dataclass
from typing import Optional, List, Tuple

import websockets
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.utils import timezone  # <-- ADDED (needed to end sessions)

from .rag_factory import get_rag
from .memory_auto import extract_memories_via_openai, heuristic_gate
from .providers.tts_elevenlabs import ElevenLabsTTS, ElevenLabsTTSConfig

from .prompting import PromptContext, build_system_prompt, build_reply_instructions
from conversations.models import ConversationSession, ConversationMessage
from conversations.openai_client import stream_reply

# Keep helper functions importable from consumers.py (backward compat)
from .consumer_helpers import (
    _debug_enabled,
    _silence_pcm16,
    _normalize_text_for_tts,
    _chunk_text_for_cadence,
)

OPENAI_REALTIME_URL = settings.VOICE_APP.get("OPENAI_REALTIME_URL")


@dataclass
class SessionCfg:
    profile_id: str = "default"
    loved_one_id: int = 0

    ptt_enabled: bool = False
    ptt_down: bool = False

    # Default faster VAD (you asked 600 or less)
    vad_silence_ms: int = 600
    vad_threshold: float = 0.55

    loved_one_name: str = ""
    loved_one_relationship: str = ""
    loved_one_nickname_for_user: str = ""
    loved_one_speaking_style: str = ""

    # NEW model fields
    catch_phrase: str = ""
    description: str = ""
    core_memories: str = ""

    eleven_voice_id: str = ""


class RealtimeVoiceConsumer(AsyncWebsocketConsumer):
    async def _send_json(self, obj: dict):
        if getattr(self, "_ws_closed", False):
            return
        try:
            await self.send(text_data=json.dumps(obj))
        except Exception:
            self._ws_closed = True

    def _apply_config(self, content: dict):
        def i(key: str, default: int) -> int:
            try:
                return int(content.get(key, default))
            except Exception:
                return default

        def f(key: str, default: float) -> float:
            try:
                return float(content.get(key, default))
            except Exception:
                return default

        # allow down to 300ms (so your 600 works and even lower if needed)
        self.cfg.vad_silence_ms = max(300, min(4000, i("vad_silence_ms", self.cfg.vad_silence_ms)))
        self.cfg.vad_threshold = max(0.05, min(0.95, f("vad_threshold", self.cfg.vad_threshold)))
        self.cfg.ptt_enabled = bool(content.get("ptt_enabled", self.cfg.ptt_enabled))

    @staticmethod
    def _truncate(s: str, max_chars: int) -> str:
        s = (s or "").strip()
        if len(s) <= max_chars:
            return s
        return s[: max(0, max_chars - 1)].rstrip() + "…"

    @staticmethod
    def _looks_like_noise(transcript: str) -> bool:
        t = (transcript or "").strip().lower()
        if not t:
            return True
        if len(t) < 3:
            return True
        if t in {"um", "uh", "hmm", "hm", "mm"}:
            return True
        if all(ch in ".…," for ch in t):
            return True
        return False

    def _ends_thought(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        if re.search(r"[.?!…]+[\"')\]]?$", t):
            return True
        return False

    @staticmethod
    def _looks_like_story_mode(text: str) -> bool:
        t = (text or "").lower()
        triggers = [
            "tell me a story",
            "story",
            "long story",
            "explain",
            "in detail",
            "deep dive",
            "walk me through",
            "step by step",
            "describe",
            "what happened",
            "what was it like",
        ]
        return any(k in t for k in triggers)

    def _compute_grace_ms(self, full_text: str) -> int:
        """
        Extra debounce AFTER VAD says speech stopped.
        Keeps story mode intact, but allows fast resume after a barge-in.
        """
        t = (full_text or "").strip()
        words = len(t.split())

        base = int(getattr(self, "_end_of_turn_grace_ms", 450))  # faster default

        # If user just barged in, make the next response quicker (better UX)
        now = asyncio.get_running_loop().time()
        if (now - getattr(self, "_barge_in_ts", 0.0)) <= 6.0:
            base = min(base, 300)

        if words <= 6:
            grace = base
        elif words <= 14:
            grace = max(base, 650)
        elif words <= 30:
            grace = max(base, 900)
        elif words <= 70:
            grace = max(base, 1200)
        else:
            grace = max(base, 1500)

        # Story mode: preserve your “don’t cut off narration” behavior
        if self._looks_like_story_mode(t):
            grace = max(grace, 1200)

        # If it looks like mid-thought, wait a bit more
        if words >= 12 and (not self._ends_thought(t)):
            grace = max(grace, 900)

        last = (t.split()[-1].lower() if t.split() else "")
        if last in {"and", "but", "so", "because", "then", "with", "of", "to", "or"}:
            grace = max(grace, 1100)

        return grace

    # ==========================================================
    # ADDED: Conversation DB helpers (new conversations app)
    # ==========================================================

    @database_sync_to_async
    def _db_create_conversation_session(self, profile_id: str, loved_one_id: int) -> int:
        """
        Creates a conversations.ConversationSession row for VOICE channel.
        Uses authenticated user from TokenAuthMiddleware (self.scope["user"]) when available.
        """
        from conversations.models import ConversationSession

        user = getattr(self, "scope", {}).get("user", None)
        if not (user and getattr(user, "is_authenticated", False)):
            from accounts.models import User
            try:
                user = User.objects.filter(id=profile_id).first()
            except Exception:
                user = None

        s = ConversationSession.objects.create(
            user=user if (user and getattr(user, "is_authenticated", False)) else None,
            loved_one_id=int(loved_one_id),
            channel=ConversationSession.CHANNEL_VOICE,
        )

        # Voice call started: update LovedOne conversation timestamp.
        from .models import LovedOne
        LovedOne.objects.filter(id=int(loved_one_id)).update(last_conversation_at=timezone.now())

        return int(s.id)

    @database_sync_to_async
    def _db_check_talk_time_limit(self, profile_id: str = "") -> tuple[bool, str]:
        """
        Checks if the user has exceeded their plan's talk time limit.
        Returns (is_allowed, error_message).
        """
        user = getattr(self, "scope", {}).get("user", None)
        if not (user and getattr(user, "is_authenticated", False)):
            if profile_id:
                from accounts.models import User
                try:
                    user = User.objects.filter(id=profile_id).first()
                except Exception:
                    user = None

        if not user or not getattr(user, "is_authenticated", False):
            return True, ""

        from accounts.models import UserSubscription, SubscriptionTalkTimeUsage
        from django.db.models import Sum

        subscription = UserSubscription.objects.filter(user=user, is_active=True).select_related("plan").first()
        if not subscription or not subscription.plan:
            return True, ""

        limit = subscription.plan.talk_time_limit
        if limit and limit > 0:
            used = SubscriptionTalkTimeUsage.objects.filter(subscription=subscription).aggregate(total=Sum("duration"))["total"] or 0
            if used >= limit:
                return False, f"Talk time limit of {limit} seconds reached for your subscription plan ({subscription.plan.name})."

        return True, ""

    @database_sync_to_async
    def _db_end_conversation_session(self, session_id: int, duration_seconds: int = 0):
        from conversations.models import ConversationSession
        from accounts.models import UserSubscription, SubscriptionTalkTimeUsage

        sid = int(session_id or 0)
        if not sid:
            return

        session = ConversationSession.objects.filter(id=sid).first()
        if not session:
            return

        session.last_activity_at = timezone.now()
        session.save(update_fields=["last_activity_at"])

        if duration_seconds > 0:
            user = session.user
            if user and getattr(user, "is_authenticated", False):
                subscription = UserSubscription.objects.filter(user=user, is_active=True).first()
                if subscription:
                    SubscriptionTalkTimeUsage.objects.create(
                        subscription=subscription,
                        session=session,
                        duration=duration_seconds,
                    )

    @database_sync_to_async
    def _db_add_message(self, session_id: int, role: str, content: str):
        """
        Stores the full message text as one row.
        """
        from conversations.models import ConversationMessage, ConversationSession

        sid = int(session_id or 0)
        if not sid:
            return

        text = (content or "").strip()
        if not text:
            return

        last_seq = (
            ConversationMessage.objects.filter(session_id=sid)
            .order_by("-seq")
            .values_list("seq", flat=True)
            .first()
        )
        seq = int(last_seq or 0) + 1

        ConversationMessage.objects.create(
            session_id=sid,
            role=(role or "user"),
            content=text,
            seq=seq,
        )
        ConversationSession.objects.filter(id=sid).update(last_activity_at=timezone.now())

        # Any voice message turn should refresh LovedOne's last conversation timestamp.
        loved_one_id = (
            ConversationSession.objects.filter(id=sid)
            .values_list("loved_one_id", flat=True)
            .first()
        )
        if loved_one_id:
            from .models import LovedOne
            LovedOne.objects.filter(id=int(loved_one_id)).update(last_conversation_at=timezone.now())

    @database_sync_to_async
    def _db_get_recent_history(self, session_id: int, max_msgs: int = 14):
        """
        Returns last N messages in chronological order: [{role, content, seq}, ...]
        """
        from conversations.models import ConversationMessage

        sid = int(session_id or 0)
        if not sid:
            return []

        qs = ConversationMessage.objects.filter(session_id=sid).order_by("-seq")[: int(max_msgs)]
        rows = list(qs.values("role", "content", "seq"))
        rows.reverse()
        return rows

    async def _inject_recent_history_context(self):
        """
        Inject recent DB chat history as a system message before response.create.
        This lets the model use your stored conversation as memory/context.
        """
        if self._openai_ws is None:
            return

        sid = int(getattr(self, "_conv_session_id", 0) or 0)
        if not sid:
            return

        max_msgs = int(os.getenv("HISTORY_MAX_MSGS", "14"))
        rows = await self._db_get_recent_history(sid, max_msgs=max_msgs)
        if not rows:
            return

        lines: List[str] = []
        for r in rows:
            role = (r.get("role") or "").strip()
            content = (r.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                lines.append(f"User: {content}")
            elif role == "assistant":
                lines.append(f"You: {content}")
            else:
                lines.append(f"{role}: {content}")

        if not lines:
            return

        history_text = (
            "RECENT CONVERSATION HISTORY (verbatim):\n"
            + "\n".join(lines[-80:])
            + "\nUse this for continuity with what was previously said.\n"
        )

        await self._send_openai(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": history_text}],
                },
            }
        )

    # ==========================================================

    async def _schedule_response_after_grace(self, snapshot: str, grace_ms: int):
        try:
            await asyncio.sleep(max(0.0, grace_ms / 1000.0))
            if self._ws_closed:
                return

            if (snapshot or "").strip() != (self._pending_transcript or "").strip():
                return

            final_text = (self._pending_transcript or "").strip()
            if not final_text:
                return

            self._pending_transcript = ""
            self._awaiting_transcript_after_stop = False

            # ADDED: store FULL user message once per turn
            try:
                await self._db_add_message(int(getattr(self, "_conv_session_id", 0) or 0), "user", final_text)
            except Exception:
                pass

            await self._inject_rag_for_turn_and_create_response(final_text)
        except asyncio.CancelledError:
            return

    def _cancel_pending_response(self):
        t = self._pending_response_task
        if t and not t.done():
            t.cancel()
        self._pending_response_task = None

    def _bump_audio_gen(self, reason: str = "") -> int:
        """
        Increment a monotonic generation counter. Frontend and backend both use this
        to ignore any stale rt.audio.delta after an interrupt/barge-in.
        """
        self._audio_gen = int(getattr(self, "_audio_gen", 0)) + 1
        if _debug_enabled() and reason:
            asyncio.create_task(
                self._send_json(
                    {
                        "type": "event",
                        "name": "audio.gen.bump",
                        "gen": self._audio_gen,
                        "reason": reason,
                    }
                )
            )
        return self._audio_gen

    async def _interrupt_now(self, reason: str):
        """
        Hard-stop any in-flight TTS + cancel any in-flight OpenAI response.
        Also bumps audio generation so the frontend can drop stale audio.
        """
        await self._cancel_tts()
        await self._cancel_openai_response()
        gen = self._bump_audio_gen(reason)
        await self._send_json({"type": "ai.interrupt", "gen": gen, "reason": reason})
        # Explicit end marker so frontend can flush immediately
        await self._send_json({"type": "rt.audio.end", "gen": gen})

    async def connect(self):
        self._ws_closed = False
        await self.accept()
        await self._send_json({"type": "session.connecting"})

        self.cfg = SessionCfg()
        self.rag = get_rag()

        # Authenticated user from TokenAuthMiddleware (?access_token=…)
        self._user = self.scope.get("user", None)
        print(f"WebSocket connection from user: {self._user} (authenticated: {getattr(self._user, 'is_authenticated', False)})")

        self._audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._openai_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._task_out: Optional[asyncio.Task] = None
        self._task_in: Optional[asyncio.Task] = None
        self._tts_task: Optional[asyncio.Task] = None

        # generation counter to invalidate stale TTS audio after barge-in / interrupt
        self._audio_gen: int = 0

        self._last_user_transcript: str = ""
        self._last_assistant_text: str = ""
        self._memory_job_last_ts: float = 0.0
        self._ai_started: bool = False

        self._response_in_flight: bool = False

        self._mic_rms: float = 0.0
        self._mic_rms_ts: float = 0.0

        self._user_speaking: bool = False
        self._pending_transcript: str = ""
        self._pending_response_task: Optional[asyncio.Task] = None

        # faster default; override via env END_OF_TURN_GRACE_MS if you want
        self._end_of_turn_grace_ms: int = int(os.getenv("END_OF_TURN_GRACE_MS", "450"))

        self._last_transcript_ts: float = 0.0

        self._awaiting_transcript_after_stop: bool = False
        self._speech_stopped_ts: float = 0.0

        # track last barge-in time
        self._barge_in_ts: float = 0.0

        # ADDED: conversation session id holder & session start time
        self._conv_session_id: int = 0
        self._session_start_time = timezone.now()

        await self._send_json({"type": "session.ready"})

    async def disconnect(self, close_code):
        self._ws_closed = True

        t = getattr(self, "_pending_response_task", None)
        if t and not t.done():
            t.cancel()
        self._pending_response_task = None

        duration_seconds = 0
        if getattr(self, "_session_start_time", None) is not None:
            duration_seconds = max(0, int((timezone.now() - self._session_start_time).total_seconds()))

        # ADDED: end DB conversation session and record talk time usage
        try:
            await self._db_end_conversation_session(
                int(getattr(self, "_conv_session_id", 0) or 0),
                duration_seconds=duration_seconds,
            )
        except Exception:
            pass

        await self._cancel_tts()
        await self._shutdown_openai()

    async def _cancel_tts(self):
        t = self._tts_task
        if t and not t.done():
            t.cancel()
        self._tts_task = None

    async def _cancel_openai_response(self):
        if self._response_in_flight:
            await self._send_openai({"type": "response.cancel"})
        self._response_in_flight = False
        self._ai_started = False
        self._last_assistant_text = ""

    async def receive(self, text_data=None, bytes_data=None):
        if self._ws_closed:
            return

        if bytes_data is not None:
            if self.cfg.ptt_enabled and (not self.cfg.ptt_down):
                return
            try:
                try:
                    rms_i16 = audioop.rms(bytes_data, 2)
                    self._mic_rms = (0.85 * self._mic_rms) + (0.15 * (rms_i16 / 32768.0))
                    self._mic_rms_ts = asyncio.get_running_loop().time()
                except Exception:
                    pass

                self._audio_q.put_nowait(bytes_data)
            except asyncio.QueueFull:
                await self._send_json({"type": "warn", "note": "audio_queue_full_drop"})
            return

        if not text_data:
            return

        try:
            content = json.loads(text_data)
        except Exception:
            await self._send_json({"type": "error", "error": "invalid_json"})
            return

        mtype = content.get("type")

        if mtype == "session.start":
            # Prefer authenticated user; fall back to profile_id from payload
            user = getattr(self, "_user", None)
            print(f"Session start from user: {user} (authenticated: {getattr(user, 'is_authenticated', False)})")
            if user and getattr(user, "is_authenticated", False):
                self.cfg.profile_id = str(user.id)
            else:
                self.cfg.profile_id = (content.get("profile_id") or "default").strip()

            self.cfg.loved_one_id = int(content.get("loved_one_id") or 0)
            print(f"Session config: profile_id={self.cfg.profile_id}, loved_one_id={self.cfg.loved_one_id}")
            if not self.cfg.loved_one_id:
                await self._send_json({"type": "error", "error": "loved_one_id is required"})
                return

            # Check talk time limit before starting session
            allowed, err_msg = await self._db_check_talk_time_limit(self.cfg.profile_id)
            if not allowed:
                await self._send_json(
                    {
                        "type": "error",
                        "error": "talk_time_limit_exceeded",
                        "detail": err_msg,
                    }
                )
                return

            self._apply_config(content)

            ok = await self._load_persona_from_db(self.cfg.profile_id, self.cfg.loved_one_id)
            if not ok:
                await self._send_json({"type": "error", "error": "loved_one not found"})
                return

            if not (self.cfg.eleven_voice_id or "").strip():
                await self._send_json(
                    {
                        "type": "error",
                        "error": "no_cloned_voice",
                        "detail": "This Loved One has no cloned ElevenLabs voice yet. Upload voice samples first and wait for cloning to complete.",
                    }
                )
                return

            # ADDED: create DB conversation session (once per websocket session)
            try:
                self._conv_session_id = await self._db_create_conversation_session(self.cfg.profile_id, self.cfg.loved_one_id)
            except Exception:
                self._conv_session_id = 0

            # Reset session start time to now when voice session starts
            self._session_start_time = timezone.now()

            await self._send_json(
                {
                    "type": "session.started",
                    "profile_id": self.cfg.profile_id,
                    "loved_one_id": self.cfg.loved_one_id,
                    "conv_session_id": self._conv_session_id,
                    "authenticated": bool(user and getattr(user, "is_authenticated", False)),
                }
            )
            await self._startup_openai()
            return

        if mtype == "session.config":
            self._apply_config(content)
            await self._send_json(
                {
                    "type": "event",
                    "name": "session.config.ok",
                    "cfg": {
                        "vad_silence_ms": self.cfg.vad_silence_ms,
                        "vad_threshold": self.cfg.vad_threshold,
                        "ptt_enabled": self.cfg.ptt_enabled,
                    },
                }
            )
            await self._send_openai_session_update()
            return

        if mtype == "ptt.down":
            self.cfg.ptt_down = True
            await self._send_json({"type": "event", "name": "ptt.down"})
            return

        if mtype == "ptt.up":
            self.cfg.ptt_down = False
            await self._send_json({"type": "event", "name": "ptt.up"})
            return

        if mtype == "ai.cut_audio":
            await self._interrupt_now("client.cut_audio")
            return

    @database_sync_to_async
    def _load_persona_from_db(self, profile_id: str, loved_one_id: int) -> bool:
        from .models import LovedOne
        print(f"Loading persona from DB for profile_id: {profile_id}, loved_one_id: {loved_one_id}")
        # filt, _profile_key = _db_filter_from_profile_id(profile_id)
        filt = {"user_id": profile_id}
        print(f"Loading persona from DB with filter: {filt}, loved_one_id: {loved_one_id}")
        lo = LovedOne.objects.filter(**filt, id=loved_one_id).first()
        print(f"DB query result for LovedOne: {lo}")
        if not lo:
            return False

        self.cfg.loved_one_name = (lo.name or "").strip()
        self.cfg.loved_one_relationship = (lo.relationship or "").strip()
        self.cfg.loved_one_nickname_for_user = (lo.nickname_for_user or "").strip()
        self.cfg.loved_one_speaking_style = (lo.speaking_style or "").strip()
        self.cfg.eleven_voice_id = (getattr(lo, "eleven_voice_id", "") or "").strip()

        # New model fields (safe even if empty)
        self.cfg.catch_phrase = (getattr(lo, "catch_phrase", "") or "").strip()
        self.cfg.description = (getattr(lo, "description", "") or "").strip()
        self.cfg.core_memories = (getattr(lo, "core_memories", "") or "").strip()

        return True

    async def _startup_openai(self):
        if self._openai_ws is not None:
            return

        api_key = settings.VOICE_APP.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            await self._send_json({"type": "error", "error": "OPENAI_API_KEY missing"})
            return

        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            self._openai_ws = await websockets.connect(
                OPENAI_REALTIME_URL,
                additional_headers=headers,
                max_size=20 * 1024 * 1024,
            )
        except TypeError:
            self._openai_ws = await websockets.connect(
                OPENAI_REALTIME_URL,
                extra_headers=headers,
                max_size=20 * 1024 * 1024,
            )

        await self._send_json({"type": "event", "name": "openai.ws.connected"})
        await self._send_openai_session_update(initial=True)
        await self._send_openai_system_prompt()

        self._task_out = asyncio.create_task(self._pump_audio_to_openai())
        self._task_in = asyncio.create_task(self._pump_events_from_openai())

    async def _shutdown_openai(self):
        for t in [self._task_out, self._task_in]:
            if t and not t.done():
                t.cancel()
        self._task_out = None
        self._task_in = None

        if self._openai_ws is not None:
            try:
                await self._openai_ws.close()
            except Exception:
                pass
            self._openai_ws = None

    async def _send_openai(self, event: dict):
        if self._openai_ws is None:
            return
        try:
            await self._openai_ws.send(json.dumps(event))
        except Exception:
            await self._send_json({"type": "warn", "note": "openai_send_failed"})
            await self._shutdown_openai()

    async def _send_openai_session_update(self, initial: bool = False):
        if self._openai_ws is None:
            return

        session_update = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "output_modalities": ["text"],
                "instructions": (
                    "Always respond in English only.\n"
                    "Make this feel like real conversation.\n"
                    "Keep wording plain and natural.\n"
                ),
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "noise_reduction": {"type": "near_field"},
                        "transcription": {
                            "model": settings.VOICE_APP.get("OPENAI_RT_TRANSCRIBE_MODEL", "gpt-4o-transcribe"),
                            "language": "en",
                            "prompt": "Transcribe in English.",
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": float(self.cfg.vad_threshold),
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": int(self.cfg.vad_silence_ms),
                            "create_response": False,
                            "interrupt_response": True,
                        },
                    },
                },
            },
        }

        await self._send_openai(session_update)
        await self._send_json(
            {
                "type": "event",
                "name": "openai.session.update.sent",
                "cfg": {
                    "vad_silence_ms": self.cfg.vad_silence_ms,
                    "vad_threshold": self.cfg.vad_threshold,
                    "initial": initial,
                    "output": "text",
                },
            }
        )

    async def _send_openai_system_prompt(self):
        try:
            # normalize profile_id for RAG filters
            profile_key = self.cfg.profile_id

            rag = self.rag.query(
                profile_id=profile_key,
                loved_one_id=self.cfg.loved_one_id,
                query_text="session_bootstrap",
                k=5,
            )
            memories = "\n".join(f"- {d}" for d in rag.docs) if getattr(rag, "docs", None) else "(none)"
        except Exception as e:
            memories = f"(rag error: {type(e).__name__}: {e})"

        persona_lines = []
        if self.cfg.loved_one_name:
            persona_lines.append(f"Name: {self.cfg.loved_one_name}")
        if self.cfg.loved_one_relationship:
            persona_lines.append(f"Relationship: {self.cfg.loved_one_relationship}")
        if self.cfg.loved_one_nickname_for_user:
            persona_lines.append(
                "Nickname for the user (use occasionally, not every reply): "
                f"{self.cfg.loved_one_nickname_for_user}"
            )
        if self.cfg.loved_one_speaking_style:
            persona_lines.append(
                "Tone guidance (apply subtly; do not repeat adjectives/labels): "
                f"{self.cfg.loved_one_speaking_style}"
            )

        # NEW fields in persona
        if self.cfg.catch_phrase:
            persona_lines.append(f"Catch phrase (use rarely): {self.cfg.catch_phrase}")
        if self.cfg.description:
            persona_lines.append(f"Description: {self.cfg.description}")
        if self.cfg.core_memories:
            persona_lines.append("Core memories (high priority, treat as true): " + self.cfg.core_memories)

        persona_block = "\n".join(persona_lines) if persona_lines else "(not provided)"

        ctx = PromptContext(
            profile_id=self.cfg.profile_id,
            loved_one_id=self.cfg.loved_one_id,
            persona_block=persona_block,
            memories_block=memories,
        )
        system_text = build_system_prompt(ctx)

        await self._send_openai(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_text}],
                },
            }
        )
        await self._send_json({"type": "event", "name": "openai.system_prompt.sent"})

    async def _inject_rag_for_turn_and_create_response(self, transcript: str):
        if self._openai_ws is None:
            return

        t = (transcript or "").strip()
        if self._looks_like_noise(t):
            await self._send_json({"type": "event", "name": "rag.skip_noise", "text": t})
            return

        self._last_user_transcript = t

        # ADDED: inject recent DB conversation history as context
        try:
            await self._inject_recent_history_context()
        except Exception:
            pass

        try:
            profile_key = self.cfg.profile_id

            rag = self.rag.query(
                profile_id=profile_key,
                loved_one_id=self.cfg.loved_one_id,
                query_text=t,
                k=6,
            )
            docs = rag.docs or []
        except Exception as e:
            docs = [f"(rag error: {type(e).__name__}: {e})"]

        max_total_chars = 1400
        picked = []
        total = 0
        for d in docs:
            d = (d or "").strip()
            if not d:
                continue
            d = self._truncate(d, 320)
            if total + len(d) > max_total_chars:
                break
            picked.append(d)
            total += len(d)

        if picked:
            context_text = (
                "CONTEXT (relevant memories for replying to the user's latest message):\n"
                + "\n".join(f"- {x}" for x in picked)
                + "\n"
                "Use these as first-person memories. If not relevant, ignore.\n"
            )
            await self._send_openai(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "system",
                        "content": [{"type": "input_text", "text": context_text}],
                    },
                }
            )

        reply_style = build_reply_instructions(t)

        self._ai_started = True
        self._response_in_flight = True
        self._last_assistant_text = ""
        gen = self._bump_audio_gen("ai.text.start")
        await self._send_json({"type": "ai.text.start", "gen": gen})
        await self._send_openai({"type": "response.create", "response": {"instructions": reply_style}})

    async def _pump_audio_to_openai(self):
        assert self._openai_ws is not None
        while not self._ws_closed and self._openai_ws is not None:
            try:
                chunk = await self._audio_q.get()
            except asyncio.CancelledError:
                return
            b64 = base64.b64encode(chunk).decode("ascii")
            await self._send_openai({"type": "input_audio_buffer.append", "audio": b64})

    async def _fire_auto_memory(self, assistant_text: str, from_event: str):
        await self._send_json(
            {
                "type": "event",
                "name": "memory.checkpoint.turn_done",
                "has_user": bool(self._last_user_transcript),
                "assistant_len": len(assistant_text or ""),
                "from_event": from_event,
            }
        )
        if self._last_user_transcript and assistant_text:
            asyncio.create_task(self._auto_memory_after_turn(self._last_user_transcript, assistant_text))

    async def _auto_memory_after_turn(self, user_text: str, assistant_text: str):
        await self._send_json({"type": "event", "name": "memory.checkpoint.job_started"})

        if not settings.VOICE_APP.get("AUTO_MEMORY_ENABLED", True):
            await self._send_json({"type": "event", "name": "memory.checkpoint.disabled"})
            return

        now = asyncio.get_running_loop().time()
        min_interval = float(settings.VOICE_APP.get("MEMORY_EXTRACT_MIN_INTERVAL_SEC", 12))
        if now - self._memory_job_last_ts < min_interval:
            await self._send_json({"type": "event", "name": "memory.checkpoint.rate_limited"})
            return
        self._memory_job_last_ts = now

        always = bool(settings.VOICE_APP.get("MEMORY_ALWAYS_EXTRACT", False))
        if (not always) and (not heuristic_gate(user_text)):
            await self._send_json({"type": "event", "name": "memory.checkpoint.gated"})
            return

        api_key = settings.VOICE_APP.get("OPENAI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        model = settings.VOICE_APP.get("OPENAI_MEMORY_MODEL", "gpt-4o-mini")
        max_items = int(settings.VOICE_APP.get("MEMORY_EXTRACT_MAX_ITEMS", 3))

        try:
            memories = await extract_memories_via_openai(
                api_key=api_key,
                model=model,
                user_text=user_text,
                assistant_text=assistant_text,
                max_items=max_items,
            )
        except Exception as e:
            await self._send_json({"type": "warn", "note": f"memory.extract.failed: {type(e).__name__}: {e}"})
            return

        await self._send_json({"type": "event", "name": "memory.checkpoint.extracted", "count": len(memories)})
        if not memories:
            return

        existing = set()
        try:
            profile_key = self.cfg.profile_id
            recent = self.rag.query(
                profile_id=profile_key,
                loved_one_id=self.cfg.loved_one_id,
                query_text=user_text,
                k=10,
            ).docs
            existing = set((d or "").strip().lower() for d in (recent or []))
        except Exception:
            existing = set()

        for m in memories:
            text = (m.text or "").strip()
            if not text:
                continue
            if text.lower() in existing:
                await self._send_json({"type": "event", "name": "memory.checkpoint.duplicate_skipped"})
                continue
            await self._save_memory_to_db_and_rag(self.cfg.profile_id, self.cfg.loved_one_id, text)

    @database_sync_to_async
    def _db_create_memory(self, profile_id: str, loved_one_id: int, text: str) -> str:
        """
        New models.py: Memory model removed.
        Persist memory by appending to LovedOne.core_memories.
        Return a generated memory_id for RAG.
        """
        import uuid
        from .models import LovedOne

        filt = {"user_id": profile_id}  # normalize profile_id for DB query
        lo = LovedOne.objects.filter(**filt, id=loved_one_id).first()
        if not lo:
            raise ValueError("loved_one not found")

        existing = (getattr(lo, "core_memories", "") or "").strip()
        combined = (existing + "\n" + (text or "").strip()).strip() if existing else (text or "").strip()
        lo.core_memories = combined

        try:
            lo.save(update_fields=["core_memories"])
        except Exception:
            lo.save()

        return uuid.uuid4().hex

    async def _save_memory_to_db_and_rag(self, profile_key: str, loved_one_id: int, text: str):
        memory_id = await self._db_create_memory(profile_key, loved_one_id, text)

        self.rag.add_memory(profile_id=profile_key, loved_one_id=loved_one_id, text=text, memory_id=str(memory_id))
        await self._send_json({"type": "event", "name": "memory.auto.saved", "memory_id": str(memory_id)})

    async def _speak_elevenlabs(self, text: str, gen: int):
        await self._send_json({"type": "event", "name": "tts.elevenlabs.start", "gen": gen})

        try:
            voice_id = (self.cfg.eleven_voice_id or "").strip()
            api_key = settings.VOICE_APP.get("ELEVENLABS_API_KEY") or os.getenv("ELEVENLABS_API_KEY", "")
            model_id = settings.VOICE_APP.get("ELEVENLABS_MODEL_ID") or ""

            swap_endian = (os.getenv("ELEVENLABS_PCM_SWAP_ENDIAN", "0") == "1")

            if not api_key:
                await self._send_json({"type": "warn", "note": "elevenlabs_api_key_missing_no_audio"})
                await self._send_json({"type": "rt.audio.end", "gen": gen})
                return

            if not voice_id:
                await self._send_json({"type": "warn", "note": "no_cloned_voice_id_no_audio"})
                await self._send_json({"type": "rt.audio.end", "gen": gen})
                return

            stream_output_format = "pcm_24000"
            fallback_output_format = "pcm_24000"
            pcm_rate = 24000

            disable_chunking = os.getenv("TTS_DISABLE_CHUNKING", "0") == "1"
            cadence_mode = "full" if disable_chunking else "chunk+silence"

            cfg = ElevenLabsTTSConfig(
                api_key=api_key,
                voice_id=voice_id,
                model_id=model_id,
                stream_output_format=stream_output_format,
                fallback_output_format=fallback_output_format,
                mp3_output_format=os.getenv("ELEVENLABS_MP3_OUTPUT_FORMAT", "mp3_44100_128"),
                timeout_sec=float(os.getenv("ELEVENLABS_TTS_TIMEOUT_SEC", "60")),
                speed=float(os.getenv("ELEVENLABS_TTS_SPEED", "0.90")),
            )
            tts = ElevenLabsTTS(cfg, swap_endian=swap_endian)

            if disable_chunking:
                chunks = [(_normalize_text_for_tts(text), 0.0)]
                inter_chunk_pause = 0.0
            else:
                chunks = _chunk_text_for_cadence(
                    text,
                    max_words_per_chunk=int(os.getenv("TTS_MAX_WORDS_PER_CHUNK", "10")),
                )
                inter_chunk_pause = float(os.getenv("TTS_INTER_CHUNK_PAUSE_SEC", "0.08"))

            for chunk_text, pause_after in chunks:
                if self._ws_closed:
                    return
                if gen != int(getattr(self, "_audio_gen", 0)):
                    return
                if not (chunk_text or "").strip():
                    continue

                async for pcm_chunk in tts.stream_pcm(chunk_text):
                    if self._ws_closed:
                        return
                    if gen != int(getattr(self, "_audio_gen", 0)):
                        return
                    b64 = base64.b64encode(pcm_chunk).decode("ascii")
                    await self._send_json({"type": "rt.audio.delta", "audio_b64": b64, "gen": gen})

                total_pause = max(0.0, inter_chunk_pause + float(pause_after))
                sil = _silence_pcm16(total_pause, sample_rate=pcm_rate)
                if sil:
                    frame = 4096
                    for i in range(0, len(sil), frame):
                        if self._ws_closed:
                            return
                        b64 = base64.b64encode(sil[i : i + frame]).decode("ascii")
                        if gen != int(getattr(self, "_audio_gen", 0)):
                            return
                        await self._send_json({"type": "rt.audio.delta", "audio_b64": b64, "gen": gen})
                        await asyncio.sleep(0)

            await self._send_json({"type": "rt.audio.end", "gen": gen})

        except asyncio.CancelledError:
            await self._send_json({"type": "rt.audio.end", "gen": gen})
            raise
        except Exception as e:
            await self._send_json({"type": "warn", "note": f"tts.elevenlabs.failed: {type(e).__name__}: {e}"})
            await self._send_json({"type": "rt.audio.end", "gen": gen})
        finally:
            await self._send_json({"type": "event", "name": "tts.elevenlabs.done", "gen": gen})

    async def _pump_events_from_openai(self):
        assert self._openai_ws is not None
        try:
            async for raw in self._openai_ws:
                if self._ws_closed:
                    return

                try:
                    ev = json.loads(raw)
                except Exception:
                    await self._send_json({"type": "warn", "note": "openai_event_json_parse_failed"})
                    continue

                et = ev.get("type", "")
                await self._send_json({"type": "event", "name": "openai.event", "openai_type": et})

                if et in ("error", "invalid_request_error"):
                    await self._send_json({"type": "error", "error": ev})
                    continue

                if et == "input_audio_buffer.speech_started":
                    self._user_speaking = True
                    self._cancel_pending_response()
                    self._awaiting_transcript_after_stop = False

                    tts_playing = bool(self._tts_task and (not self._tts_task.done()))
                    ai_in_flight = bool(self._response_in_flight)

                    if not (tts_playing or ai_in_flight):
                        continue

                    if self.cfg.ptt_enabled and (not self.cfg.ptt_down):
                        continue

                    thr = float(os.getenv("BARGE_IN_RMS_THRESHOLD", "0.09"))

                    # Mark barge-in time to speed up the follow-up response
                    self._barge_in_ts = asyncio.get_running_loop().time()

                    if thr <= 0.0:
                        continue

                    now = asyncio.get_running_loop().time()
                    recent = (now - getattr(self, "_mic_rms_ts", 0.0)) <= 0.80
                    loud = getattr(self, "_mic_rms", 0.0) >= thr

                    if recent and loud:
                        await self._interrupt_now("barge_in")
                    continue

                if et == "input_audio_buffer.speech_stopped":
                    self._user_speaking = False
                    self._speech_stopped_ts = asyncio.get_running_loop().time()

                    pending = (self._pending_transcript or "").strip()
                    if pending:
                        self._cancel_pending_response()
                        grace_ms = self._compute_grace_ms(pending)
                        snapshot = pending
                        self._pending_response_task = asyncio.create_task(
                            self._schedule_response_after_grace(snapshot, grace_ms)
                        )
                    else:
                        self._awaiting_transcript_after_stop = True
                    continue

                if et == "conversation.item.input_audio_transcription.completed":
                    transcript = (ev.get("transcript") or "").strip()
                    if transcript:
                        await self._send_json({"type": "stt.text", "text": transcript})

                    if transcript:
                        if self._pending_transcript:
                            self._pending_transcript = (self._pending_transcript + " " + transcript).strip()
                        else:
                            self._pending_transcript = transcript

                    if (not self._user_speaking) and (self._pending_transcript or "").strip():
                        now = asyncio.get_running_loop().time()
                        recently_stopped = (now - getattr(self, "_speech_stopped_ts", 0.0)) <= 2.5

                        if self._awaiting_transcript_after_stop or recently_stopped:
                            self._awaiting_transcript_after_stop = False
                            self._cancel_pending_response()
                            pending = (self._pending_transcript or "").strip()
                            grace_ms = self._compute_grace_ms(pending)
                            snapshot = pending
                            self._pending_response_task = asyncio.create_task(
                                self._schedule_response_after_grace(snapshot, grace_ms)
                            )
                    continue

                if et in ("response.output_text.delta", "response.text.delta"):
                    delta = ev.get("delta") or ""
                    if delta:
                        if not self._ai_started:
                            self._ai_started = True
                            self._last_assistant_text = ""
                            gen = self._bump_audio_gen("ai.text.start.delta")
                            await self._send_json({"type": "ai.text.start", "gen": gen})
                        self._last_assistant_text += delta
                        await self._send_json({"type": "ai.text.delta", "delta": delta})
                    continue

                if et in ("response.output_text.done", "response.text.done"):
                    text = (ev.get("text") or self._last_assistant_text or "").strip()
                    self._last_assistant_text = text
                    self._response_in_flight = False
                    self._ai_started = False

                    # ADDED: store FULL assistant reply once per turn
                    try:
                        await self._db_add_message(int(getattr(self, "_conv_session_id", 0) or 0), "assistant", text)
                    except Exception:
                        pass

                    await self._send_json({"type": "ai.text.final", "text": text})
                    await self._fire_auto_memory(text, et)

                    await self._cancel_tts()
                    if text:
                        gen = int(getattr(self, "_audio_gen", 0))
                        self._tts_task = asyncio.create_task(self._speak_elevenlabs(text, gen))
                    else:
                        gen2 = int(getattr(self, "_audio_gen", 0))
                        await self._send_json({"type": "rt.audio.end", "gen": gen2})
                    continue

        except asyncio.CancelledError:
            return
        except Exception as e:
            await self._send_json({"type": "warn", "note": f"openai_ws_reader_error: {type(e).__name__}: {e}"})
        finally:
            await self._shutdown_openai()


class VoiceChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for text-based voice chat.
    Connects at ws/voice/chat/text/stream/<loved_one_id>/
    Similar behavior to voice_chat_text_stream in views.py

    Stream cancellation
    -------------------
    Streaming runs in a background asyncio.Task (_stream_task) so that
    receive() remains free to accept a concurrent "stream.stop" message.
    When "stream.stop" arrives the task is cancelled, partial assistant
    text is persisted to the DB, and a "stream.stopped" acknowledgement
    is sent to the frontend.  The consumer is then fully ready to handle
    new text messages.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self):
        self.loved_one_id = self.scope['url_route']['kwargs'].get('loved_one_id')
        self.user = self.scope.get("user")
        self.profile_id = str(self.user.id) if self.user and self.user.is_authenticated else "default"
        self._session_start_time = timezone.now()

        if not self.loved_one_id:
            await self.close(code=4000)
            return

        exists = await self._check_loved_one_exists()
        if not exists:
            await self.close(code=4004)
            return

        # Check talk time limit
        allowed, err_msg = await self._check_talk_time_limit()
        if not allowed:
            await self.accept()
            await self.send(json.dumps({"type": "error", "error": "talk_time_limit_exceeded", "message": err_msg}))
            await self.close(code=4003)
            return

        # Per-connection streaming state
        self._stream_task: Optional[asyncio.Task] = None
        self._stop_streaming: bool = False
        # Holds accumulated assistant text for the in-flight stream so that
        # _stop_stream_handler() can persist it even when it interrupts early.
        self._current_partial_text: str = ""
        self._current_conv_session_id: int = 0

        await self.accept()

    async def disconnect(self, close_code):
        """Cancel any in-flight stream task and record talk time usage on disconnect."""
        await self._cancel_stream_task()

        duration_seconds = 0
        if getattr(self, "_session_start_time", None) is not None:
            duration_seconds = max(0, int((timezone.now() - self._session_start_time).total_seconds()))

        conv_session_id = getattr(self, "_current_conv_session_id", 0)
        if conv_session_id:
            try:
                await self._save_talk_time_usage(conv_session_id, duration_seconds)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _cancel_stream_task(self):
        """Cancel _stream_task and await it silently."""
        task = getattr(self, "_stream_task", None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._stream_task = None

    @database_sync_to_async
    def _check_loved_one_exists(self):
        from .models import LovedOne
        return LovedOne.objects.filter(id=self.loved_one_id).exists()

    @database_sync_to_async
    def _check_talk_time_limit(self):
        if not self.user or not getattr(self.user, "is_authenticated", False):
            return True, ""

        from accounts.models import UserSubscription, SubscriptionTalkTimeUsage
        from django.db.models import Sum

        subscription = UserSubscription.objects.filter(user=self.user, is_active=True).select_related("plan").first()
        if not subscription or not subscription.plan:
            return True, ""

        limit = subscription.plan.talk_time_limit
        if limit and limit > 0:
            used = SubscriptionTalkTimeUsage.objects.filter(subscription=subscription).aggregate(total=Sum("duration"))["total"] or 0
            if used >= limit:
                return False, f"Talk time limit of {limit} seconds reached for your subscription plan ({subscription.plan.name})."

        return True, ""

    @database_sync_to_async
    def _save_talk_time_usage(self, session_id: int, duration_seconds: int):
        from conversations.models import ConversationSession
        from accounts.models import UserSubscription, SubscriptionTalkTimeUsage

        sid = int(session_id or 0)
        if not sid:
            return

        session = ConversationSession.objects.filter(id=sid).first()
        if not session:
            return

        session.last_activity_at = timezone.now()
        session.save(update_fields=["last_activity_at"])

        if duration_seconds > 0:
            user = session.user or (self.user if (self.user and getattr(self.user, "is_authenticated", False)) else None)
            if user:
                subscription = UserSubscription.objects.filter(user=user, is_active=True).first()
                if subscription:
                    SubscriptionTalkTimeUsage.objects.create(
                        subscription=subscription,
                        session=session,
                        duration=duration_seconds,
                    )

    # ------------------------------------------------------------------
    # receive() – entry point for all incoming WebSocket messages
    # ------------------------------------------------------------------

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except Exception:
            await self.send(json.dumps({"type": "error", "message": "Invalid JSON"}))
            return

        mtype = data.get("type", "")

        # ----------------------------------------------------------------
        # "stream.stop" – cancel any in-flight streaming task
        # ----------------------------------------------------------------
        if mtype == "stream.stop":
            await self._stop_stream_handler()
            return

        # ----------------------------------------------------------------
        # Default: a new text message to stream a reply for
        # ----------------------------------------------------------------
        text = (data.get("text") or "").strip()
        session_id = data.get("session_id")

        if not text:
            await self.send(json.dumps({"type": "error", "message": "text is required"}))
            return

        # If a previous stream is still running, cancel it cleanly first
        if self._stream_task and not self._stream_task.done():
            await self._cancel_stream_task()

        # Reset per-stream state
        self._stop_streaming = False
        self._current_partial_text = ""
        self._current_conv_session_id = 0

        # Prepare session and build prompt
        prep = await self._prepare_session_and_prompt(text, session_id)
        if not prep:
            await self.send(json.dumps({"type": "error", "message": "Failed to prepare session or loved_one not found"}))
            return

        conv_session_id, loved_one_name, system_prompt = prep
        self._current_conv_session_id = conv_session_id

        # Get ElevenLabs single-use token for the frontend
        eleven_token = await self._get_elevenlabs_token()

        # Send stream.start event
        await self.send(json.dumps({
            "type": "stream.start",
            "session_id": conv_session_id,
            "loved_one_name": loved_one_name,
            "eleven_token": eleven_token
        }))

        # Launch streaming in a background task so receive() stays free
        # to handle a concurrent "stream.stop" message.
        self._stream_task = asyncio.create_task(
            self._run_stream(text, system_prompt, conv_session_id)
        )

    # ------------------------------------------------------------------
    # Stream stop handler
    # ------------------------------------------------------------------

    async def _stop_stream_handler(self):
        """
        Handle a "stream.stop" event from the frontend.
        Cancels the in-flight streaming task, persists whatever partial
        assistant text has been accumulated so far, and sends a
        "stream.stopped" acknowledgement.
        """
        # Snapshot and clear the partial text *before* cancelling the task
        # to avoid a race with the streaming loop's own writes.
        partial_text = self._current_partial_text
        conv_session_id = self._current_conv_session_id

        self._stop_streaming = True  # signal the inner loop to exit
        await self._cancel_stream_task()

        # Persist partial text to keep conversation history consistent
        if partial_text and conv_session_id:
            try:
                await self._save_assistant_message(conv_session_id, partial_text)
            except Exception:
                pass

        # Reset state so subsequent messages work normally
        self._stop_streaming = False
        self._current_partial_text = ""
        self._current_conv_session_id = 0

        await self.send(json.dumps({"type": "stream.stopped"}))

    # ------------------------------------------------------------------
    # Background streaming task
    # ------------------------------------------------------------------

    async def _run_stream(self, text: str, system_prompt: str, conv_session_id: int):
        """
        Background task that drives the LLM stream and forwards sentences
        to the frontend.  Checks self._stop_streaming after every queue
        read so it exits promptly when the frontend sends "stream.stop".
        """
        full_assistant_text = ""
        sentence_buffer = ""
        sentence_idx = 0

        try:
            # Bridge the sync generator to the async world via a queue
            queue: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def producer():
                try:
                    for delta in stream_reply(system_prompt=system_prompt, user_text=text):
                        loop.call_soon_threadsafe(queue.put_nowait, delta)
                    loop.call_soon_threadsafe(queue.put_nowait, None)  # EOF sentinel
                except Exception as exc:
                    loop.call_soon_threadsafe(queue.put_nowait, exc)

            asyncio.create_task(asyncio.to_thread(producer))

            while True:
                # Check stop flag before each read
                if self._stop_streaming:
                    return

                item = await queue.get()

                # Check stop flag immediately after unblocking
                if self._stop_streaming:
                    return

                if item is None:  # EOF
                    break
                if isinstance(item, Exception):
                    raise item

                delta: str = item
                full_assistant_text += delta
                # Keep the shared partial text in sync so _stop_stream_handler
                # can read it at any point during streaming.
                self._current_partial_text = full_assistant_text
                sentence_buffer += delta

                # Flush completed sentences to the frontend
                if any(t in sentence_buffer for t in ('. ', '! ', '? ', '.\n', '!\n', '?\n')):
                    parts = self._split_into_sentences(sentence_buffer)
                    if len(parts) > 1:
                        for i in range(len(parts) - 1):
                            if self._stop_streaming:
                                return
                            s = parts[i].strip()
                            if s:
                                await self.send(json.dumps({
                                    "type": "stream.sentence",
                                    "sentence": s,
                                    "index": sentence_idx
                                }))
                                sentence_idx += 1
                        sentence_buffer = parts[-1]

            # Flush any remaining buffer
            if sentence_buffer.strip() and not self._stop_streaming:
                parts = self._split_into_sentences(sentence_buffer)
                for s in parts:
                    if self._stop_streaming:
                        return
                    s = s.strip()
                    if s:
                        await self.send(json.dumps({
                            "type": "stream.sentence",
                            "sentence": s,
                            "index": sentence_idx
                        }))
                        sentence_idx += 1

            if self._stop_streaming:
                return

            # Normal completion
            await self.send(json.dumps({
                "type": "stream.complete",
                "total_sentences": sentence_idx
            }))

            # Post-stream: persist and trigger memory extraction
            if full_assistant_text:
                await self._save_assistant_message(conv_session_id, full_assistant_text)

                from .views import _auto_memory_background
                asyncio.create_task(asyncio.to_thread(
                    _auto_memory_background,
                    self.profile_id,
                    int(self.loved_one_id),
                    text,
                    full_assistant_text
                ))

        except asyncio.CancelledError:
            # Task was cancelled externally (e.g. by _cancel_stream_task);
            # _stop_stream_handler is responsible for persisting partial text.
            return
        except Exception as exc:
            try:
                await self.send(json.dumps({"type": "error", "message": f"Streaming failed: {str(exc)}"}))            
            except Exception:
                pass

    def _split_into_sentences(self, text: str) -> list[str]:
        if not text:
            return []
        # Split by . ! ? followed by space or newline
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]

    @database_sync_to_async
    def _prepare_session_and_prompt(self, text, session_id):
        from .models import LovedOne
        from django.db import transaction
        from .rag_factory import get_rag
        
        try:
            lo = LovedOne.objects.filter(id=self.loved_one_id).first()
            if not lo:
                return None
            
            with transaction.atomic():
                if session_id:
                    conv_session = ConversationSession.objects.filter(id=session_id, loved_one_id=self.loved_one_id).first()
                    if not conv_session:
                        return None
                else:
                    conv_session = ConversationSession.objects.create(
                        user=self.user if self.user and self.user.is_authenticated else None,
                        loved_one_id=int(self.loved_one_id),
                        channel=ConversationSession.CHANNEL_VOICE,
                    )
                
                # Save user message
                last_seq = ConversationMessage.objects.filter(session_id=conv_session.id).order_by("-seq").values_list("seq", flat=True).first()
                seq = int(last_seq or 0) + 1
                ConversationMessage.objects.create(
                    session_id=conv_session.id,
                    role="user",
                    content=text,
                    seq=seq
                )
                
                # Update timestamps
                ConversationSession.objects.filter(id=conv_session.id).update(last_activity_at=timezone.now())
                LovedOne.objects.filter(id=int(self.loved_one_id)).update(last_conversation_at=timezone.now())

            # Build system prompt (RAG + Persona)
            rag_docs = []
            try:
                _rag = get_rag()
                rag_result = _rag.query(
                    profile_id=self.profile_id,
                    loved_one_id=int(self.loved_one_id),
                    query_text=text,
                    k=6
                )
                rag_docs = rag_result.docs or []
            except Exception:
                pass
            
            rag_context = ""
            if rag_docs:
                picked = []
                total_chars = 0
                for doc in rag_docs:
                    doc_str = (doc or "").strip()
                    if not doc_str: continue
                    doc_str = (doc_str[:320] if len(doc_str) > 320 else doc_str)
                    if total_chars + len(doc_str) > 1400: break
                    picked.append(doc_str)
                    total_chars += len(doc_str)
                if picked:
                    rag_context = "CONTEXT (relevant memories):\n" + "\n".join(f"- {x}" for x in picked) + "\n"

            persona_parts = []
            if lo.name: persona_parts.append(f"Name: {lo.name}")
            if lo.relationship: persona_parts.append(f"Relationship: {lo.relationship}")
            if lo.speaking_style: persona_parts.append(f"Speaking style: {lo.speaking_style}")
            if lo.catch_phrase: persona_parts.append(f"Catch phrase: {lo.catch_phrase}")
            if lo.description: persona_parts.append(f"Description: {lo.description}")
            persona_block = "\n".join(persona_parts) if persona_parts else "(no persona data)"
            
            memories_block = (lo.core_memories or "")
            if rag_context:
                memories_block = (memories_block + "\n\n" + rag_context).strip() if memories_block else rag_context
                
            prompt_ctx = PromptContext(
                profile_id=self.profile_id,
                loved_one_id=int(self.loved_one_id),
                persona_block=persona_block,
                memories_block=memories_block
            )
            system_prompt = build_system_prompt(prompt_ctx)
            
            return conv_session.id, lo.name, system_prompt
        except Exception:
            return None

    @database_sync_to_async
    def _save_assistant_message(self, session_id, content):
        last_seq_val = ConversationMessage.objects.filter(session_id=session_id).order_by("-seq").values_list("seq", flat=True).first()
        final_seq = int(last_seq_val or 0) + 1
        ConversationMessage.objects.create(
            session_id=session_id,
            role="assistant",
            content=content,
            seq=final_seq
        )
        ConversationSession.objects.filter(id=session_id).update(last_activity_at=timezone.now())

    async def _get_elevenlabs_token(self):
        """
        Request a single-use token from ElevenLabs to avoid exposing API key on frontend.
        """
        api_key = settings.VOICE_APP.get("ELEVENLABS_API_KEY") or os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key:
            return None
        
        # Endpoint for single-use tokens (TTS WebSocket)
        url = "https://api.elevenlabs.io/v1/single-use-token/tts_websocket"
        headers = {"xi-api-key": api_key}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("token")
                    else:
                        return None
        except Exception:
            return None
