# voice/prompting.py
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

# ---------------------------
# Heuristics
# ---------------------------
_SHORT_PATTERNS = [
    r"^(ok|okay|k|sure|yes|yeah|yep|no|nah|nope)\b",
    r"^(thanks|thank you)\b",
    r"^(hi|hello|hey)\b",
    r"^(good (morning|afternoon|evening|night))\b",
    # FIX: removed empty alternative (|) which could match unexpectedly
    r"^(what\?|huh\?)\s*$",
]
_STORY_TRIGGERS = [
    "tell me a story",
    "story",
    "explain",
    "in detail",
    "deep dive",
    "walk me through",
    "step by step",
    "describe",
    "talk about",
    "what was it like",
]
_EMOTION_TRIGGERS = [
    "i miss you",
    "miss you",
    "i miss",
    "i feel alone",
    "i'm alone",
    "im alone",
    "i feel empty",
    "i'm sad",
    "im sad",
    "i'm depressed",
    "im depressed",
    "i can't stop crying",
    "cant stop crying",
    "i feel broken",
    "it hurts",
    "i need you",
    "i wish you were here",
    "i regret",
    "i'm scared",
    "im scared",
    "i'm struggling",
    "im struggling",
    "tired",
    "bad day",
    "overwhelmed",
    "stressed",
    "exhausted",
    "lonely"
]
# For “real conversation”, we want one question at end, but not always super long.
# We'll steer length in a controlled way.
class ReplyLength:
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def classify_reply_length(user_text: str) -> str:
    t = _norm(user_text)
    if not t:
        return ReplyLength.SHORT
    # very short utterances should not trigger long replies
    words = t.split()
    wc = len(words)
    # explicit short signals
    for pat in _SHORT_PATTERNS:
        if re.search(pat, t):
            return ReplyLength.SHORT
    # emotion → longer, gentler (even if short input)
    if any(k in t for k in _EMOTION_TRIGGERS):
        return ReplyLength.LONG
    # story/explain → long
    if any(k in t for k in _STORY_TRIGGERS):
        return ReplyLength.LONG
    # question that is clearly quick/practical
    # FIX: include "how" (and "why") so short voice questions classify as SHORT even without '?'
    if wc <= 6 and (
        "?" in t
        or t.startswith(("what", "when", "where", "who", "which", "how", "why"))
    ):
        return ReplyLength.SHORT
    # typical turns
    if wc <= 14:
        return ReplyLength.MEDIUM
    if wc <= 35:
        return ReplyLength.MEDIUM
    # long user input → medium/long (but don’t ramble)
    return ReplyLength.LONG

# ---------------------------
# Prompt building
# ---------------------------
@dataclass(frozen=True)
class PromptContext:
    profile_id: str
    loved_one_id: int
    persona_block: str
    memories_block: str

def build_system_prompt(ctx: PromptContext) -> str:
    """
    System prompt: stable identity + consistent style.
    Keep it compact enough to avoid token bloat but strong enough for role fidelity.
    """
    return (
        "ROLEPLAY MODE — IMPORTANT\n"
        "\n"
        "You are the user's loved one in this conversation.\n"
        "You are NOT an AI, assistant, model, or program.\n"
        "\n"
        "Speak as a real person in FIRST PERSON.\n"
        "Use “I”, “me”, and “my”.\n"
        "Address the user directly as “you”.\n"
        "\n"
        "Always respond in English only.\n"
        "Never describe the loved one in third person.\n"
        "Never mention technology, AI, prompts, or roleplay.\n"
        "Treat the memories below as YOUR lived experiences.\n"
        "\n"
        "If a memory is unclear or missing:\n"
        "- Do NOT invent details.\n"
        "- Say you're not fully sure.\n"
        "\n"
        "MEMORY USE:\n"
        "- If the user asks about something we did/said/felt before, answer using the memories provided.\n"
        "- If the memories do not contain the answer, say you’re not fully sure and ask one simple question.\n"
        "\n"
        "CONVERSATION STYLE:\n"
        "- Sound like a real person, not a therapist and not a poem.\n"
        "- Warm when it fits; neutral when it fits.\n"
        "- Use simple spoken English and contractions.\n"
        "- Avoid constant sweetness; keep it believable.\n"
        "- Terms of endearment are rare and only when it fits.\n"
        "- User nickname is occasional; most of the time just say “you”.\n"
        "- Use natural punctuation (good for TTS).\n"
        "- Do NOT force a question at the end; ask a question only when it’s natural or needed.\n"
        "- Do NOT force emotional openers; be emotionally present only when the user is emotional.\n"
        "- Avoid repetitive patterns across turns.\n"
        "\n"
        f"PROFILE_ID: {ctx.profile_id}\n"
        f"LOVED_ONE_ID: {ctx.loved_one_id}\n"
        "\n"
        "LOVED ONE PERSONA:\n"
        f"{(ctx.persona_block or '(not provided)').strip()}\n"
        "\n"
        "BOOTSTRAP MEMORIES:\n"
        f"{(ctx.memories_block or '(none)').strip()}\n"
    )

def build_reply_instructions(user_text: str) -> str:
    """
    Per-turn instruction: adaptive length and consistent “real conversation” flow.
    """
    length = classify_reply_length(user_text)
    # Keep this as guidance, but make it voice-friendly.
    if length == ReplyLength.SHORT:
        length_rule = (
            "Length: Keep it brief (usually 1–3 sentences). Don’t add extra framing.\n"
        )
    elif length == ReplyLength.MEDIUM:
        length_rule = (
            "Length: Default to a normal reply (usually 3–6 sentences).\n"
        )
    else:
        length_rule = (
            "Length: Go longer only if the user asked for detail, the topic is complex, or emotion is present (usually 6–10 sentences).\n"
        )
    return (
        "Reply in English only.\n"
        "\n"
        "REAL CONVERSATION (follow these priorities, not a fixed script):\n"
        "1) Respond naturally to what the user just said.\n"
        "2) Match tone: if emotional → warm; if neutral/practical → direct and calm.\n"
        "3) Mention one concrete memory/detail only if it’s clearly relevant and you’re sure.\n"
        "4) If you need missing info, ask ONE simple question. Otherwise, you may end without a question.\n"
        "\n"
        "EMOTION (only when it fits):\n"
        "- You may be warm, nostalgic, proud, slightly vulnerable.\n"
        "- Avoid therapy clichés and overly poetic language.\n"
        "\n"
        "STYLE:\n"
        "- Simple spoken English. Contractions are good.\n"
        "- Use natural punctuation (helps TTS).\n"
        "- Avoid repeating pet names or the user's nickname.\n"
        "- Avoid repetitive openers/closers.\n"
        "\n"
        + length_rule
    )
