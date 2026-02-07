from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, List, Optional

from openai import AsyncOpenAI


@dataclass
class ExtractedMemory:
    text: str
    confidence: float
    kind: str  


def _clamp01(x: Any, default: float = 0.6) -> float:
    try:
        v = float(x)
        return max(0.0, min(1.0, v))
    except Exception:
        return default


def _looks_like_request_to_remember(user_text: str) -> bool:
    t = (user_text or "").lower()
    return any(p in t for p in [
        "remember this",
        "remember that",
        "save this",
        "save that",
        "store this",
        "note this",
        "don't forget",
        "do not forget",
    ])


def heuristic_gate(user_text: str) -> bool:
    """
    Gate extraction to reduce cost + prevent junk. Conservative default.
    If you want to debug pipeline, set ALWAYS_EXTRACT=True in settings and bypass in consumers.py.
    """
    u = (user_text or "").strip()
    if len(u) < 6:
        return False

    if _looks_like_request_to_remember(u):
        return True

    t = u.lower()
    patterns = [
        r"\bmy name is\b",
        r"\bcall me\b",
        r"\byou can call me\b",
        r"\bplease call me\b",
        r"\bi am\b",
        r"\bi'm\b",
        r"\bi live in\b",
        r"\bi work\b",
        r"\bi like\b",
        r"\bi love\b",
        r"\bi hate\b",
        r"\bmy favorite\b",
        r"\bi prefer\b",
        r"\bmy mum\b|\bmy mom\b|\bmy dad\b|\bmy father\b|\bmy mother\b|\bmy grandpa\b|\bmy grandma\b|\bmy wife\b|\bmy husband\b|\bmy sister\b|\bmy brother\b",
        r"\balways\b.+\bcall\b",
        r"\bnever\b.+\bcall\b",
    ]
    return any(re.search(p, t) for p in patterns)


def _extract_json_from_text(s: str) -> Optional[dict]:
    if not s:
        return None
    s = s.strip()

    try:
        return json.loads(s)
    except Exception:
        pass

    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        return None

    blob = m.group(0).strip()
    try:
        return json.loads(blob)
    except Exception:
        return None


def _filter_sensitive(memories: List[ExtractedMemory], user_text: str) -> List[ExtractedMemory]:
    """
    Prevent saving sensitive info unless the user explicitly asked to remember/store it.
    """
    explicit = _looks_like_request_to_remember(user_text)

    sensitive_keywords = [
        "diagnosed", "depression", "anxiety", "bipolar", "adhd", "cancer", "diabetes", "medication",
        "vote", "voted", "party", "democrat", "republican",
        "muslim", "christian", "hindu", "buddhist", "atheist",
        "sex", "sexual",
    ]

    out: List[ExtractedMemory] = []
    for m in memories:
        tl = (m.text or "").lower()
        if not tl:
            continue
        if not explicit and any(k in tl for k in sensitive_keywords):
            continue
        out.append(m)
    return out


async def extract_memories_via_openai(
    *,
    api_key: str,
    model: str,
    user_text: str,
    assistant_text: str,
    max_items: int = 3,
) -> List[ExtractedMemory]:

    if not api_key:
        return []

    client = AsyncOpenAI(api_key=api_key)

    system = (
        "You are a memory extraction engine for a conversational AI.\n"
        "Extract ONLY durable, stable information worth remembering for future chats.\n"
        "Return STRICT JSON only (no markdown), with schema:\n"
        "{\n"
        '  "memories": [\n'
        '    {"text": "...", "kind": "preference|profile|relationship|fact", "confidence": 0.0}\n'
        "  ]\n"
        "}\n"
        f"Rules:\n"
        f"- Output 0 to {max_items} memories.\n"
        "- Each memory must be a short single sentence.\n"
        "- Prefer: user preferences, stable facts, relationships, names, nicknames, speaking style cues.\n"
        "- Avoid ephemeral info (plans, one-off Q&A).\n"
        "- Avoid sensitive medical/political/sexual info unless user explicitly asked to remember it.\n"
    )

    user = (
        "Conversation snippet:\n"
        f"USER SAID:\n{user_text}\n\n"
        f"ASSISTANT REPLIED:\n{assistant_text}\n\n"
        "Now output the JSON.\n"
    )

    resp = await client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )

    txt = ""
    try:
        for item in getattr(resp, "output", []) or []:
            for c in getattr(item, "content", []) or []:
                t = getattr(c, "text", None)
                if t:
                    txt += t
    except Exception:
        txt = ""

    data = _extract_json_from_text(txt)
    if not isinstance(data, dict):
        return []

    raw = data.get("memories", [])
    if not isinstance(raw, list):
        return []

    out: List[ExtractedMemory] = []
    for it in raw[:max_items]:
        if not isinstance(it, dict):
            continue
        text = (it.get("text") or "").strip()
        if not text:
            continue
        kind = (it.get("kind") or "fact").strip()
        conf = _clamp01(it.get("confidence", 0.6), 0.6)
        out.append(ExtractedMemory(text=text, kind=kind, confidence=conf))

    out = _filter_sensitive(out, user_text)
    out = [m for m in out if m.confidence >= 0.55]

    return out
