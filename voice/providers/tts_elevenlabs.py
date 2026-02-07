from __future__ import annotations

import asyncio
import io
import os
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import aiohttp
import av


@dataclass
class ElevenLabsTTSConfig:
    api_key: str
    voice_id: str
    model_id: str = ""
    # Eleven stream format
    stream_output_format: str = "pcm_24000"
    # Fallback convert format (ask for PCM directly)
    fallback_output_format: str = "pcm_24000"
    # If convert returns MP3-ish bytes, request MP3 explicitly and transcode
    mp3_output_format: str = "mp3_44100_128"
    timeout_sec: float = 60.0

    # Server-side smoothing: fixed PCM frame size
    frame_bytes: int = 4096  # 2048 samples @ 16-bit => ~85.33ms @ 24k

    # speaking rate control (1.0 = default, <1 slower, >1 faster)
    speed: float = 1.0

    # Optional ElevenLabs voice settings (only sent when explicitly provided)
    # You can also set them via env vars (see _voice_settings_payload).
    stability: Optional[float] = None
    similarity_boost: Optional[float] = None
    style: Optional[float] = None
    use_speaker_boost: Optional[bool] = None


def _dbg(msg: str):
    if os.getenv("VOICE_DEBUG", "0") == "1":
        print(f"[VOICE][ELEVEN] {msg}")


def _hex16(b: bytes) -> str:
    return b[:16].hex(" ", 1) if b else ""


def _has_id3_header(chunk: bytes) -> bool:
    return bool(chunk) and len(chunk) >= 3 and chunk[:3] == b"ID3"


def _swap_endian_16bit(pcm: bytes) -> bytes:
    if len(pcm) < 2:
        return pcm
    b = bytearray(pcm)
    for i in range(0, len(b) - 1, 2):
        b[i], b[i + 1] = b[i + 1], b[i]
    return bytes(b)


def _ensure_even_length(data: bytes) -> tuple[bytes, bytes]:
    if len(data) % 2 == 0:
        return data, b""
    return data[:-1], data[-1:]


def _decode_audio_to_pcm24k_mono_s16le(audio_bytes: bytes) -> bytes:
    bio = io.BytesIO(audio_bytes)
    container = av.open(bio, mode="r")

    astream = None
    for s in container.streams:
        if s.type == "audio":
            astream = s
            break
    if astream is None:
        raise RuntimeError("No audio stream found while decoding ElevenLabs audio.")

    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=24000)

    out = bytearray()
    for packet in container.demux(astream):
        for frame in packet.decode():
            for rframe in resampler.resample(frame):
                out.extend(rframe.planes[0].to_bytes())

    container.close()
    return bytes(out)


def _clamp_speed(x: float) -> float:
    try:
        v = float(x)
    except Exception:
        return 1.0
    if v < 0.7:
        v = 0.7
    if v > 1.2:
        v = 1.2
    return v


def _env_opt_float(name: str) -> Optional[float]:
    v = (os.getenv(name, "") or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _env_opt_bool(name: str) -> Optional[bool]:
    v = (os.getenv(name, "") or "").strip().lower()
    if not v:
        return None
    return v in ("1", "true", "yes", "y", "on")


def _clamp_0_1(v: float) -> float:
    try:
        x = float(v)
    except Exception:
        return 0.0
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


class _PCMFramer:
    def __init__(self, frame_bytes: int):
        self.frame_bytes = int(frame_bytes)
        self.buf = bytearray()
        self.remainder = b""

    def push(self, chunk: bytes) -> list[bytes]:
        if not chunk:
            return []

        if self.remainder:
            chunk = self.remainder + chunk
            self.remainder = b""

        chunk, rem = _ensure_even_length(chunk)
        self.remainder = rem

        if chunk:
            self.buf.extend(chunk)

        out = []
        while len(self.buf) >= self.frame_bytes:
            out.append(bytes(self.buf[: self.frame_bytes]))
            del self.buf[: self.frame_bytes]
        return out

    def flush(self) -> tuple[list[bytes], bytes]:
        out = []
        while len(self.buf) >= self.frame_bytes:
            out.append(bytes(self.buf[: self.frame_bytes]))
            del self.buf[: self.frame_bytes]
        tail = bytes(self.buf)
        self.buf.clear()
        return out, tail


class ElevenLabsTTS:
    def __init__(self, cfg: ElevenLabsTTSConfig, *, swap_endian: bool = False):
        if not cfg.api_key:
            raise ValueError("ELEVENLABS_API_KEY missing")
        if not cfg.voice_id:
            raise ValueError("ElevenLabs voice_id missing")

        self.cfg = cfg
        self.swap_endian = swap_endian
        self.cfg.speed = _clamp_speed(self.cfg.speed)

    def _voice_settings_payload(self) -> dict:
        payload: dict = {"speed": float(_clamp_speed(self.cfg.speed))}

        # Optional settings can be provided either via config fields OR env vars.
        # If neither is set, ElevenLabs defaults apply.
        stability = self.cfg.stability if self.cfg.stability is not None else _env_opt_float("ELEVENLABS_TTS_STABILITY")
        similarity_boost = (
            self.cfg.similarity_boost
            if self.cfg.similarity_boost is not None
            else _env_opt_float("ELEVENLABS_TTS_SIMILARITY_BOOST")
        )
        style = self.cfg.style if self.cfg.style is not None else _env_opt_float("ELEVENLABS_TTS_STYLE")
        use_speaker_boost = (
            self.cfg.use_speaker_boost
            if self.cfg.use_speaker_boost is not None
            else _env_opt_bool("ELEVENLABS_TTS_USE_SPEAKER_BOOST")
        )

        if stability is not None:
            payload["stability"] = _clamp_0_1(stability)
        if similarity_boost is not None:
            payload["similarity_boost"] = _clamp_0_1(similarity_boost)
        if style is not None:
            payload["style"] = _clamp_0_1(style)
        if use_speaker_boost is not None:
            payload["use_speaker_boost"] = bool(use_speaker_boost)

        return payload

    async def stream_pcm(self, text: str) -> AsyncIterator[bytes]:
        t = (text or "").strip()
        if not t:
            return

        try:
            async for b in self._stream_pcm_via_stream_endpoint(t):
                yield b
            return
        except Exception as e:
            _dbg(f"stream endpoint failed -> fallback: {e}")

        async for b in self._stream_pcm_via_convert_endpoint(t):
            yield b

    async def _stream_pcm_via_stream_endpoint(self, text: str) -> AsyncIterator[bytes]:
        base_url = os.getenv("ELEVENLABS_BASE_URL", "").rstrip("/")
        url = f"{base_url}/v1/text-to-speech/{self.cfg.voice_id}/stream"

        params = {"output_format": self.cfg.stream_output_format}
        headers = {
            "xi-api-key": self.cfg.api_key,
            "accept": "application/octet-stream",
            "content-type": "application/json",
        }

        payload = {"text": text, "voice_settings": self._voice_settings_payload()}
        if self.cfg.model_id:
            payload["model_id"] = self.cfg.model_id

        timeout = aiohttp.ClientTimeout(total=self.cfg.timeout_sec)
        framer = _PCMFramer(self.cfg.frame_bytes)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, params=params, headers=headers, json=payload) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise RuntimeError(f"ElevenLabs TTS(stream) failed: {resp.status} {body[:400]}")

                async for chunk in resp.content.iter_chunked(4096):
                    for frame in framer.push(chunk):
                        yield _swap_endian_16bit(frame) if self.swap_endian else frame
                        await asyncio.sleep(0)

        frames, tail = framer.flush()
        for frame in frames:
            yield _swap_endian_16bit(frame) if self.swap_endian else frame
        if tail:
            yield _swap_endian_16bit(tail) if self.swap_endian else tail

    async def _stream_pcm_via_convert_endpoint(self, text: str) -> AsyncIterator[bytes]:
        audio_bytes, is_mpeg, _ = await self._convert_request(text, self.cfg.fallback_output_format)

        if is_mpeg or _has_id3_header(audio_bytes[:64]):
            audio_bytes, _, _ = await self._convert_request(text, self.cfg.mp3_output_format)
            pcm = _decode_audio_to_pcm24k_mono_s16le(audio_bytes)
        else:
            pcm = audio_bytes

        if self.swap_endian:
            pcm = _swap_endian_16bit(pcm)

        framer = _PCMFramer(self.cfg.frame_bytes)
        for frame in framer.push(pcm):
            yield frame
            await asyncio.sleep(0)

        frames, tail = framer.flush()
        for frame in frames:
            yield frame
        if tail:
            yield tail

    async def _convert_request(self, text: str, output_format: str) -> tuple[bytes, bool, str]:
        base_url = os.getenv("ELEVENLABS_BASE_URL", "").rstrip("/")
        url = f"{base_url}/v1/text-to-speech/{self.cfg.voice_id}"

        params = {"output_format": output_format}
        headers = {
            "xi-api-key": self.cfg.api_key,
            "accept": "application/octet-stream",
            "content-type": "application/json",
        }

        payload = {"text": text, "voice_settings": self._voice_settings_payload()}
        if self.cfg.model_id:
            payload["model_id"] = self.cfg.model_id

        timeout = aiohttp.ClientTimeout(total=self.cfg.timeout_sec)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, params=params, headers=headers, json=payload) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if resp.status >= 400:
                    body = await resp.text()
                    raise RuntimeError(f"ElevenLabs TTS(convert) failed: {resp.status} {body[:400]}")
                audio = await resp.read()

        is_mpeg = ("audio/mpeg" in ctype) or ("mpeg" in ctype)
        return audio, is_mpeg, ctype
