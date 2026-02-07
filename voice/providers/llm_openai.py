from __future__ import annotations

from typing import AsyncIterator, List, Any
from openai import AsyncOpenAI
from .llm_base import LLMBase, LLMMessage


def _get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class OpenAILLM(LLMBase):

    def __init__(self, api_key: str, model: str = "gpt-5.2-chat-latest"):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is missing")
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def stream(self, messages: List[LLMMessage]) -> AsyncIterator[str]:
        input_msgs = [{"role": m.role, "content": m.content} for m in messages]

        stream = await self.client.responses.create(
            model=self.model,
            input=input_msgs,
            stream=True,
        )

        saw_delta = False
        yielded_fallback_final = False

        async for event in stream:
            etype = _get(event, "type")

            if etype == "response.output_text.delta":
                delta = _get(event, "delta", "")
                if delta:
                    saw_delta = True
                    yield delta
                continue


            if (not saw_delta) and (not yielded_fallback_final) and etype in (
                "response.output_text",
                "response.output_text.done",
            ):
                text = _get(event, "text", "")
                if text:
                    yielded_fallback_final = True
                    yield text
                continue
