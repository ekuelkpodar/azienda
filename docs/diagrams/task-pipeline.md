# Governed task pipeline (sequence)

The AI-native operating loop made concrete: every agent-executed task passes through
planning, policy check, budget reserve, durable execution, observation, and outcome recording.
Policy denials and approval timeouts fail closed.

```mermaid
sequenceDiagram
    autonumber
    participant U as User / App
    participant TS as TaskService
    participant AGRL as AGRL ledger
    participant PLN as Planner
    participant GOV as Governance rail
    participant BUD as BudgetEnforcer
    participant ORC as Orchestrator
    participant RTE as Router
    participant T as Tool (scoped grant)
    participant M as ModelProvider

    U->>TS: POST /tasks (Idempotency-Key)
    TS->>AGRL: append task.created / goal.linked
    TS->>PLN: produce plan
    PLN-->>TS: plan (steps × tools × est. cost)
    TS->>AGRL: append plan.proposed

    loop each step
        TS->>GOV: PolicyEngine.evaluate(action)
        alt deny
            GOV-->>TS: DENY + reasons
            TS->>AGRL: append outcome.recorded (failed)
            TS-->>U: 403 policy_denied
        else require_approval
            GOV-->>TS: approval_id
            TS->>AGRL: append approval.requested
            U->>GOV: POST /approvals/{id}/decide
            alt timeout or denied
                GOV-->>TS: DENY (fail closed)
            else approved
                TS->>BUD: reserve(est. credits)
                alt insufficient
                    BUD-->>TS: BLOCKED + alert
                end
            end
        else allow
            TS->>BUD: reserve(est. credits)
        end
    end

    TS->>ORC: run_task(task_id)
    loop executing (LangGraph + checkpointer)
        ORC->>RTE: select tool (capability × cost × risk × policy)
        ORC->>GOV: re-evaluate tool call
        GOV-->>ORC: scoped grant
        ORC->>T: execute(grant)
        T-->>ORC: result (untrusted output)
        ORC->>M: complete() if reasoning needed
        M-->>ORC: response + usage
        ORC->>BUD: settle actual cost (CostRecorder)
        ORC->>AGRL: append step observation
    end
    ORC->>TS: finished (completed | failed | blocked)
    TS->>AGRL: append outcome.recorded (plan, decisions, tool calls, retries, FULL cost)
    TS->>BUD: release reservation
    TS-->>U: outcome record
    Note over TS,AGRL: async: eval scores, cost-per-outcome trends,<br/>policy refinements → FEEDBACK → LEARNING
```
