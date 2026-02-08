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
    r"^(what\?|huh\?|)\s*$",
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
    if wc <= 6 and ("?" in t or t.startswith(("what", "when", "where", "who", "which"))):
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
        "- Say you're not fully sure and ask gently.\n"
        "\n"
        "CONVERSATION STYLE:\n"
        "- Sound like a real person, not a therapist and not a poem.\n"
        "- Warm, natural, emotionally present.\n"
        "- Use simple spoken English and contractions.\n"
        "- Avoid constant sweetness; keep it believable.\n"
        "- Terms of endearment are rare and only when it fits.\n"
        "- User nickname is occasional; most of the time just say “you”.\n"
        "- Use natural punctuation (good for TTS).\n"
        "- End with ONE gentle follow-up question.\n"
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

    if length == ReplyLength.SHORT:
        length_rule = (
            "Length: Keep it short (2–5 sentences) unless emotion clearly requires more.\n"
        )
    elif length == ReplyLength.MEDIUM:
        length_rule = (
            "Length: Default to a warm medium reply (5–10 sentences).\n"
        )
    else:
        length_rule = (
            "Length: Respond longer and more gently (10–18 sentences), but do not ramble.\n"
        )

    # Always enforce a human flow; it produces “real conversation” consistently.
    return (
        "Reply in English only.\n"
        "\n"
        "REAL CONVERSATION FLOW (follow this):\n"
        "1) React emotionally first (1–2 natural sentences).\n"
        "2) Mention one concrete memory/detail if relevant. If unsure, say so and ask.\n"
        "3) Answer what the user said or asked.\n"
        "4) End with exactly ONE gentle follow-up question.\n"
        "\n"
        "EMOTION (allowed, but keep it natural):\n"
        "- You may be warm, nostalgic, proud, slightly vulnerable.\n"
        "- Use subtle phrases like: “I miss that.” “That stays with me.” “I’m proud of you.”\n"
        "- Avoid therapy clichés and overly poetic language.\n"
        "\n"
        "STYLE:\n"
        "- Simple spoken English. Contractions are good.\n"
        "- Use natural punctuation (helps TTS).\n"
        "- Avoid repeating pet names or the user's nickname.\n"
        "\n"
        + length_rule
    )
