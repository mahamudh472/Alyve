from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional
from openai import AsyncOpenAI
from .tts_base import TTSBase


class OpenAITTS(TTSBase):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini-tts",
        voice: str = "cedar",
        instructions: Optional[str] = None,
    ):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is missing")
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.voice = voice
        self.instructions = instructions

    async def stream_audio_mp3(self, text: str) -> AsyncIterator[bytes]:
        req = {"model": self.model, "voice": self.voice, "input": text}
        if self.instructions:
            req["instructions"] = self.instructions

        yielded = 0

        try:
            async with self.client.audio.speech.with_streaming_response.create(**req) as resp:
                async for chunk in resp.iter_bytes(chunk_size=4096):
                    if chunk:
                        yielded += len(chunk)
                        yield chunk
        except Exception:
            pass

        if yielded == 0:
            resp2 = await self.client.audio.speech.create(**req)

            data = getattr(resp2, "content", None)
            if data is None and hasattr(resp2, "read"):
                maybe = resp2.read()
                data = await maybe if asyncio.iscoroutine(maybe) else maybe

            if not data:
                raise RuntimeError("OpenAI TTS returned empty audio bytes")

            for i in range(0, len(data), 4096):
                yield data[i : i + 4096]
