from __future__ import annotations

import os
from dataclasses import dataclass
from django.conf import settings


@dataclass(frozen=True)
class LLMResult:
    text: str


def _get_env(name: str, default: str = "") -> str:
    v = os.getenv(name, "")
    return v.strip() if v else default


def generate_reply(*, system_prompt: str, user_text: str) -> LLMResult:
    """
    Uses OpenAI Responses API via the official SDK.
    pip install openai
    """
    api_key = _get_env("OPENAI_API_KEY")
    model = _get_env("OPENAI_LLM_MODEL", "gpt-5.2-chat-latest")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    # Import here so the server can still boot if openai isn't installed yet.
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=api_key)

    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
    )

    # Responses API returns output items; safest is to use output_text helper when present.
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return LLMResult(text=text.strip())

    # Fallback extraction (SDK shapes can vary)
    out = ""
    try:
        for item in resp.output or []:
            for c in getattr(item, "content", []) or []:
                if getattr(c, "type", "") == "output_text":
                    out += getattr(c, "text", "") or ""
    except Exception:
        out = ""

    out = (out or "").strip()
    if not out:
        raise RuntimeError("OpenAI returned empty output")

    return LLMResult(text=out)


def stream_reply(*, system_prompt: str, user_text: str):
    """
    Synchronous generator that yields text deltas from OpenAI Responses API.
    """
    api_key = _get_env("OPENAI_API_KEY")
    model = _get_env("OPENAI_LLM_MODEL", "gpt-5.2-chat-latest")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    stream = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        stream=True,
    )

    for event in stream:
        etype = getattr(event, "type", "")
        if etype == "response.output_text.delta":
            delta = getattr(event, "delta", "")
            if delta:
                yield delta
        elif etype in ("response.output_text.done", "response.text.done"):
            # Some SDK versions might put the full text here
            text = getattr(event, "text", "")
            if text:
                # We usually only want deltas, but if this is the only thing we get...
                # For now, let's just yield deltas.
                pass