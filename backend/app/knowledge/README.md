# knowledge

**Status:** implemented (in-memory dev/test adapters; pgvector production path pending).
Idempotent document ingestion with chunking; hybrid retrieval (vector + keyword) with
citation-bearing results and tenant/source filters; embedding provider interface with a
deterministic hash-embedding fallback (labeled, non-semantic — tests only). Cross-tenant
invisibility enforced at the adapter level.

## Boundary
RAG substrate: sources, ingested documents, pgvector chunks (HNSW), entity/edge tables for MVP GraphRAG, retrieval behind the `KnowledgeStore` interface.

## Owned tables
`knowledge_sources`, `documents`, `document_chunks` (pgvector embedding), `knowledge_edges`.

## Public interfaces
Implements `KnowledgeStore.search/ingest`; hybrid (vector + full-text + tenant filter) in one query; citation-bearing results.

## Consumes
`core`. Retrieval is read-only at request time; ingestion is a background job.

## Non-goals
A dedicated vector DB (pgvector suffices at MVP scale — ADR-002); a graph server.

## Rules for builders
1. Import other packages ONLY through `core/contracts.py` protocols (or this package's
   own public interface). No cross-package private imports — CI enforces this with import-lint.
2. Every row this package writes carries `tenant_id`. Every query filters by it. Add a
   cross-tenant invisibility test for every new table.
3. Mutations that matter emit AGRL/domain events via the `EventBus`; the Postgres ledger
   is truth, the bus is transport.
4. Anything irreversible, financial, bulk, or externally visible goes through the
   governance rail (`governance/`) BEFORE execution. Fail closed.
5. No secrets in code or logs. Use the `SecretBroker` interface; config comes from env.
