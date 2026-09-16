# AI Business OS — Stack & Architecture Decision Document

**Status:** DRAFT for architecture-phase adoption (mandate §32–§35, §60)
**Date:** 2026-09-15
**Author:** technical architecture research (subagent)
**Constraint context:** Modular monolith MVP, clean package boundaries. Spine fixed:
Business Applications → AI Agents → Agent Control Plane → Governance/Security →
Data/Knowledge → Integrations. Do NOT reimplement `agent-control-plane` or `nexora-erp`;
reuse their proven patterns.

**Evidence labels used throughout:** CONFIRMED (verified in tool output/docs),
LIKELY (strong secondary evidence), ASSUMED (reasonable but unverified),
UNKNOWN (cannot verify — do not act on as fact).

---

## 1. Decision Summary Table

| Component | Chosen | Alternatives rejected | Why (one line) |
|---|---|---|---|
| Backend framework | **FastAPI (Python 3.12/3.13), Pydantic v2** | NestJS, Django/DRF, Litestar, Go, .NET | Reuses the proven `agent-control-plane` pattern; every AI lib (LangGraph, LiteLLM, MCP SDK) is Python-first; async-native; best DX-to-power ratio for LLM-heavy backends |
| Language runtime | **Python 3.12 (uv-managed)** | 3.13, 3.11 | 3.12 is the current sweet spot: all wheels available, 3.9/3.10 EOL pressure; 3.13 adopted after dependency wheels verified |
| Packaging / toolchain | **uv + ruff + pytest** | pip/poetry, black/flake8 | 10–100× faster envs; single Rust toolchain for lint+format (already the workspace convention) |
| Frontend | **React 19 + Vite + TypeScript SPA, served same-origin by FastAPI** | Next.js 16, Angular, SvelteKit | Dashboard is behind login (SEO irrelevant); single deploy artifact, no second runtime; mirrors the AI-native-TMS pattern that already shipped; Next.js deferred until public pages/SEO need exists |
| Styling / components | **Tailwind CSS v4 + headless components owned in-repo (Radix/shadcn pattern)** | MUI, Ant Design, Bootstrap | Full ownership, no dependency lock-in; matches modern dashboard templates' 2026 baseline |
| Primary database | **PostgreSQL 16/17 + pgvector** | MySQL, MongoDB, SQLite, CockroachDB | One system of record; JSONB + relational + vector in one engine; Postgres is the proven spine in both workspace repos |
| Vector search | **pgvector (HNSW) inside Postgres** | Pinecone, Qdrant, Weaviate, Milvus, Chroma | <1M vectors: pgvector suffices (CONFIRMED by AWS guidance + community benchmarks); tenant-filtered hybrid search in one SQL query; zero new infra |
| Graph needs | **Relational entity/edge tables in Postgres for MVP; embedded Kuzu documented as step-up** | Neo4j (server), Neptune | Dedicated graph server is overkill for MVP GraphRAG; Kuzu is embedded, zero-ops, swappable later |
| Cache / sessions / rate limits | **Redis 7** | Memcached, Valkey, in-process | Proven in ACP (rate limits, cache, pub/sub); Redis Streams is the documented step-up event path. Note licensing: Redis is RSAL/SSPL since 2024 — Valkey is the drop-in OSS escape hatch |
| Object storage | **S3-compatible API (MinIO locally; any S3 provider in prod)** | Local disk only, GCS/Azure-native APIs | S3 API is the de-facto standard; zero lock-in; audit artifacts, uploads, exports |
| Event bus | **In-process asyncio bus behind an `EventBus` interface; Redis Streams as documented step-up** | Kafka, Redpanda, NATS-as-day-one | Kafka/Redpanda are operational overkill for MVP event volumes; interface makes the upgrade a config change, not a rewrite |
| Agent orchestration | **LangGraph (1.x) for agent loops with Postgres checkpointer; custom `WorkflowBackend` interface for macro-workflows** | Temporal as day-one, custom engine, CrewAI/AutoGen | LangGraph is the agent-loop standard in 2026 (battle-tested at Uber/LinkedIn/Klarna); Temporal's operational weight isn't justified at MVP scale — but the interface preserves the swap path |
| Model abstraction | **LiteLLM (direct SDK + `Router` in-process for MVP)** | Raw provider SDKs only, custom abstraction, LiteLLM Proxy day-one, OpenRouter-as-only | One call signature across OpenAI/Anthropic/Google/Bedrock/OpenRouter/Ollama; built-in fallbacks, retries, cost tracking (~6.5ms overhead measured 2026-07); Proxy deferred until multi-team gateway need exists |
| MCP support | **Official `mcp` Python SDK v1.x (stable) for clients; FastMCP for exposing our tools as servers** | MCP SDK v2 pre-release, hand-rolled protocol | v1.x is the only stable line (CONFIRMED via SDK README); v2 pre-releases may break; FastMCP is the 2026 de-facto for exposing tools |
| Auth (humans) | **JWT: short-lived access + rotating refresh; RBAC; OIDC interface for SSO later** | Sessions-in-Redis only, opaque tokens, rolling own crypto | Stateless API tier scales horizontally; rotation bounds theft; never roll custom crypto |
| Tenant isolation | **Application-enforced `tenant_id` on every row (primary) + Postgres RLS as defense-in-depth** | Schema-per-tenant, DB-per-tenant, RLS-only | Matches ACP pattern; row-level is cheapest to operate; RLS as backstop, not the only gate |
| Migrations | **Alembic** | Prisma, hand-rolled SQL, Django migrations | Already the workspace convention (ACP); SQLAlchemy 2.0 async works; versioned, reviewable |
| ORM | **SQLAlchemy 2.0 (async)** | SQLModel, raw SQL, Prisma (Node) | Proven in ACP; full control for RLS/pgvector/complex queries |
| Observability | **OpenTelemetry (traces/metrics/logs) → OTLP; Prometheus+Grafana stack; structured JSON logs (structlog)** | Datadog/New Relic day-one, Langfuse day-one, logs-only | Vendor-neutral, matches ACP; Langfuse optional later for LLM-specific observability |
| Background jobs | **ARQ (Redis-backed, asyncio-native) behind a `TaskQueue` interface** | Celery, Dramatiq, Temporal day-one | Celery's operational weight and sync-first design are wrong for an asyncio codebase; ARQ is the modern asyncio-native pick |
| CI/CD | **GitHub Actions: ruff → mypy → pytest → docker build → (staging deploy)** | Jenkins, GitLab CI, no CI | Already the workspace convention; free for public repos |
| Local dev | **`docker compose up` (api, worker, postgres+pgvector, redis, minio)** | Kind/minikube, bare-metal scripts | `git clone → cp .env.example .env → docker compose up` is the mandated DX bar |

---

## 2. Per-Component Tradeoff Writeups

### 2.1 Backend framework — FastAPI wins

**Chosen: FastAPI ≥0.126 on Python 3.12, Pydantic v2, uvicorn[standard], uv + ruff.**

- **Reuse:** `agent-control-plane` is a FastAPI modular monolith with the exact
  package-boundary discipline this build mandates (`src/` bounded packages,
  Alembic, OTel, structlog, Redis, SSE). Reusing the pattern collapses
  architecture risk — the build team already knows what "done" looks like.
  [CONFIRMED — read pyproject.toml + ARCHITECTURE.md]
- **Ecosystem fit:** LangGraph, LiteLLM, the official MCP Python SDK, and
  FastMCP are all Python-first. Choosing Node or Go would force the agent
  layer to live in a second language or behind FFI/IPC. [CONFIRMED]
- **2026 state:** FastAPI ≥0.126 requires Pydantic v2 (v1 support dropped at
  0.126.0, `pydantic.v1` shim dropped at 0.128.0); Pydantic v1 is unsupported
  on Python 3.14. Target Pydantic v2 exclusively. [CONFIRMED — fastapi.tiangolo.com
  migration guide]
- **Rejected — NestJS:** excellent framework, but it would strand the Python
  agent ecosystem and duplicate what NEXORA (Fastify/Node) already covers in
  the workspace. Two languages for the agent-heavy layer buys nothing.
  [ASSUMED — judgment call, not benchmarked]
- **Rejected — Django/DRF:** sync-first ORM heritage; async support still
  second-class for the SSE/streaming-heavy agent UI this OS needs. Heavier
  than needed for an API-only backend. [LIKELY]
- **Rejected — Litestar:** genuinely faster (community benchmarks claim ~2×
  RPS, −55% p99 vs FastAPI) with built-in rate limiting/caching, but the AI
  ecosystem (docs, examples, hiring, agent-library integrations) is
  FastAPI-shaped. Raw RPS is not the bottleneck of an LLM-orchestrated
  system — model latency is. [LIKELY — benchmark figures from a community
  post, not independently verified]
- **Rejected — Go / .NET:** throughput wins that don't matter here (LLM calls
  dominate latency), at the cost of losing the Python AI ecosystem. Revisit
  only if a specific hot path (e.g., high-RPS tool proxy) proves
  Python-bound. [ASSUMED]

**Tradeoffs accepted:** Python's GIL and per-worker memory (~85MB/worker in
community benchmarks) vs Litestar/Go; mitigated by keeping the API tier
stateless and horizontally scalable. Dependency churn in the LangChain-adjacent
ecosystem — mitigate with pinned `uv.lock` and the interface-boundary rule.

### 2.2 Frontend — React+Vite+TS SPA (same-origin), not Next.js

**Chosen: React 19 + Vite + TypeScript SPA, Tailwind v4, owned headless
component set (Radix primitives + shadcn-style composition), static build
served by FastAPI on the same origin (`/api/v1` + SPA fallback).**

- **Why not Next.js:** The OS console is an authenticated app — SEO is
  irrelevant, and every page sits behind login. Next.js buys SSR/SSG and a
  Node runtime the project otherwise doesn't need. A second runtime doubles
  the deploy surface (two Docker images, two scaling stories) for zero user
  value at MVP. [ASSUMED — architectural judgment]
- **Why SPA same-origin:** The AI-native-TMS build already proved this pattern
  in this workspace (FastAPI serves built Vite SPA, same-origin `/api/v1`,
  single Render deploy). One artifact, one container, no CORS, cookies work
  for refresh tokens. [CONFIRMED — MEMORY.md deploy record]
- **Component library:** shadcn-style (copy-owned components over Radix
  primitives) over MUI/AntD — full ownership, no dependency lock-in, and it
  is the 2026 default for new dashboards per template surveys. [LIKELY]
- **Deferred:** Next.js App Router if/when public marketing pages, docs, or
  SEO-indexed content become part of the product. Recorded in ADR-08.

### 2.3 Database spine — Postgres + pgvector; nothing else at MVP

**Chosen: PostgreSQL 16 (17/18 when images stabilize) as the single system of
record, with the pgvector extension for embeddings. SQLAlchemy 2.0 async +
Alembic.**

- **Postgres is the spine:** relational accounting-style ledgers (immutable
  journals, audit hash chains, approvals), JSONB for flexible agent state,
  full-text search for hybrid retrieval, and pgvector for embeddings — one
  engine, one backup story, one transaction model. Both workspace repos made
  the same call independently. [CONFIRMED — ACP + NEXORA both Postgres-backed]
- **pgvector, not a dedicated vector DB:** For <1M vectors pgvector "often
  suffices" (AWS Prescriptive Guidance); HNSW indexes, cosine/L2/IP distance,
  hybrid vector+SQL predicates (tenant filtering *before* ANN ranking —
  something separate vector DBs make awkward). pgvector 0.8.2 (2026-02-26)
  fixed a parallel-HNSW CVE — pin ≥0.8.2. The escape hatch is real: dedicated
  DBs (Qdrant self-hosted, Pinecone serverless) pull ahead past ~10M vectors
  or extreme QPS; the retrieval layer gets an interface so the swap is
  contained. [CONFIRMED — AWS guidance + pgvector release notes; scale
  thresholds LIKELY]
- **Graph DB — not for MVP:** GraphRAG at MVP scale is entity/edge tables in
  Postgres + pgvector for the vector half. A dedicated graph server (Neo4j,
  Neptune) adds an operational dependency before the query patterns are even
  known. Documented step-up: **Kuzu** (embedded, zero-ops, Cypher) behind a
  `GraphStore` interface. [ASSUMED — judgment; Kuzu's embedded nature CONFIRMED]
- **Rejected — MongoDB/MySQL:** document stores sacrifice the relational
  integrity the finance-adjacent modules need; MySQL's JSON/vector story is
  weaker than Postgres's. [LIKELY]
- **Rejected — CockroachDB:** global distribution isn't an MVP requirement;
  operational and cost overhead for no benefit. [ASSUMED]

**What Redis is for (explicit, per mandate):** cache (short-TTL registry/policy
lookups), rate limiting (token buckets per tenant), refresh-token denylist,
distributed locks for leader election/singletons, ARQ job queue backend,
Redis Streams as the step-up event transport. Redis is **not** the system of
record for anything — Postgres is. [CONFIRMED pattern from ACP]

**Licensing note:** Redis changed to RSAL/SSPL in 2024. For a commercial
product, **Valkey** (Linux Foundation fork, BSD) is the documented drop-in
escape hatch; the code must not depend on Redis-proprietary modules.
[CONFIRMED — widely reported; exact legal reading UNKNOWN — get counsel if
it matters]

**Object storage:** S3-compatible API from day one (MinIO in compose, any
S3 provider in prod). Stores: file uploads, report exports, audit snapshots,
agent artifacts. Purpose: bulk bytes never belong in Postgres. [ASSUMED]

### 2.4 Agent / orchestration infra — LangGraph now, Temporal-shaped interface

**Chosen: LangGraph 1.x for agent loops, Postgres-backed checkpointer,
interrupts for HITL approvals. A `WorkflowBackend` interface (ACP already
defines one) owns macro-workflows; the MVP implementation is LangGraph +
DB-backed state, with Temporal as the documented future binding.**

- **2026 state:** LangGraph 1.0 alpha released Sept 2026, official 1.0 target
  late October 2026, "no breaking changes" promised from alpha; battle-tested
  at Uber, LinkedIn, Klarna. LangChain 1.x split: `langchain` = model
  abstractions/prebuilt patterns, `langgraph` = durable agent runtime.
  [CONFIRMED — langchain.com announcement]
- **Why not Temporal day-one:** Temporal is the category definer for durable
  execution, but it brings a cluster (server + DB + workers + UI) and an
  ops story disproportionate to MVP workflow volumes. The 2026 consensus
  pattern is layering: **agent frameworks own the reasoning loop; Temporal
  (if ever) owns cross-framework macro-orchestration.** We adopt the
  layering without the cluster: `WorkflowBackend` interface, LangGraph
  implementation now. [LIKELY — community consensus; Temporal weight CONFIRMED]
- **Why not a custom engine:** ACP's honest gap list already flags its
  hand-rolled durable runner as non-production. LangGraph's checkpointer +
  interrupts give us crash recovery and approval gates without maintaining a
  workflow engine. Build the differentiated layer (policy, routing, audit);
  integrate the rest. [CONFIRMED — ACP ARCHITECTURE.md principle 9]
- **Rejected — CrewAI/AutoGen as core:** role-play abstractions add magic the
  governance rail must then see through; explicit graphs are auditable.
  [ASSUMED]
- **Relationship to `agent-control-plane`:** reuse the *patterns* — admission
  chain, policy-before-execution, scoped credentials, `WorkflowBackend`
  interface, hash-chained audit — not the code. The OS consumes ACP as a
  library/service boundary where it fits; it does not fork it. [ASSUMED —
  architecture-phase to draw the exact seam]

### 2.5 Model abstraction — LiteLLM (library, not proxy, for MVP)

**Chosen: LiteLLM as an in-process library (`litellm` completion calls /
`Router` for fallbacks), behind our own `ModelProvider` adapter interface
(ACP §11 already specifies this shape). OpenRouter as an *optional*
configured provider; Ollama for local dev.**

- **Why:** one call signature across 100+ providers; built-in retries,
  fallbacks, and spend tracking; ~6.5ms median overhead measured July 2026
  (independent harness) — negligible next to model latency. 2026 surveys: 87%
  of AI engineers use multiple models; cost is the #2 monitored metric; a
  June 2026 Anthropic export-control outage took models offline for ~3 weeks
  — single-provider stacks have no fallback. Multi-provider routing is risk
  management, not fashion. [CONFIRMED — awesome-ai-gateway 2026-07 data]
- **Why library, not Proxy, for MVP:** a single service calling LLMs needs
  the SDK with a shared session, not a separate gateway process (which adds
  a network hop, a Postgres for keys/spend, and an ops burden). The Proxy
  becomes right when multiple services/teams share one gateway — that's a
  documented step-up, and ACP's "route, don't proxy" principle already
  anticipates OpenRouter/LiteLLM-class backends. [LIKELY]
- **Rejected — raw provider SDKs only:** N×M integration matrix, no unified
  fallback/cost story. [ASSUMED]
- **Rejected — fully custom abstraction:** rebuilding provider quirks
  (drop_params behavior, streaming differences) is undifferentiated heavy
  lifting. [ASSUMED]
- **Envoy AI Gateway v1.0** (CNCF, June 2026) noted as the K8s-native data
  plane to watch for the Proxy-stage future. [CONFIRMED]

### 2.6 MCP support — official SDK v1.x; FastMCP to expose

**Chosen: official `modelcontextprotocol` Python SDK, pinned `>=1.27,<2`
(the v1.x stable line); FastMCP for exposing OS tools as MCP servers;
Streamable HTTP transport for remote, stdio for local.**

- **2026 state:** MCP spec 2026-07-28 released; SDK v2 is pre-release
  (breaking changes expected) — **v1.x is the only production line**, with a
  6-month security-fix window after v2 ships. Pin `<2` and plan the migration
  as a tracked work item. The public registry counted ~31k distinct servers
  (Sept 2026) — MCP won the tool-calling standard war. [CONFIRMED — SDK
  README notices; registry count LIKELY]
- **Trust boundaries (binding):** MCP servers are *untrusted tool providers*.
  Every MCP tool call passes through the governance rail (policy eval +
  scoped credentials) exactly like any other tool invocation; MCP OAuth is
  for server identity, not user authorization; tool schemas are validated,
  outputs treated as untrusted data (prompt-injection surface — route through
  the DLP/injection detectors ACP already built). No MCP server ever receives
  ambient credentials. [ASSUMED — derived from ACP security architecture]
- **Rejected — hand-rolled MCP:** spec churn makes this a maintenance trap.
  [ASSUMED]

### 2.7 Event bus — in-process default, interface from day one

**Chosen: in-process asyncio event bus behind an `EventBus` interface
(publish/subscribe, at-least-once local semantics); Redis Streams as the
documented step-up; NATS JetStream after that.**

- **Why:** MVP event volume (audit writes, task status, approval
  notifications, cache invalidation) does not justify a broker. ACP's
  architecture already names this exact ladder: Redis Streams → NATS
  JetStream via interface. In-process keeps `docker compose up` to five
  services and the failure modes trivial. [CONFIRMED — ACP ARCHITECTURE.md §15]
- **Rejected — Kafka/Redpanda day-one:** ZooKeeper-less or not, they are
  multi-service distributed systems with their own ops runbooks. Nothing in
  the MVP needs cross-datacenter replayable topics. Explicitly not fashion.
  [ASSUMED]
- **Durability note:** events that must survive a crash (audit, task state)
  are written to Postgres (the ledger), not the bus — the bus is transport,
  the ledger is truth. [CONFIRMED — ACP/AGRL consolidation note]

### 2.8 Auth — JWT + rotation, RBAC, tenant_id + RLS

**Chosen:**
- **Humans:** OIDC-ready login; **short-lived JWT access tokens (5–15 min) +
  rotating refresh tokens** (rotation with reuse detection), HttpOnly
  Secure cookies for the SPA. RBAC roles per tenant; ABAC/policy engine for
  fine-grained action checks (governance rail owns the decision).
- **Agents/tools:** no ambient authority — per-action scoped grants with
  expiry (ACP anti-confused-deputy rules 1–5), secret brokering not passing.
- **Tenant isolation:** `tenant_id` on every row, application-enforced as the
  primary gate (query filters + tests that assert cross-tenant invisibility);
  **Postgres RLS as defense-in-depth** on top. Hierarchy: Org → Tenant →
  {users, agents, tools, policies, knowledge, budgets, audit}. [CONFIRMED —
  ACP ARCHITECTURE.md §10]
- **Rejected — schema-per-tenant / DB-per-tenant:** migration and connection
  sprawl at MVP; revisit for enterprise/regulated tenants (deployment model
  2/3 in ACP §17). [ASSUMED]
- **Rejected — sessions-in-Redis as the only auth:** stateful sessions fight
  horizontal scaling of the API tier; JWT keeps it stateless, Redis holds
  only the denylist. [ASSUMED]

### 2.9 DevOps — Compose, Actions, Alembic, OTel

- **Local:** `docker compose up` — `api` (FastAPI), `worker` (ARQ),
  `postgres:16/17 + pgvector`, `redis:7`, `minio`. `.env.example` documents
  every variable. Frontend built into the API image (multi-stage). [CONFIRMED
  pattern — ACP + TMS compose files]
- **CI (GitHub Actions):** `ruff check + format --check` → `mypy` (strict on
  new packages) → `pytest` (unit + integration vs compose services) →
  `docker build` → image scan (trivy) → SBOM (syft). Alembic migration check
  (heads-upgradeable from a fresh DB). Frontend: `tsc --noEmit`, `eslint`,
  `vitest`, `vite build`. [LIKELY — standard 2026 practice; versions UNKNOWN]
- **CD:** tag → build → push GHCR → deploy to staging compose; production
  deploy is a separate approval (matches "human approval for deploys" policy).
  Helm/K8s manifests deferred to post-MVP (ACP already has K8s scaffolding to
  borrow when the time comes). [ASSUMED]
- **Migrations:** Alembic, one linear history, migrations are code-reviewed,
  backward-compatible (expand → migrate → contract) once prod data exists.
  [CONFIRMED — ACP convention]
- **Observability:** OTel SDK from day one — traces (with ACP-style decision
  provenance attributes: `task.id`, `policy.decision`, `model.selected`,
  `cost`), metrics, structured JSON logs via structlog → OTLP. Local:
  console + optional Grafana stack (Tempo/Loki/Prometheus) via compose
  profile. **Langfuse optional profile** for LLM-call-level observability
  later — not day-one. [CONFIRMED pattern — ACP §14; Langfuse deferral ASSUMED]
- **Secrets:** env vars locally; secret-broker interface with Vault/Doppler/
  cloud-secret-manager adapters for prod. Never in git. [CONFIRMED — ACP §6]

---

## 3. Cost Estimates (rough, labeled)

Assumptions: MVP = 1 API + 1 worker container, Postgres, Redis, MinIO, static
SPA; single small cloud deploy; dev on Ekue's own machine. Model API spend is
usage-driven and dominates everything below.

| Item | Option | Rough cost | Confidence |
|---|---|---|---|
| Local dev | own machine + Docker | **$0/mo** | CONFIRMED |
| Small cloud deploy (self-managed) | Hetzner CX32-class VPS (4 vCPU/8GB) + self-hosted Postgres/Redis | **~$15–25/mo** | LIKELY (Hetzner pricing moves; check at purchase) |
| Small cloud deploy (managed PaaS) | Railway / Render: 1 web service + managed Postgres + Redis | **~$25–60/mo** | LIKELY (Render free tier sleeps; paid from ~$7/service + $6–20 data) |
| Managed Postgres alternative | Supabase / Neon / RDS small | **$0–25/mo** (free tiers → $15–25) | ASSUMED |
| Domain + TLS | registrar + Let's Encrypt | **~$12/yr + $0** | CONFIRMED |
| CI | GitHub Actions (public repo) | **$0** | CONFIRMED |
| Container registry | GHCR | **$0** | CONFIRMED |
| LLM API (dev/test) | OpenRouter / direct, small models | **$20–100/mo** typical dev burn | ASSUMED — varies wildly with usage |
| LLM API (early prod) | mixed routing (cheap default, strong on demand) | **$200–2,000+/mo** | UNKNOWN — depends on agent volume; cost telemetry (ACP §14/§15) exists precisely to bound this |
| Vector DB | pgvector (in Postgres) | **$0** incremental | CONFIRMED |
| Pinecone (if ever needed) | serverless | from ~$70/mo (2025 figure) | LIKELY — verify current pricing |

**Headline:** MVP infra is **$0 local / ~$15–60/mo deployed** before model
spend. Model API cost is the only line item that scales with success — which
is why LiteLLM cost tracking + per-tenant budgets + kill switches are
day-one requirements, not nice-to-haves.

### Vendor lock-in analysis

| Dependency | Lock-in risk | Mitigation |
|---|---|---|
| Postgres + pgvector | Low — everywhere, OSS | Standard SQL; no exotic extensions beyond pgvector |
| Redis | Low-Medium (license) | No proprietary modules; Valkey drop-in documented |
| LiteLLM (library) | Low — behind our `ModelProvider` interface | Swap provider or remove LiteLLM without touching call sites |
| LangGraph | Medium — graph definitions are framework-shaped | `WorkflowBackend` interface; keep business logic in nodes, not framework magic |
| MCP SDK v1.x | Low — protocol standard, multiple SDKs | Pin `<2`; migration tracked |
| OTel / Prometheus | Very low — open standards | No vendor SDK in the hot path |
| S3 API | Very low — de-facto standard | Any provider; MinIO locally |
| GitHub Actions / GHCR | Low-Medium | Workflows are YAML; portable to any CI |
| Cloud VPS/PaaS | Low — containers | No cloud-native services in the MVP path (no Lambda-isms, no proprietary queues) |

**Deliberate non-goals for lock-in:** no managed AI platform (Bedrock-only,
Vertex-only) as the *required* path — Bedrock is a *provider option* behind
LiteLLM, never the spine.

---

## 4. Explicitly NOT for MVP (with reasons)

1. **Kafka / Redpanda** — broker ops weight unjustified; interface preserves
   the path. (Revisit: sustained >10k events/sec or multi-team streaming.)
2. **Temporal cluster** — same reason; `WorkflowBackend` interface is the
   hedge. (Revisit: cross-framework durable workflows or human waits measured
   in days at real volume.)
3. **Dedicated vector DB (Pinecone/Qdrant/Milvus)** — pgvector covers MVP
   scale; separate system = sync pipelines + second backup story. (Revisit:
   >~1–10M vectors or ANN QPS beyond a single Postgres.)
4. **Dedicated graph DB (Neo4j/Neptune)** — relational edges + pgvector cover
   MVP GraphRAG. (Revisit: multi-hop traversal latency proves Postgres-bound;
   step-up is embedded Kuzu, still not a server.)
5. **Next.js / SSR** — no SEO need behind login; second runtime. (Revisit:
   public pages.)
6. **LiteLLM Proxy / Envoy AI Gateway as required infra** — library mode
   suffices for one service. (Revisit: multi-service or multi-team gateway.)
7. **Kubernetes** — compose is the MVP deploy story; K8s manifests deferred
   (ACP has scaffolding to borrow). (Revisit: multi-node, multi-region, or
   enterprise deployment models.)
8. **MCP SDK v2 / spec 2026-07-28 features** — pre-release; adopt on stable
   with a tracked migration. [CONFIRMED pre-release status]
9. **Langfuse / Datadog APM day-one** — OTel + Grafana stack covers MVP;
   LLM-specific tracing is an optional compose profile later.
10. **Multi-region / active-passive** — single-region MVP; DR = managed
    backups + PITR + ledger anchoring design (borrow ACP §16).
11. **Schema-per-tenant / DB-per-tenant** — row-level + RLS is enough; revisit
    for regulated enterprise tenants.
12. **Custom agent framework / custom workflow engine** — LangGraph +
    interfaces; the differentiated code is policy, routing, audit, and the
    business apps.

---

## 5. Draft ADRs (for architecture-phase adoption)

### ADR-001: Modular monolith on FastAPI + Python 3.12
**Status:** proposed · **Date:** 2026-09-15
**Context:** MVP must ship fast with clean extraction paths; the team has a
proven FastAPI modular-monolith pattern; the agent ecosystem is Python-first.
**Decision:** single deployable (API + worker processes), strict package
boundaries (`backend/app/<domain>/` with public-interface-only imports),
FastAPI ≥0.126, Python 3.12 via uv, Pydantic v2 only.
**Consequences:** + fastest path, + ecosystem, − GIL-bound CPU work must move
to workers; extraction to services later requires no rewrite *if* boundaries
hold (enforce with import-lint in CI).
**Evidence:** CONFIRMED (workspace pattern), LIKELY (ecosystem claims).

### ADR-002: PostgreSQL (+ pgvector) as the single system of record
**Status:** proposed · **Date:** 2026-09-15
**Context:** ledgers, audit chains, tenant data, embeddings, and full-text
all need one transactional home.
**Decision:** Postgres 16/17 + pgvector (≥0.8.2, HNSW) for all of it;
SQLAlchemy 2.0 async; Alembic migrations. No dedicated vector or graph DB
at MVP; retrieval behind interfaces.
**Consequences:** + one backup/transaction story, + tenant-filtered hybrid
search in one query; − ANN scale ceiling (~millions of vectors) is accepted
and monitored.
**Evidence:** CONFIRMED (AWS guidance, pgvector release notes).

### ADR-003: Redis for cache/queues/rate-limits — never the system of record
**Status:** proposed · **Date:** 2026-09-15
**Context:** need ephemeral shared state (cache, locks, rate limits, job
queue, refresh-token denylist) without a second database.
**Decision:** Redis 7; Valkey documented as the license-safe drop-in; no
Redis-proprietary modules. ARQ (asyncio-native) for background jobs behind
a `TaskQueue` interface.
**Consequences:** + proven, tiny ops footprint; − RSAL/SSPL licensing noted
for commercial use; Celery explicitly rejected (sync-first, heavy).
**Evidence:** CONFIRMED (ACP usage), ASSUMED (ARQ vs Celery judgment).

### ADR-004: LangGraph for agent loops; WorkflowBackend interface for macro-flows
**Status:** proposed · **Date:** 2026-09-15
**Context:** need durable, resumable, human-gateable agent execution without
operating a workflow cluster at MVP.
**Decision:** LangGraph 1.x, Postgres checkpointer, interrupts for approvals;
macro-workflows behind ACP's `WorkflowBackend` interface (Temporal is the
documented future binding, not a day-one dependency).
**Consequences:** + durable HITL now, + no cluster; − framework-shaped graph
code (mitigate: business logic in plain functions, thin nodes).
**Evidence:** CONFIRMED (1.0 alpha, prod users), LIKELY (Temporal weight).

### ADR-005: LiteLLM as in-process model gateway behind a ModelProvider interface
**Status:** proposed · **Date:** 2026-09-15
**Context:** multi-provider routing, fallbacks, and cost control are risk
management (June 2026 outage proved single-provider fragility); a separate
gateway process is unjustified for one service.
**Decision:** LiteLLM library + `Router` in-process, behind our own
`ModelProvider` adapter interface; OpenRouter optional; Ollama for local dev;
Proxy/Gateway deferred.
**Consequences:** + one signature for all providers, + spend telemetry day
one; − ~6.5ms overhead (negligible), − LiteLLM release churn (pin in uv.lock).
**Evidence:** CONFIRMED (gateway benchmarks, outage), LIKELY (Proxy timing).

### ADR-006: MCP via official SDK v1.x; MCP servers are untrusted tools
**Status:** proposed · **Date:** 2026-09-15
**Context:** MCP is the tool-calling standard (31k servers, Sept 2026); SDK
v2 is pre-release.
**Decision:** pin `mcp>=1.27,<2`; FastMCP to expose our tools; Streamable
HTTP remote / stdio local. Every MCP invocation passes the governance rail;
no ambient credentials to any MCP server; outputs treated as untrusted.
**Consequences:** + standards-based tool ecosystem; − v2 migration is tracked
tech debt with a 6-month security window.
**Evidence:** CONFIRMED (SDK notices, registry size LIKELY).

### ADR-007: In-process event bus behind an EventBus interface; ledger is truth
**Status:** proposed · **Date:** 2026-09-15
**Context:** MVP event volumes don't justify a broker; future scale must not
require a rewrite.
**Decision:** asyncio in-process bus behind `EventBus`; Redis Streams then
NATS JetStream as documented step-ups; Kafka/Redpanda explicitly rejected
for MVP. Anything that must survive a crash goes to the Postgres ledger —
the bus is transport, not truth.
**Evidence:** CONFIRMED (ACP §15 ladder), ASSUMED (volume judgment).

### ADR-008: Auth = JWT access + rotating refresh, RBAC, tenant_id + RLS
**Status:** proposed · **Date:** 2026-09-15
**Context:** stateless API tier, multi-tenant SaaS, agents as a principal
class alongside humans.
**Decision:** short-lived JWT access (5–15 min) + rotating refresh with reuse
detection (HttpOnly Secure cookies for SPA); RBAC per tenant; `tenant_id` on
every row application-enforced + Postgres RLS defense-in-depth; agents get
per-action scoped grants, zero ambient authority; OIDC interface reserved for
SSO later.
**Consequences:** + horizontal scale, + fail-closed tenancy; − token theft
window bounded by rotation, not eliminated (short lifetimes + denylist).
**Evidence:** CONFIRMED (ACP §9/§10 patterns), ASSUMED (JWT-vs-session call).

---

## 6. Risks in the chosen stack (top 3 for the build)

1. **LangGraph 1.0 isn't stable yet** (official release late Oct 2026; we're
   building on the alpha). Mitigation: pin exact version in `uv.lock`,
   keep graph code thin (business logic in plain functions), and the
   `WorkflowBackend` interface means a forced framework change is contained.
2. **Model API cost is the only unbounded line item.** A misconfigured agent
   loop can burn hundreds of dollars overnight. Mitigation is architectural,
   not hopeful: LiteLLM spend tracking + per-tenant budgets + kill switches
   + approval gates on expensive actions are **P0**, not backlog.
3. **MCP SDK v2 migration is coming** (spec 2026-07-28, v1.x security window
   ~6 months post-stable). Pinning `<2` is safe today but creates tracked
   debt; the trust-boundary rules (untrusted servers, rail-gated calls) must
   be re-verified against v2's new primitives (discovery, long-running
   tasks) at migration time.

---

*Research method: workspace pattern evidence (agent-control-plane
ARCHITECTURE.md + pyproject, nexora-erp stack, AI-native-TMS deploy record)
plus web research 2026-09-15 (FastAPI/Pydantic docs, LangChain 1.0 alpha
announcement, MCP SDK notices, AWS vector-DB guidance, pgvector release
notes, gateway benchmarks). No application code was written.*
