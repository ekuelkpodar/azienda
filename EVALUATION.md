# EVALUATION.md — Azienda evaluation philosophy and harness

> Per Ekue's standing rule: measure task success, accuracy, hallucination rate,
> tool-call accuracy, recovery/failure rates, latency, cost, safety/policy
> violations, and human-intervention rate. Automated eval suites over demos.

## What exists today (CONFIRMED, 2026-09-16)

- **269 backend tests** — unit + integration coverage across all 15 packages.
- **3 signature demos** (`scripts/demos/`), each printing PASS/FAIL per step and
  an overall verdict:
  - Demo 1 (12 steps): lead → score → convert → approval-gated outreach.
  - Demo 2 (9 steps): ticket → CRM lookup → KB search → draft → authorized
    reply → human escalation.
  - Demo 3 (11 steps): exec briefing aggregating approvals, failed/blocked/
    overdue tasks, budget freeze, policy deny-rate anomaly, AGRL outcomes.
- Deterministic, no-LLM demos: results are reproducible (fresh temp DB per run).

## What is still missing (honest gaps)

- No automated **agent-quality** eval suite (task success rate, tool-call
  accuracy, hallucination rate) — demos assert mechanics, not agent judgment.
- No **cost/latency benchmarks** per task type (the billing package can meter,
  but no eval harness consumes it yet).
- No **safety/policy-violation** eval set (e.g. prompt-injection attempts
  against the rail, jailbreak prompts against the planner).
- No **human-intervention-rate** measurement (approval-queue analytics exist in
  demo 3 but aren't trended).
- Live LLM providers are not integrated, so model-behavior evals have no target
  yet.

## Recommended next evals (ordered)

1. **Rail red-team suite:** prompt-injection corpus → assert DENY/approval on
   every malicious case (covers THREAT_MODEL.md T5).
2. **Tool-call accuracy suite:** synthetic tasks with known-correct tool-call
   sequences → measure precision/recall of agent tool selection.
3. **Cost regression suite:** per-demo token/credit budget → fail CI if a
   change increases cost beyond a threshold.
4. **Recovery suite:** inject tool failures (timeouts, 500s) → measure
   task recovery rate and bounded-retry behavior.
