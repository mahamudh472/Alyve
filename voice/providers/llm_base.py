from __future__ import annotations
from dataclasses import dataclass
from typing import AsyncIterator, List

@dataclass
class LLMMessage:
    role: str
    content: str

class LLMBase:
    async def stream(self, messages: List[LLMMessage]) -> AsyncIterator[str]:
        raise NotImplementedError
