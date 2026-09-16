# Agent lifecycle states

Agent definitions are versioned and immutable once published; the registry tracks lifecycle.
Autonomy level L0–L5 is set per agent version and enforced by the governance rail.

```mermaid
stateDiagram-v2
    [*] --> draft: register
    draft --> vetting: submit for vetting
    vetting --> draft: vetting failed
    vetting --> active: vetted + published
    active --> paused: pause (operator)
    paused --> active: resume
    active --> deprecated: new version published
    deprecated --> retired: grace period elapsed
    paused --> retired: retire
    retired --> [*]

    note right of active
        autonomy_level L0..L5
        allowed_tools (least privilege)
        model_prefs
        enforced per action by
        the governance rail
    end note
```

## Task-side states (for reference)

`pending → planning → waiting_approval → executing → blocked → completed | failed`,
plus `cancelled` from any non-terminal state. See `task-pipeline.md` and `DATABASE.md` §4.
Every transition is recorded in `task_transitions` and mirrored as an AGRL event.
