# Alyve Voice Service (Django + Realtime Voice)

This repo contains a Django backend + WebSocket voice pipeline used for:
- Creating “Loved One” profiles
- Storing memories (and indexing them in Chroma for retrieval)
- Uploading voice samples and **auto-cloning a voice in ElevenLabs**
- Running a realtime voice conversation loop over WebSockets (mic audio → OpenAI Realtime → cloned-voice TTS → streamed PCM back)

> Note: Redis channel layer is **not tested** in this codebase yet. Local dev runs on **Uvicorn** + in-memory Channels.

---

## Requirements

- **Python:** 3.11 or 3.12  
  - Python **3.13 is not supported** because the code uses `audioop` in the WebSocket consumer (removed from stdlib in 3.13).
- OS: Windows/macOS/Linux (local dev)
- Optional (production scaling): Redis (only if you switch Channels to Redis)

---

## Project Structure (high level)

- `config/`
  - `settings.py` – Django + AI/voice settings loaded from `.env`
  - `asgi.py` – ASGI app (HTTP + WebSocket)
  - `urls.py` – routes: `/api/…` + `/ws/voice/`
- `voice/`
  - `models.py` – `LovedOne`, `Memory`, `VoiceSample`
  - `views.py` – REST endpoints (`/api/lovedone/*`, `/api/memory/*`, `/api/voice/*`)
  - `routing.py` – WebSocket URL pattern: `/ws/voice/`
  - `consumers.py` – realtime voice pipeline + OpenAI Realtime WS + ElevenLabs streaming TTS
  - `rag_*` – Chroma-based retrieval store (RAG)
  - `tts_*`, `stt_*`, `llm_*` – provider implementations

---

## Quick Start (Local)

### 1) Create and activate venv (Python 3.11/3.12)

PowerShell:

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2) Create `.env`

Create a `.env` file in the project root (same folder as `manage.py`).

Minimum for local UI + basic API:

```env
DJANGO_DEBUG=1
DJANGO_SECRET_KEY=dev-secret-key

# Providers
LLM_PROVIDER=openai
STT_PROVIDER=openai
TTS_PROVIDER=elevenlabs

# OpenAI
OPENAI_API_KEY=YOUR_OPENAI_KEY
OPENAI_LLM_MODEL=gpt-5.2-chat-latest
OPENAI_STT_MODEL=gpt-4o-transcribe
OPENAI_REALTIME_URL=wss://api.openai.com/v1/realtime?model=gpt-realtime

# ElevenLabs
ELEVENLABS_API_KEY=YOUR_ELEVENLABS_KEY
ELEVENLABS_BASE_URL=https://api.elevenlabs.io
ELEVENLABS_MODEL_ID=eleven_turbo_v2_5

# RAG
CHROMA_DIR=chroma_db

# Debug
VOICE_DEBUG=1
```

### 3) DB setup

```powershell
python manage.py migrate
python manage.py createsuperuser
```

### 4) Run server with Uvicorn

```powershell
uvicorn config.asgi:application --host 127.0.0.1 --port 8001 --reload
```

- Web UI: `http://127.0.0.1:8001/`
- Admin: `http://127.0.0.1:8001/admin/`

---

## REST API (for backend dev)

All REST endpoints are under `/api/`.

> ⚠️ Authentication is **not implemented** yet. Current endpoints accept `profile_id` from the client and should be protected later.

### 1) Create Loved One
**POST** `/api/lovedone/create/`  
Content-Type: `application/json`

Body:
```json
{
  "profile_id": "default",
  "name": "Kevin",
  "relationship": "Friend",
  "nickname_for_user": "buddy",
  "speaking_style": "calm, supportive"
}
```

Response:
```json
{ "ok": true, "loved_one_id": 4 }
```

### 2) List Loved Ones
**GET** `/api/lovedone/list/?profile_id=default`

Response:
```json
{
  "ok": true,
  "items": [
    { "id": 4, "name": "Kevin", "relationship": "Friend", "eleven_voice_id": "", "created_at": "..." }
  ]
}
```

### 3) Get Loved One
**GET** `/api/lovedone/get/?profile_id=default&loved_one_id=4`

Response:
```json
{ "ok": true, "item": { "id": 4, "name": "Kevin", "...": "..." } }
```

### 4) Add Memory (indexes into Chroma)
**POST** `/api/memory/add/`  
Content-Type: `application/json`

Body:
```json
{
  "profile_id": "default",
  "loved_one_id": 4,
  "text": "He always called me 'buddy' and loved fishing trips."
}
```

Response:
```json
{ "ok": true, "memory_id": 12, "indexed_ids": ["..."] }
```

### 5) Upload Voice Sample (and auto-clone in ElevenLabs)
**POST** `/api/voice/upload/`  
Content-Type: `multipart/form-data`

Form fields:
- `profile_id` (optional, default `"default"`)
- `loved_one_id` (required)
- `file` (required) – audio file
- `force_reclone` (optional) – `1/true/yes` resets existing `eleven_voice_id` first

Example (PowerShell / VS Code):

```powershell
curl.exe -X POST "http://127.0.0.1:8001/api/voice/upload/" `
  -F "profile_id=default" `
  -F "loved_one_id=4" `
  -F "file=@C:\project\voice\alyve\raw_recording\Kevin voice .caf" `
  -F "force_reclone=1"
```

Response:
```json
{
  "ok": true,
  "voice_sample_id": 7,
  "eleven_voice_id": "21m00Tcm4TlvDq8ikWAM",
  "samples_count": 1,
  "min_samples_for_clone": 1,
  "has_cloned_voice": true
}
```

#### Cloning thresholds (optional env vars)
- `ELEVENLABS_MIN_SAMPLES_FOR_CLONE` (default `1`)
- `ELEVENLABS_MAX_FILES_FOR_CLONE` (default `5`)

---

## WebSocket API (Realtime Voice)

### URL
`/ws/voice/`

Local example:
- `ws://127.0.0.1:8001/ws/voice/`

### Client → Server messages

#### 1) Start a session
Send JSON:

```json
{
  "type": "session.start",
  "profile_id": "default",
  "loved_one_id": 4,
  "vad_silence_ms": 220,
  "vad_threshold": 0.55,
  "ptt_enabled": false
}
```

Notes:
- Session start will **fail** if the loved one has no `eleven_voice_id` yet.
- VAD/PTT config can be updated later with `session.config`.

#### 2) Update config (optional)
```json
{
  "type": "session.config",
  "vad_silence_ms": 220,
  "vad_threshold": 0.55,
  "ptt_enabled": true
}
```

#### 3) Push-to-talk (optional)
```json
{ "type": "ptt.down" }
{ "type": "ptt.up" }
```

#### 4) Interrupt assistant speech (barge-in)
```json
{ "type": "ai.cut_audio" }
```

#### 5) Send mic audio frames
Send **binary WebSocket frames** containing **PCM16LE mono @ 24kHz**.

The included `templates/index.html` does this automatically using an AudioContext at 24kHz and sending `Int16` PCM.

---

### Server → Client messages

- `session.connecting` – backend is connecting to OpenAI Realtime
- `session.ready` – realtime session initialized
- `session.started` – session started for a profile + loved_one
- `stt.text` – transcript chunks
- `ai.text.start` / `ai.text.delta` / `ai.text.final` – assistant text streaming
- `rt.audio.delta` – base64 audio bytes (PCM16LE) to play
- `rt.audio.end` – end of assistant audio stream (may not always fire)
- `event` – internal/debug events (gated by `VOICE_DEBUG`)
- `warn` / `error` – errors and warnings

---

## Providers & Memory

### LLM (chat)
- OpenAI `responses.create(... stream=True)`  
Config:
- `LLM_PROVIDER=openai`
- `OPENAI_LLM_MODEL=...`

### STT (transcription)
- OpenAI `audio.transcriptions.create(...)`
Config:
- `STT_PROVIDER=openai`
- `OPENAI_STT_MODEL=...`

### TTS (speech)
- Primary: ElevenLabs streaming PCM (`pcm_24000`)
Config:
- `TTS_PROVIDER=elevenlabs`
- `ELEVENLABS_MODEL_ID`, plus optional tuning vars:
  - `ELEVENLABS_TTS_SPEED`
  - `ELEVENLABS_TTS_STABILITY`
  - `ELEVENLABS_TTS_SIMILARITY_BOOST`
  - `ELEVENLABS_TTS_USE_SPEAKER_BOOST`
  - `ELEVENLABS_TTS_STYLE`
  - `ELEVENLABS_PCM_SWAP_ENDIAN` (0/1)

### RAG / Memory Store (Chroma)
- Persistent Chroma DB at `CHROMA_DIR`
- Embeddings via `sentence-transformers` model `all-MiniLM-L6-v2`

To reset memory index locally:
- stop server
- delete the `chroma_db/` folder (or whatever `CHROMA_DIR` points to)

---

## Channels / Redis (untested)

Local dev defaults to in-memory channel layer.

If you later enable Redis:
```env
CHANNEL_BACKEND=redis
REDIS_URL=redis://localhost:6379/0
```

You must also install:
- `channels-redis`

> Redis is recommended for multi-worker / production deployments (in-memory channels won’t share state across processes).

---

## Production Notes (handoff)

- Set `DJANGO_DEBUG=0`
- Set a real `DJANGO_SECRET_KEY`
- Configure persistent storage for:
  - `MEDIA_ROOT` (uploads)
  - `CHROMA_DIR` (vector index)
- Add authentication + authorization (scoping `profile_id` per user)
- Add file upload limits (size/type/rate) (planned by backend team)

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'audioop'`
You are running **Python 3.13**. Switch to Python **3.11/3.12**.

### ElevenLabs cloning doesn’t happen
- Ensure `ELEVENLABS_API_KEY` is set
- Ensure `ELEVENLABS_BASE_URL=https://api.elevenlabs.io`
- Upload enough samples to meet `ELEVENLABS_MIN_SAMPLES_FOR_CLONE`

### WebSocket session errors: `no_cloned_voice`
Upload voice samples first and ensure the Loved One has an `eleven_voice_id`.

---
