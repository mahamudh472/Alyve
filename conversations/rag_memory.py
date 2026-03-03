from __future__ import annotations

from typing import Any, Dict, List

from voice.rag_factory import get_rag

_rag = get_rag()


def profile_key_from_user(user) -> str:
    # user is authenticated always; id is UUID -> convert to stable string key
    return str(user.id)


def retrieve_context(*, profile_key: str, loved_one_id: int, query_text: str, k: int = 6) -> List[Dict[str, Any]]:
    """
    Normalize multiple possible RAG backends:

    - Some backends return List[Dict[str, Any]] like:
        {"text": "...", "score": 0.12, "metadata": {...}, "memory_id": "..."}
    - ChromaRAG (voice) returns a RAGResult object with:
        .docs: List[str]
        .metadatas: List[Dict[str, Any]]
    """
    try:
        results = _rag.query(
            profile_id=profile_key,
            loved_one_id=int(loved_one_id),
            query_text=query_text,
            k=int(k),
        )
    except Exception:
        return []

    # Case 1: already the expected shape
    if isinstance(results, list):
        # Ensure dict-like items
        out: List[Dict[str, Any]] = []
        for r in results:
            if isinstance(r, dict):
                out.append(r)
        return out

    # Case 2: RAGResult-like object (docs/metadatas)
    docs = getattr(results, "docs", None)
    metas = getattr(results, "metadatas", None)

    if isinstance(docs, list):
        metas_list: List[Dict[str, Any]] = metas if isinstance(metas, list) else []
        if len(metas_list) < len(docs):
            metas_list = metas_list + [{} for _ in range(len(docs) - len(metas_list))]

        out: List[Dict[str, Any]] = []
        for d, m in zip(docs, metas_list):
            text = (d or "").strip()
            if not text:
                continue
            out.append({"text": text, "metadata": (m or {})})
        return out

    # Unknown shape -> no context
    return []


def format_context_block(hits: List[Dict[str, Any]]) -> str:
    if not hits:
        return "CONTEXT:\n(none)\n"

    lines = ["CONTEXT:"]
    for i, h in enumerate(hits, start=1):
        txt = (h.get("text") or "").strip()
        if not txt:
            continue
        lines.append(f"[{i}] {txt}")
    if len(lines) == 1:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def write_chat_turn_to_rag(
    *,
    profile_key: str,
    loved_one_id: int,
    text: str,
    memory_id: str,
    metadata: Dict[str, Any] | None = None,
) -> Any:
    """
    Best-effort write so chat never 500s if RAG backend is down.
    """
    try:
        return _rag.add_memory(
            profile_id=profile_key,
            loved_one_id=int(loved_one_id),
            text=text,
            memory_id=memory_id,
            metadata=metadata or {},
        )
    except Exception:
        return None