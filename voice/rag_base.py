from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod


@dataclass
class RAGResult:
    docs: List[str]
    metadatas: List[Dict[str, Any]]


class RAGBase(ABC):
    @abstractmethod
    def add_memory(
        self,
        *,
        profile_id: str,
        loved_one_id: int,
        text: str,
        memory_id: str,
    ) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def query(
        self,
        *,
        profile_id: str,
        loved_one_id: int,
        query_text: str,
        k: int = 5,
    ) -> RAGResult:
        raise NotImplementedError
