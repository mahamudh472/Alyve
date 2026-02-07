from __future__ import annotations
from typing import AsyncIterator

class TTSBase:
    async def stream_audio_mp3(self, text: str) -> AsyncIterator[bytes]:
        raise NotImplementedError
