# Alyve Voice Service (Django REST API)

This repo contains a Django backend for voice conversations:
- Creating "Loved One" profiles with persona attributes
- Storing memories and auto-extracting important facts from conversations
- Uploading voice samples and auto-cloning voices in ElevenLabs
- REST API for text-based chat with RAG context and conversation history
- Streaming responses sentence-by-sentence via Server-Sent Events (SSE)

> Note: Frontend (STT/TTS) and WebSocket consumer are **deprecated**. Use the new REST API endpoints.

---

## Requirements

- **Python:** 3.11 or 3.12  
  - Python 3.13+ is supported (no longer uses `audioop`)
- OS: Windows/macOS/Linux (local dev)
- Optional (production scaling): Redis (only if you switch Channels to Redis)

---

## Project Structure (high level)

- `config/`
  - `settings.py` – Django + AI settings loaded from `.env`
  - `asgi.py` – ASGI app (HTTP + Django Channels for other features)
  - `urls.py` – routes: `/api/…`
- `voice/`
  - `models.py` – `LovedOne` model to store loved one profiles
  - `views.py` – REST endpoints:
    - `/api/voice/lovedone/` – Create and list loved ones
    - `/api/voice/memory/add/` – Add memories manually
    - `/api/voice/upload/` – Upload voice samples for cloning
    - `/api/voice/chat/text/` – Text-based chat (non-streaming)
    - `/api/voice/chat/text/stream/` – Text-based chat (streaming, SSE)
  - `routing.py` – WebSocket URL patterns (deprecated)
  - `consumers.py` – old WebSocket realtime consumer (deprecated, can be deleted)
  - `rag_*` – Chroma-based retrieval store (RAG) for memory context
  - `memory_auto.py` – Automatic memory extraction from conversations
  - `prompting.py` – System prompt and response instruction building

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

## Quick Start (Docker)

### 1) Prepare env file

Create a `.env` in project root (or copy from `.env.example`):

```bash
cp .env.example .env
```

Then edit `.env` and fill required keys:
- `OPENAI_API_KEY`
- `ELEVENLABS_API_KEY`
- `DJANGO_HTTPS_ENABLED=1` (when using Nginx with TLS)
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain.com,https://www.your-domain.com`
- `DJANGO_DB_BACKEND=sqlite` or `DJANGO_DB_BACKEND=postgres`

If using Postgres, also set:
- `POSTGRES_HOST=postgres`
- `POSTGRES_PORT=5432`
- `POSTGRES_DB=alyve`
- `POSTGRES_USER=alyve`
- `POSTGRES_PASSWORD=alyve`

### 2) Start containers

```bash
docker compose up --build
```

Server runs at:
- `https://127.0.0.1/` (if certs exist)
- `http://127.0.0.1/` (automatic fallback if certs are missing)

### 3) Stop containers

```bash
docker compose down
```

### Notes

- The app initializes Firebase on startup using:
  `eternalink27-firebase-adminsdk-fbsvc-3e599484ed.json`
  Keep this file in the project root when running with Docker.
- The container entrypoint runs migrations automatically before starting Uvicorn.
- The container entrypoint also runs `collectstatic`, and Nginx serves `/static/` and `/media/` directly.
- Current compose includes Redis service, but default env keeps `CHANNEL_BACKEND=inmemory`.
- Compose includes PostgreSQL service; Django uses it only when `DJANGO_DB_BACKEND=postgres`.
- Nginx is the public entrypoint and proxies HTTP/HTTPS requests to Django ASGI.

### SSL certificates (optional)

Put cert files in `deploy/nginx/certs/`:
- `fullchain.pem`
- `privkey.pem`

If these files are missing, Nginx starts in HTTP-only mode automatically.

For local testing, you can generate a self-signed certificate:

```bash
openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout deploy/nginx/certs/privkey.pem \
  -out deploy/nginx/certs/fullchain.pem \
  -days 365 \
  -subj "/CN=localhost"
```

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

## REST API (Voice Chat)

### New Architecture
- **Frontend handles:** STT (speech-to-text), TTS (text-to-speech), audio playback
- **Backend handles:** LLM (language model), RAG (context retrieval), conversation history, auto-memory extraction

### Endpoints

#### 1) Non-streaming chat
`POST /api/voice/chat/text/`

**Request:**
```json
{
  "text": "What is your name?",
  "loved_one_id": 123,
  "session_id": 456
}
```

**Response:**
```json
{
  "ok": true,
  "response": "My name is Grandma...",
  "session_id": 456,
  "loved_one_name": "Grandma"
}
```

#### 2) Streaming chat (Server-Sent Events)
`POST /api/voice/chat/text/stream/`

Same request format. Response is SSE stream with `stream.sentence` events.

---

## Providers & Optional Features

### LLM (chat)
- OpenAI `responses.create()`  
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
