"""Workflow DSL: node/edge schema + structural validation.

Node types: TRIGGER -> CONDITION -> AGENT -> TOOL -> APPROVAL -> ACTION -> NOTIFICATION.
Validation is strict: unknown node types are rejected, approval nodes REQUIRE a
policy_ref, condition nodes REQUIRE an expression, tool nodes REQUIRE a tool_name,
agent nodes REQUIRE a capability, action nodes REQUIRE a known action.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

NodeType = Literal["trigger", "condition", "agent", "tool", "approval", "action", "notification"]

KNOWN_TYPES = set(NodeType.__args__)  # type: ignore[attr-defined]


class RetrySpec(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_seconds: float = Field(default=1.0, ge=0, le=3600)


class NodeSpec(BaseModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-]+$")
    type: str
    label: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    retry: RetrySpec = Field(default_factory=RetrySpec)
    # Optional named edge to follow when this node fails and retries are exhausted.
    on_error: str | None = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in KNOWN_TYPES:
            raise ValueError(
                f"unknown node type '{v}'; must be one of {sorted(KNOWN_TYPES)}")
        return v


class EdgeSpec(BaseModel):
    from_node: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    # condition branches use "true"/"false"; approval nodes use "approved"/"denied".
    label: str | None = None

    model_config = {"populate_by_name": True}


class DagSpec(BaseModel):
    nodes: list[NodeSpec] = Field(min_length=1)
    edges: list[EdgeSpec] = Field(default_factory=list)


class DagValidationError(ValueError):
    """Raised with .errors listing every structural problem found."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_dag(dag: dict[str, Any]) -> DagSpec:
    """Parse + structurally validate a DAG. Raises DagValidationError listing ALL problems."""
    errors: list[str] = []
    try:
        spec = DagSpec.model_validate(dag)
    except Exception as e:  # pydantic ValidationError
        raise DagValidationError([f"schema: {e}"]) from e

    node_ids = [n.id for n in spec.nodes]
    if len(set(node_ids)) != len(node_ids):
        errors.append("duplicate node ids")

    by_id = {n.id: n for n in spec.nodes}

    triggers = [n for n in spec.nodes if n.type == "trigger"]
    if len(triggers) != 1:
        errors.append(f"dag must have exactly one trigger node, found {len(triggers)}")

    for n in spec.nodes:
        cfg = n.config
        if n.type == "approval" and not cfg.get("policy_ref"):
            errors.append(f"approval node '{n.id}' requires config.policy_ref")
        if n.type == "approval" and not cfg.get("action"):
            errors.append(f"approval node '{n.id}' requires config.action")
        if n.type == "condition" and "expression" not in cfg:
            errors.append(f"condition node '{n.id}' requires config.expression")
        if n.type == "tool" and not cfg.get("tool_name"):
            errors.append(f"tool node '{n.id}' requires config.tool_name")
        if n.type == "agent" and not cfg.get("capability"):
            errors.append(f"agent node '{n.id}' requires config.capability")
        if n.type == "action" and not cfg.get("action"):
            errors.append(f"action node '{n.id}' requires config.action")
        if n.on_error and n.on_error not in by_id:
            errors.append(f"node '{n.id}' on_error target '{n.on_error}' does not exist")

    for e in spec.edges:
        if e.from_node not in by_id:
            errors.append(f"edge from unknown node '{e.from_node}'")
        if e.to not in by_id:
            errors.append(f"edge to unknown node '{e.to}'")

    # reachability from the trigger
    if triggers:
        adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
        for e in spec.edges:
            if e.from_node in adjacency:
                adjacency[e.from_node].append(e.to)
        for n in spec.nodes:
            if n.on_error and n.on_error in adjacency:
                adjacency[n.id].append(n.on_error)
        seen: set[str] = set()
        stack = [triggers[0].id]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adjacency.get(cur, []))
        unreachable = [nid for nid in node_ids if nid not in seen]
        if unreachable:
            errors.append(f"unreachable nodes: {unreachable}")

    # cycle detection (definitions are DAGs; loops belong in the engine's retry primitive)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}
    adjacency = {nid: [] for nid in node_ids}
    for e in spec.edges:
        if e.from_node in adjacency and e.to in adjacency:
            adjacency[e.from_node].append(e.to)

    def dfs(u: str) -> bool:
        color[u] = GRAY
        for v in adjacency[u]:
            if color[v] == GRAY:
                return True
            if color[v] == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False

    if any(color[nid] == WHITE and dfs(nid) for nid in node_ids):
        errors.append("dag contains a cycle; definitions must be acyclic")

    if errors:
        raise DagValidationError(errors)
    return spec


def outgoing(spec: DagSpec, node_id: str, label: str | None = None) -> list[str]:
    """Edge targets from a node, optionally filtered by edge label."""
    return [
        e.to for e in spec.edges
        if e.from_node == node_id and (label is None or e.label == label)
    ]


def default_next(spec: DagSpec, node_id: str) -> str | None:
    """The unlabeled outgoing edge (the 'happy path'), if exactly one exists."""
    unlabeled = [e.to for e in spec.edges if e.from_node == node_id and e.label is None]
    return unlabeled[0] if len(unlabeled) == 1 else None
