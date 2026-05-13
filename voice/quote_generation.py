from __future__ import annotations

import json
import logging

from conversations.openai_client import generate_reply

from .models import LovedOne, Quote


logger = logging.getLogger(__name__)


def _non_empty_value(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _loved_one_prompt_payload(loved_one: LovedOne) -> dict[str, str]:
    payload: dict[str, str] = {}
    for field in (
        "name",
        "relationship",
        "nickname_for_user",
        "description",
        "speaking_style",
        "catch_phrase",
        "core_memories",
    ):
        value = _non_empty_value(getattr(loved_one, field, ""))
        if value:
            payload[field] = value
    return payload


def should_generate_personal_quotes(loved_one: LovedOne) -> bool:
    return bool(_non_empty_value(loved_one.name) and _non_empty_value(loved_one.relationship))


def _extract_quotes(raw_text: str) -> list[str]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    data = None
    decoder = json.JSONDecoder()
    for start_index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            data, _ = decoder.raw_decode(text[start_index:])
            break
        except json.JSONDecodeError:
            continue

    if data is None:
        data = json.loads(text)

    if isinstance(data, dict):
        data = data.get("quotes", [])

    if not isinstance(data, list):
        raise ValueError("OpenAI response is not a list of quotes")

    quotes: list[str] = []
    for item in data:
        quote = _non_empty_value(item)
        if quote:
            quotes.append(quote)
    return quotes


def generate_personal_quotes_for_loved_one(loved_one: LovedOne) -> list[Quote]:
    if not should_generate_personal_quotes(loved_one):
        return []

    prompt_payload = _loved_one_prompt_payload(loved_one)
    system_prompt = (
           "You write short, warm, emotionally grounded quote fragments about the loved one. "
           "Make them feel like descriptive lines or memories, not spoken dialogue. "
           "Use third-person or observational language, like: 'The smell of his pipe tobacco and cedar wood.' "
           "Avoid first person, second person, direct speech, quotation marks, and phrases like 'I remember' or 'you are'. "
           "Return valid JSON only as a list of exactly 3 strings. "
           "Do not include markdown, numbering, or extra commentary."
    )
    user_text = json.dumps(prompt_payload, ensure_ascii=False, indent=2)

    try:
        response = generate_reply(system_prompt=system_prompt, user_text=user_text)
        quotes = _extract_quotes(response.text)
    except Exception as exc:
        logger.warning("Failed to generate personal quotes for loved one %s: %s", loved_one.pk, exc)
        return []

    if len(quotes) < 3:
        logger.warning(
            "OpenAI returned %s quote(s) for loved one %s; expected 3",
            len(quotes),
            loved_one.pk,
        )
        return []

    cleaned_quotes = quotes[:3]
    Quote.objects.filter(loved_one=loved_one, quote_type=Quote.QuoteScope.PERSONAL).delete()

    quote_objects = [
        Quote(
            user=loved_one.user,
            loved_one=loved_one,
            quote_type=Quote.QuoteScope.PERSONAL,
            content=quote,
        )
        for quote in cleaned_quotes
    ]
    Quote.objects.bulk_create(quote_objects)
    return quote_objects