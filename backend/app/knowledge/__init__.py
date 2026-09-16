"""Package: knowledge — RAG substrate (ingest, chunk, embed, hybrid search)."""
from app.knowledge.store import (
    ChunkRecord,
    DocumentRecord,
    EmbeddingProvider,
    HashEmbeddingProvider,
    KnowledgeEdge,
    KnowledgeStore,
    LiteLLMEmbeddingProvider,
    chunk_text,
    cosine,
    keyword_overlap,
)

__all__ = [
    "KnowledgeStore", "EmbeddingProvider", "HashEmbeddingProvider",
    "LiteLLMEmbeddingProvider", "chunk_text", "cosine", "keyword_overlap",
    "DocumentRecord", "ChunkRecord", "KnowledgeEdge",
]
