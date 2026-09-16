"""Tests: memory stores (tenant-scoped, TTL, provenance) + knowledge RAG."""
from __future__ import annotations

import pytest

from app.core import contracts
from app.knowledge.store import KnowledgeStore
from app.memory.stores import MemoryStore

DOC = (
    "Azienda's Agent Control Plane routes tasks through a governed pipeline. "
    "Every tool call passes the policy engine before execution. "
    "Approvals are required for bulk messaging and financial actions. "
    "The AGRL ledger records goals, resources, plans, and outcomes as "
    "hash-chained events. Budgets are reserved before spend."
)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
async def test_memory_put_get_roundtrip(svc, tenant):
    mem: MemoryStore = svc["memory"]
    await mem.put(tenant, "short_term", "pref", {"theme": "dark"})
    got = await mem.get(tenant, "short_term", "pref")
    assert got is not None and got["value"] == {"theme": "dark"}
    assert got["provenance"] == {}
    assert 0.0 <= got["confidence"] <= 1.0


async def test_memory_semantic_requires_provenance(svc, tenant):
    mem: MemoryStore = svc["memory"]
    with pytest.raises(ValueError):
        await mem.put(tenant, "semantic", "fact", {"x": 1})
    await mem.put(tenant, "semantic", "fact",
                  {"x": 1, "_provenance": {"source": "doc-1"}})
    got = await mem.get(tenant, "semantic", "fact")
    assert got is not None and got["provenance"] == {"source": "doc-1"}


async def test_memory_forget_deletes(svc, tenant):
    mem: MemoryStore = svc["memory"]
    await mem.put(tenant, "short_term", "tmp", {"v": 1})
    await mem.forget(tenant, "short_term", "tmp")
    assert await mem.get(tenant, "short_term", "tmp") is None


async def test_memory_ttl_expiry(svc, tenant):
    mem: MemoryStore = svc["memory"]
    await mem.put(tenant, "short_term", "fast", {"v": 1}, ttl_seconds=-1)
    assert await mem.get(tenant, "short_term", "fast") is None


async def test_memory_namespace_isolation_and_tenant_isolation(svc, tenant, tenant_b):
    mem: MemoryStore = svc["memory"]
    await mem.put(tenant, "short_term", "k", {"v": "a"})
    # same key, different namespace → invisible
    assert await mem.get(tenant, "episodic", "k") is None
    # same key, different tenant → invisible
    assert await mem.get(tenant_b, "short_term", "k") is None
    # unknown namespace → None, not an exception
    assert await mem.get(tenant, "nope", "k") is None


# ---------------------------------------------------------------------------
# Knowledge: ingestion → hybrid retrieval with provenance
# ---------------------------------------------------------------------------
async def _ingest(svc, tenant, text: str = DOC, title: str = "ACP overview"):
    ks: KnowledgeStore = svc["knowledge"]
    src = await ks.register_source(tenant, title=title,
                                   uri="doc://acp/overview", text=text)
    doc_id = await ks.ingest(tenant, src)
    return ks, doc_id


async def test_knowledge_ingest_and_search_with_citations(svc, tenant):
    ks, doc_id = await _ingest(svc, tenant)
    hits = await ks.search(tenant, "policy engine tool call approvals", top_k=3)
    assert hits, "expected hits for a query overlapping the document"
    top = hits[0]
    assert isinstance(top, contracts.KnowledgeHit)
    assert top.document_id == doc_id
    c = top.citations
    assert c["title"] == "ACP overview"
    assert c["uri"] == "doc://acp/overview"
    assert "chunk_ordinal" in c
    assert c["score_breakdown"]["w_vector"] + c["score_breakdown"]["w_keyword"] == \
        pytest.approx(1.0)
    assert c["embedding_kind"], "score provenance must name the embedding kind"


async def test_knowledge_hybrid_ranking_prefers_keyword_overlap(svc, tenant):
    ks: KnowledgeStore = svc["knowledge"]
    await _ingest(svc, tenant, text="Zebra xylophone quantum widgets.")
    await _ingest(svc, tenant, text="Budgets are reserved before agent spend happens.",
                  title="budgets")
    hits = await ks.search(tenant, "budgets reserved before spend", top_k=2)
    assert hits and hits[0].citations["title"] == "budgets"


async def test_knowledge_ingest_is_idempotent(svc, tenant):
    ks: KnowledgeStore = svc["knowledge"]
    src = await ks.register_source(tenant, title="t", uri="doc://x", text=DOC)
    d1 = await ks.ingest(tenant, src)
    n_chunks_1 = len(ks._chunks[tenant.tenant_id])
    d2 = await ks.ingest(tenant, src)
    n_chunks_2 = len(ks._chunks[tenant.tenant_id])
    assert d1 == d2
    assert n_chunks_1 == n_chunks_2, "re-ingest must not duplicate chunks"


async def test_knowledge_tenant_isolation(svc, tenant, tenant_b):
    ks, doc_id = await _ingest(svc, tenant)
    hits_b = await ks.search(tenant_b, "policy engine", top_k=5)
    assert hits_b == []
    hits_a = await ks.search(tenant, "policy engine", top_k=5)
    assert any(h.document_id == doc_id for h in hits_a)


async def test_knowledge_hash_embedding_is_labeled(svc):
    kind = svc["knowledge"].embedding_kind.lower()
    assert kind.startswith("hash-"), \
        "test/dev embeddings must be labeled non-semantic"
    assert "test" in kind or "fallback" in kind


async def test_knowledge_search_filters_by_source(svc, tenant):
    ks, doc_id = await _ingest(svc, tenant)
    src2 = await ks.register_source(tenant, title="other", uri="doc://other",
                                    text="Completely unrelated content here.")
    await ks.ingest(tenant, src2)
    only_first = [s for s in (await ks.search(tenant, "AGRL", top_k=10,
                                              filters={"source_id": "nope"}))]
    assert only_first == []
