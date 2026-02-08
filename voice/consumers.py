from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import audioop
from dataclasses import dataclass
from typing import Optional, List, Tuple

import websockets
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .rag_factory import get_rag
from .memory_auto import extract_memories_via_openai, heuristic_gate
from .providers.tts_elevenlabs import ElevenLabsTTS, ElevenLabsTTSConfig

from .prompting import PromptContext, build_system_prompt, build_reply_instructions

OPENAI_REALTIME_URL = settings.VOICE_APP.get("OPENAI_REALTIME_URL")


def _debug_enabled() -> bool:
    return os.getenv("VOICE_DEBUG", "0") == "1"


def _pcm16_stats_le(pcm_bytes: bytes) -> dict:
    if not pcm_bytes:
        return {"n": 0}

    n = len(pcm_bytes) // 2
    if n <= 0:
        return {"n": 0, "note": "odd_len"}

    mn = 32767
    mx = -32768
    s2 = 0.0

    step = max(1, n // 4000)
    count = 0

    for i in range(0, n, step):
        lo = pcm_bytes[2 * i]
        hi = pcm_bytes[2 * i + 1]
        v = (hi << 8) | lo
        if v >= 32768:
            v -= 65536

        mn = v if v < mn else mn
        mx = v if v > mx else mx
        s2 += float(v) * float(v)
        count += 1

    rms = math.sqrt(s2 / max(1, count))
    return {"n": n, "min": mn, "max": mx, "rms": round(rms, 2), "bytes": len(pcm_bytes), "step": step}


def _silence_pcm16(duration_sec: float, sample_rate: int = 24000) -> bytes:
    if duration_sec <= 0:
        return b""
    n_samples = int(sample_rate * duration_sec)
    if n_samples <= 0:
        return b""
    return b"\x00\x00" * n_samples


def _normalize_text_for_tts(t: str) -> str:
    t = (t or "").strip()
    if not t:
        return t
    t = t.replace("...", "…")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"([.?!,;:])(?=\S)", r"\1 ", t)
    return t.strip()


def _chunk_text_for_cadence(text: str, max_words_per_chunk: int = 10) -> List[Tuple[str, float]]:
    t = _normalize_text_for_tts(text)
    if not t:
        return []

    parts: List[str] = []
    buf = ""
    for ch in t:
        buf += ch
        if ch in ".?!":
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())

    out: List[Tuple[str, float]] = []

    def add(seg: str, pause: float):
        seg = (seg or "").strip()
        if seg:
            out.append((seg, pause))

    for sent in parts:
        sent = sent.strip()
        if not sent:
            continue

        phrases: List[str] = []
        pbuf = ""
        for ch in sent:
            pbuf += ch
            if ch in ",;:":
                phrases.append(pbuf.strip())
                pbuf = ""
        if pbuf.strip():
            phrases.append(pbuf.strip())

        for ph in phrases:
            words = ph.split()
            if len(words) <= max_words_per_chunk:
                end = ph[-1] if ph else ""
                if end in ",;:":
                    add(ph, 0.14)
                elif end in ".?!":
                    add(ph, 0.30)
                else:
                    add(ph, 0.18)
            else:
                for i in range(0, len(words), max_words_per_chunk):
                    seg = " ".join(words[i: i + max_words_per_chunk]).strip()
                    if i + max_words_per_chunk >= len(words):
                        if ph and ph[-1] in ".?!,;:" and seg and seg[-1] not in ".?!,;:":
                            seg = seg + ph[-1]
                    end = seg[-1] if seg else ""
                    if end in ",;:":
                        add(seg, 0.14)
                    elif end in ".?!":
                        add(seg, 0.30)
                    else:
                        add(seg, 0.16)

    if out:
        last_text, last_pause = out[-1]
        out[-1] = (last_text, min(last_pause, 0.22))

    return out


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

        await self._send_json({"type": "session.ready"})

    async def disconnect(self, close_code):
        self._ws_closed = True

        t = getattr(self, "_pending_response_task", None)
        if t and not t.done():
            t.cancel()
        self._pending_response_task = None

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
            self.cfg.profile_id = (content.get("profile_id") or "default").strip()
            self.cfg.loved_one_id = int(content.get("loved_one_id") or 0)
            if not self.cfg.loved_one_id:
                await self._send_json({"type": "error", "error": "loved_one_id is required"})
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

            await self._send_json(
                {"type": "session.started", "profile_id": self.cfg.profile_id, "loved_one_id": self.cfg.loved_one_id}
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

        lo = LovedOne.objects.filter(profile_id=profile_id, id=loved_one_id).first()
        if not lo:
            return False

        self.cfg.loved_one_name = (lo.name or "").strip()
        self.cfg.loved_one_relationship = (lo.relationship or "").strip()
        self.cfg.loved_one_nickname_for_user = (lo.nickname_for_user or "").strip()
        self.cfg.loved_one_speaking_style = (lo.speaking_style or "").strip()
        self.cfg.eleven_voice_id = (getattr(lo, "eleven_voice_id", "") or "").strip()
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
            rag = self.rag.query(
                profile_id=self.cfg.profile_id,
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

        try:
            rag = self.rag.query(
                profile_id=self.cfg.profile_id,
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
            recent = self.rag.query(
                profile_id=self.cfg.profile_id,
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
    def _db_create_memory(self, profile_id: str, loved_one_id: int, text: str) -> int:
        from .models import Memory, LovedOne

        lo = LovedOne.objects.filter(profile_id=profile_id, id=loved_one_id).first()
        if not lo:
            raise ValueError("loved_one not found")

        m = Memory.objects.create(loved_one=lo, text=text)
        return int(m.id)

    async def _save_memory_to_db_and_rag(self, profile_id: str, loved_one_id: int, text: str):
        memory_id = await self._db_create_memory(profile_id, loved_one_id, text)
        self.rag.add_memory(profile_id=profile_id, loved_one_id=loved_one_id, text=text, memory_id=str(memory_id))
        await self._send_json({"type": "event", "name": "memory.auto.saved", "memory_id": memory_id})

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
                        b64 = base64.b64encode(sil[i: i + frame]).decode("ascii")
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
