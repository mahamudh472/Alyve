from django.conf import settings

from .rag_base import RAGBase
from .rag_chroma import ChromaRAG


def get_rag() -> RAGBase:
    provider = (settings.VOICE_APP.get("VECTOR_DB") or "chroma").lower()

    if provider == "chroma":
        return ChromaRAG(settings.VOICE_APP.get("CHROMA_DIR", ""))

    # Placeholder for later:
    # if provider == "pinecone":
    #     from .rag_pinecone import PineconeRAG
    #     return PineconeRAG(...)

    raise ValueError(f"Unsupported VECTOR_DB provider: {provider}")
