from __future__ import annotations

import io
import wave
from openai import AsyncOpenAI


def _pcm16_to_wav_bytes(pcm16: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    return buf.getvalue()


class OpenAITranscribeSTT:
    def __init__(self, api_key: str, model: str = "gpt-4o-transcribe"):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is missing")
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def transcribe_pcm16(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        wav_bytes = _pcm16_to_wav_bytes(pcm16, sample_rate)
        f = io.BytesIO(wav_bytes)
        f.name = "audio.wav"

        resp = await self.client.audio.transcriptions.create(
            model=self.model,
            file=f,
            response_format="text",
            language="en",
        )

        if isinstance(resp, str):
            return resp.strip()

        text = getattr(resp, "text", None)
        if text:
            return str(text).strip()

        try:
            return str(resp.get("text", "")).strip()  
        except Exception:
            return ""
