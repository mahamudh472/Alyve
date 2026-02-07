from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import hashlib
import re

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from .rag_base import RAGBase, RAGResult


class ChromaRAG(RAGBase):
    _EMBEDDER: Optional[SentenceTransformer] = None

    def __init__(self, persist_dir: str):
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(name="memories")
        self.embedder = self._get_embedder()

    @classmethod
    def _get_embedder(cls) -> SentenceTransformer:
        if cls._EMBEDDER is None:
            cls._EMBEDDER = SentenceTransformer("all-MiniLM-L6-v2")
        return cls._EMBEDDER

    @staticmethod
    def _norm_text(s: str) -> str:
        s = (s or "").strip()
        s = re.sub(r"\s+", " ", s)
        return s

    @staticmethod
    def _text_hash(profile_id: str, loved_one_id: int, text: str) -> str:
        h = hashlib.sha256()
        h.update(profile_id.encode("utf-8"))
        h.update(b"\x00")
        h.update(str(loved_one_id).encode("utf-8"))
        h.update(b"\x00")
        h.update(text.encode("utf-8"))
        return h.hexdigest()

    @staticmethod
    def _chunk_text(text: str, *, max_chars: int = 900, overlap_chars: int = 140) -> List[str]:
        t = (text or "").strip()
        if len(t) <= max_chars:
            return [t] if t else []

        chunks: List[str] = []
        start = 0
        n = len(t)
        while start < n:
            end = min(n, start + max_chars)

            window = t[start:end]
            cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
            if cut > int(max_chars * 0.6):
                end = start + cut + 1

            chunk = t[start:end].strip()
            if chunk:
                chunks.append(chunk)

            if end >= n:
                break
            start = max(0, end - overlap_chars)

        return chunks

    def add_memory(
        self,
        *,
        profile_id: str,
        loved_one_id: int,
        text: str,
        memory_id: str,
        chunk_long: bool = True,
        max_chars: int = 900,
        overlap_chars: int = 140,
        dedup_exact: bool = True,
    ) -> List[str]:
        text_n = self._norm_text(text)
        if not text_n:
            return []

        parts = self._chunk_text(text_n, max_chars=max_chars, overlap_chars=overlap_chars) if chunk_long else [text_n]
        inserted: List[str] = []

        for i, part in enumerate(parts):
            part_n = self._norm_text(part)
            if not part_n:
                continue

            h = self._text_hash(profile_id, loved_one_id, part_n) if dedup_exact else ""

            # dedup check
            if dedup_exact:
                try:
                    existing = self.collection.get(
                        where={"profile_id": profile_id, "loved_one_id": int(loved_one_id), "hash": h},
                        include=["ids"],
                    )
                    if existing and existing.get("ids"):
                        continue
                except Exception:
                    pass

            vid = f"{memory_id}:{i}" if len(parts) > 1 else str(memory_id)
            emb = self.embedder.encode([part_n])[0].tolist()

            meta: Dict[str, Any] = {
                "profile_id": profile_id,
                "loved_one_id": int(loved_one_id),
            }
            if dedup_exact:
                meta["hash"] = h
            if len(parts) > 1:
                meta["chunk_index"] = i
                meta["chunk_total"] = len(parts)

            self.collection.add(
                ids=[vid],
                embeddings=[emb],
                documents=[part_n],
                metadatas=[meta],
            )
            inserted.append(vid)

        return inserted

    @staticmethod
    def _tokenize(s: str) -> set:
        s = (s or "").lower()
        toks = re.findall(r"[a-z0-9']+", s)
        return set(toks)

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        inter = len(a & b)
        union = len(a | b)
        return inter / max(1, union)

    def query(
        self,
        *,
        profile_id: str,
        loved_one_id: int,
        query_text: str,
        k: int = 5,
        max_return_chars: int = 1600,
        diversify: bool = True,
        diversity_jaccard_threshold: float = 0.72,
        candidate_k: Optional[int] = None,
    ) -> RAGResult:
        q = self._norm_text(query_text or "")
        if not q:
            return RAGResult(docs=[], metadatas=[])

        candidate_k = candidate_k or max(12, k * 3)

        emb = self.embedder.encode([q])[0].tolist()
        res = self.collection.query(
            query_embeddings=[emb],
            n_results=candidate_k,
            where={"profile_id": profile_id, "loved_one_id": int(loved_one_id)},
            include=["documents", "metadatas"],
        )

        docs_all = res.get("documents", [[]])[0] if res else []
        metas_all = res.get("metadatas", [[]])[0] if res else []

        picked_docs: List[str] = []
        picked_metas: List[Dict[str, Any]] = []
        total_chars = 0
        picked_token_sets: List[set] = []

        for doc, meta in zip(docs_all, metas_all):
            d = (doc or "").strip()
            if not d:
                continue
            if total_chars >= max_return_chars:
                break

            if diversify:
                dtoks = self._tokenize(d)
                too_similar = any(self._jaccard(dtoks, pt) >= diversity_jaccard_threshold for pt in picked_token_sets)
                if too_similar:
                    continue
                picked_token_sets.append(dtoks)

            picked_docs.append(d)
            picked_metas.append(meta or {})
            total_chars += len(d)

            if len(picked_docs) >= k:
                break

        return RAGResult(docs=picked_docs, metadatas=picked_metas)
