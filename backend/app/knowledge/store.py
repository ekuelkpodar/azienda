"""RAG substrate: ingestion, chunking, embeddings, hybrid search.

Implements ``core.contracts.KnowledgeStore`` (search/ingest) with
citation-bearing results.

Embeddings sit behind the ``EmbeddingProvider`` protocol:
- ``HashEmbeddingProvider`` — DETERMINISTIC, TEST/FALLBACK ONLY. SHA-256
  seeded pseudo-vectors; NOT semantic. Lets the retrieval pipeline, ranking,
  and tenant isolation be tested with zero network and zero keys.
- ``LiteLLMEmbeddingProvider`` — real embeddings via litellm when importable
  (needs provider API keys in env). Clearly labeled; not used in tests.

Hybrid search = cosine similarity + keyword overlap, tenant filter applied
FIRST (one logical query; in production a single SQL query with HNSW).

GraphRAG: ``knowledge_edges`` schema ships in the migration and edges can be
stored/listed here; graph TRAVERSAL is a documented future (not implemented —
no fake traversal).

This is the in-memory MVP adapter. Production: SQLAlchemy + pgvector over
``knowledge_sources`` / ``documents`` / ``document_chunks`` (DATABASE.md §8).
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from app.core import contracts

LITELLM_AVAILABLE = importlib.util.find_spec("litellm") is not None

VECTOR_DIM = 128  # hash-embedding dim (MVP). Production pgvector: 1536.


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- chunking
def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """Sentence-aware splitter: packs sentences into ~chunk_size char chunks."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sent in sentences:
        if current and current_len + len(sent) > chunk_size:
            chunks.append(" ".join(current))
            # overlap: carry the tail forward
            tail: list[str] = []
            tail_len = 0
            for s in reversed(current):
                if tail_len + len(s) > overlap:
                    break
                tail.insert(0, s)
                tail_len += len(s)
            current, current_len = tail, tail_len
        current.append(sent)
        current_len += len(sent) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


# ---------------------------------------------------------------- embeddings
@runtime_checkable
class EmbeddingProvider(Protocol):
    kind: str  # human-readable label of what this provider is

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbeddingProvider:
    """TEST/FALLBACK ONLY — deterministic non-semantic vectors.

    Derives a unit vector from SHA-256(text). Useful for pipeline tests;
    MUST NOT be presented as semantic search quality.
    """

    kind = "hash-embedding (TEST/FALLBACK ONLY — not semantic)"

    def __init__(self, dim: int = VECTOR_DIM) -> None:
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec: list[float] = []
        counter = 0
        while len(vec) < self.dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            vec.extend(b / 255.0 - 0.5 for b in digest)
            counter += 1
        norm = math.sqrt(sum(v * v for v in vec[: self.dim])) or 1.0
        return [v / norm for v in vec[: self.dim]]


class LiteLLMEmbeddingProvider:
    """Real embeddings via litellm (needs provider API keys in env)."""

    kind = "litellm-embeddings"

    def __init__(self, model: str = "openai/text-embedding-3-small") -> None:
        if not LITELLM_AVAILABLE:
            raise RuntimeError("litellm is not installed")
        import litellm  # type: ignore[import-not-found]
        self._litellm = litellm
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import asyncio as _asyncio
        resp = await _asyncio.to_thread(self._litellm.embedding,
                                        model=self._model, input=texts)
        return [list(d["embedding"]) for d in resp.data]


def cosine(a: list[float], b: list[float]) -> float:
    denom = (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / denom


def keyword_overlap(query: str, text: str) -> float:
    q = set(re.findall(r"\w+", query.lower()))
    t = set(re.findall(r"\w+", text.lower()))
    q = {w for w in q if len(w) > 2}
    if not q:
        return 0.0
    return len(q & t) / len(q)


# ---------------------------------------------------------------- records
@dataclass
class DocumentRecord:
    document_id: str
    source_id: str
    title: str
    uri: str
    metadata: dict[str, Any] = field(default_factory=dict)
    checksum: str = ""
    chunk_count: int = 0
    ingested_at: datetime = field(default_factory=_utcnow)


@dataclass
class ChunkRecord:
    chunk_id: str
    document_id: str
    ordinal: int
    text: str
    embedding: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeEdge:
    """GraphRAG-future edge. Stored and listed; traversal NOT implemented."""
    from_chunk_id: str
    to_chunk_id: str
    relation: str
    weight: float = 1.0


class KnowledgeStore:
    """In-memory KnowledgeStore (dev/test adapter)."""

    # Hybrid weights (ASSUMED; tune with eval).
    W_VECTOR = 0.6
    W_KEYWORD = 0.4

    def __init__(self, embedding: EmbeddingProvider | None = None) -> None:
        self._embedding = embedding or HashEmbeddingProvider()
        # tenant_id -> document_id -> DocumentRecord
        self._documents: dict[str, dict[str, DocumentRecord]] = {}
        # tenant_id -> chunk_id -> ChunkRecord
        self._chunks: dict[str, dict[str, ChunkRecord]] = {}
        # tenant_id -> list[KnowledgeEdge]
        self._edges: dict[str, list[KnowledgeEdge]] = {}
        self._sources: dict[str, dict[str, Any]] = {}

    @property
    def embedding_kind(self) -> str:
        return self._embedding.kind

    # -- contracts.KnowledgeStore ------------------------------------------------
    async def ingest(self, tenant: contracts.TenantContext,
                     source_id: str) -> str:
        """Ingest a pre-registered source. See ingest_document for direct use."""
        source = self._sources.get(tenant.tenant_id, {}).get(source_id)
        if source is None:
            raise KeyError(f"unknown knowledge source '{source_id}'")
        doc_id = await self.ingest_document(
            tenant, source_id=source_id, title=source["title"],
            uri=source["uri"], text=source["text"],
            metadata=source.get("metadata", {}))
        return doc_id

    async def search(self, tenant: contracts.TenantContext, query: str,
                     top_k: int = 8,
                     filters: dict[str, Any] | None = None) -> list[contracts.KnowledgeHit]:
        # Tenant filter FIRST — cross-tenant chunks are never ranked.
        chunks = list(self._chunks.get(tenant.tenant_id, {}).values())
        if filters:
            if "source_id" in filters:
                doc_ids = {d.document_id for d in
                           self._documents.get(tenant.tenant_id, {}).values()
                           if d.source_id == filters["source_id"]}
                chunks = [c for c in chunks if c.document_id in doc_ids]
        if not chunks or not query.strip():
            return []
        qvec = (await self._embedding.embed([query]))[0]
        scored: list[tuple[float, float, float, ChunkRecord]] = []
        for c in chunks:
            v = cosine(qvec, c.embedding) if c.embedding else 0.0
            k = keyword_overlap(query, c.text)
            total = self.W_VECTOR * v + self.W_KEYWORD * k
            scored.append((total, v, k, c))
        scored.sort(key=lambda s: s[0], reverse=True)
        hits: list[contracts.KnowledgeHit] = []
        for total, v, k, c in scored[:top_k]:
            doc = self._documents[tenant.tenant_id][c.document_id]
            hits.append(contracts.KnowledgeHit(
                chunk_id=c.chunk_id, document_id=c.document_id, text=c.text,
                score=round(total, 4),
                citations={
                    "document_id": c.document_id, "title": doc.title,
                    "uri": doc.uri, "chunk_ordinal": c.ordinal,
                    "source_id": doc.source_id,
                    "score_breakdown": {"vector": round(v, 4), "keyword": round(k, 4),
                                        "w_vector": self.W_VECTOR,
                                        "w_keyword": self.W_KEYWORD},
                    "embedding_kind": self.embedding_kind,
                    "retrieved_at": _utcnow().isoformat(),
                }))
        return hits

    # -- ingestion -----------------------------------------------------------------
    async def register_source(self, tenant: contracts.TenantContext, *,
                              title: str, uri: str, text: str,
                              kind: str = "document",
                              metadata: dict[str, Any] | None = None) -> str:
        source_id = f"src-{uuid.uuid4().hex[:12]}"
        self._sources.setdefault(tenant.tenant_id, {})[source_id] = {
            "source_id": source_id, "kind": kind, "title": title, "uri": uri,
            "text": text, "metadata": metadata or {},
            "status": "pending", "last_ingested_at": None,
        }
        return source_id

    async def ingest_document(self, tenant: contracts.TenantContext, *,
                              source_id: str, title: str, uri: str, text: str,
                              metadata: dict[str, Any] | None = None,
                              chunk_size: int = 800,
                              overlap: int = 100) -> str:
        checksum = hashlib.sha256(text.encode()).hexdigest()
        # Idempotent re-ingest: same checksum => same document (skip duplicates).
        for doc in self._documents.get(tenant.tenant_id, {}).values():
            if doc.source_id == source_id and doc.checksum == checksum:
                return doc.document_id
        document_id = f"doc-{uuid.uuid4().hex[:12]}"
        chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
        vectors = await self._embedding.embed(chunks) if chunks else []
        doc = DocumentRecord(document_id=document_id, source_id=source_id,
                             title=title, uri=uri, metadata=metadata or {},
                             checksum=checksum, chunk_count=len(chunks))
        self._documents.setdefault(tenant.tenant_id, {})[document_id] = doc
        store = self._chunks.setdefault(tenant.tenant_id, {})
        for i, (ctext, vec) in enumerate(zip(chunks, vectors, strict=True)):
            cid = f"ch-{uuid.uuid4().hex[:12]}"
            store[cid] = ChunkRecord(chunk_id=cid, document_id=document_id,
                                     ordinal=i, text=ctext, embedding=vec)
        src = self._sources.get(tenant.tenant_id, {}).get(source_id)
        if src:
            src["status"] = "ingested"
            src["last_ingested_at"] = _utcnow().isoformat()
        return document_id

    async def documents(self, tenant: contracts.TenantContext) -> list[DocumentRecord]:
        return list(self._documents.get(tenant.tenant_id, {}).values())

    # -- edges (GraphRAG future: storage only) --------------------------------------
    async def add_edge(self, tenant: contracts.TenantContext, *,
                       from_chunk_id: str, to_chunk_id: str,
                       relation: str, weight: float = 1.0) -> None:
        """Store a knowledge edge. Graph TRAVERSAL is a documented future."""
        chunks = self._chunks.get(tenant.tenant_id, {})
        if from_chunk_id not in chunks or to_chunk_id not in chunks:
            raise KeyError("edge endpoints must be chunks in this tenant")
        self._edges.setdefault(tenant.tenant_id, []).append(
            KnowledgeEdge(from_chunk_id=from_chunk_id, to_chunk_id=to_chunk_id,
                          relation=relation, weight=weight))

    async def edges(self, tenant: contracts.TenantContext) -> list[KnowledgeEdge]:
        return list(self._edges.get(tenant.tenant_id, []))
