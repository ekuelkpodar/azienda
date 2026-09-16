# AGRL — event → projection flow

One event ledger, not three. All goal/resource lifecycle facts are appended as immutable,
hash-chained events; current state is a derived projection (rebuildable by replay).

```mermaid
flowchart LR
    subgraph WRITERS["event writers (all domains)"]
        TASK[TaskService]
        WF[WorkflowService]
        ORCH[Orchestrator]
        GOVW[Governance rail]
        HUMAN[Human (via API)]
    end

    subgraph LEDGER["agrl_events — append-only, hash-chained"]
        E1["goal.created"]
        E2["resource.allocated"]
        E3["plan.proposed"]
        E4["approval.decided"]
        E5["outcome.recorded"]
        E6["goal.reprioritized"]
        E1 --> E2 --> E3 --> E4 --> E5 --> E6
    end

    subgraph PROJECT["projections — derived, never edited"]
        P_GOAL[("goals<br/>+ goal_links")]
        P_RES[("resource_allocations")]
        P_PLAN[("plans")]
        P_OUT[("outcome summaries")]
    end

    subgraph READERS["projection readers"]
        CC[Command Center]
        ROUTER[Router<br/>capacity-aware routing]
        LEARN[Learning loop<br/>weight updates]
    end

    WRITERS -->|append-only| LEDGER
    LEDGER -->|fold / replay| PROJECT
    PROJECT --> READERS

    REBUILD["CLI: azienda agrl rebuild<br/>replay events → fresh projections"] -.-> PROJECT

    style LEDGER fill:#bfdbfe,stroke:#1e40af
    style PROJECT fill:#fef3c7,stroke:#92400e
```

## Invariants (binding)

1. **Projections are derived, never edited** — domain code writes events only.
2. The AGRL writer is the sole mutator path for goals; `agent-control-plane` and business
   apps consume projections (mirrors ACP principle "AGRL stays above and separable").
3. Every event carries `tenant_id`, `causation_id` / `correlation_id`, actor, and hash chain links.
4. Goal conflicts are first-class: `goal_links` with `relation = conflicts_with` + resolution history.
