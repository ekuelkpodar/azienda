# THREAT_MODEL.md — Azienda (MVP, 2026-09-16)

> Labels: CONFIRMED = observed in code/tests this session; ASSUMED = standard
> reasoning not yet tested; UNKNOWN = not assessed. This is a threat *model*,
> not a claim that all controls exist.

## Assets

1. Tenant business data (CRM, finance, support conversations) — the asset that
   matters most.
2. Agent execution budget (money spent on model calls / tool calls).
3. Credentials (JWT signing keys, API keys, provider keys when integrated).
4. Audit ledger integrity (the tamper-evidence story).

## Actors

- **External attacker:** API abuse, credential theft, prompt injection through
  untrusted content (CRM notes, KB articles, emails).
- **Malicious or compromised tenant:** cross-tenant data access attempts.
- **Insider / misconfigured agent:** unbounded spend, data exfiltration via
  tools, policy bypass.
- **Honest-but-buggy agent:** duplicate sends, wrong-recipient comms,
  runaway loops.

## Top threats and controls

| # | Threat | Control today | Label |
|---|---|---|---|
| T1 | Tenant A reads Tenant B data | Tenant-scoped queries + Postgres RLS (defense in depth); 7/7 manual isolation probes pass | CONFIRMED (code + probes); live-Postgres RLS UNKNOWN |
| T2 | Client-supplied `X-Tenant-Id` spoofing | Fail-closed 401 in production on 8 routers | CONFIRMED mitigation; full JWT refactor still required |
| T3 | Unbounded agent spend | Budgets + kill switch, tested (P0) | CONFIRMED |
| T4 | Externally-visible action without approval | Policy-before-execution; timeout==DENY | CONFIRMED (demo 1 exercises the deny path) |
| T5 | Prompt injection via untrusted content (KB, emails, CRM notes) | Treat tool/MCP outputs as untrusted data (AGENTS.md §2); no dedicated injection detector yet | Control ASSUMED; detector UNKNOWN/absent |
| T6 | Audit log tampering | Hash chain with `verify_chain` | CONFIRMED (tests + demos) |
| T7 | Refresh-token theft | Rotation + reuse detection | CONFIRMED (tests) |
| T8 | LLM provider key compromise | Providers not integrated yet — no keys to steal | Not applicable yet; must use secrets manager at integration |
| T9 | Runaway retry loops | Bounded retries (AGENTS.md §4) — audit per package | LIKELY; not centrally verified |
| T10 | SSRF / exfiltration via agent tool calls | MCP servers untrusted, rail-gated (ADR 0006) | Design CONFIRMED; live enforcement UNKNOWN |

## Explicit non-goals for MVP

- Insider-threat DLP, SOC 2 / HIPAA / GDPR certification, hardware-backed
  key storage, formal red-teaming. See ROADMAP.md.
