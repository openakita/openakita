"""Data types for the Multi-Dimensional Relational Memory system."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class NodeType(Enum):
    EVENT = "event"
    FACT = "fact"
    DECISION = "decision"
    GOAL = "goal"


class EdgeType(Enum):
    # Temporal
    PRECEDED_BY = "preceded_by"
    FOLLOWED_BY = "followed_by"
    CONCURRENT_WITH = "concurrent_with"
    # Causal
    CAUSED_BY = "caused_by"
    LED_TO = "led_to"
    BLOCKED_BY = "blocked_by"
    # Entity
    INVOLVES = "involves"
    RELATED_TO = "related_to"
    SAME_ENTITY = "same_entity"
    # Action
    REQUIRES = "requires"
    ENABLES = "enables"
    PART_OF = "part_of"
    # Context
    BELONGS_TO_PROJECT = "belongs_to_project"
    SERVES_GOAL = "serves_goal"
    IN_SESSION = "in_session"
    # Mode 1 fallback
    LINKED = "linked"
    SAME_SUBJECT = "same_subject"


class Dimension(Enum):
    TEMPORAL = "temporal"
    ENTITY = "entity"
    CAUSAL = "causal"
    ACTION = "action"
    CONTEXT = "context"


EDGE_DIMENSION: dict[EdgeType, Dimension] = {
    EdgeType.PRECEDED_BY: Dimension.TEMPORAL,
    EdgeType.FOLLOWED_BY: Dimension.TEMPORAL,
    EdgeType.CONCURRENT_WITH: Dimension.TEMPORAL,
    EdgeType.CAUSED_BY: Dimension.CAUSAL,
    EdgeType.LED_TO: Dimension.CAUSAL,
    EdgeType.BLOCKED_BY: Dimension.CAUSAL,
    EdgeType.INVOLVES: Dimension.ENTITY,
    EdgeType.RELATED_TO: Dimension.ENTITY,
    EdgeType.SAME_ENTITY: Dimension.ENTITY,
    EdgeType.REQUIRES: Dimension.ACTION,
    EdgeType.ENABLES: Dimension.ACTION,
    EdgeType.PART_OF: Dimension.ACTION,
    EdgeType.BELONGS_TO_PROJECT: Dimension.CONTEXT,
    EdgeType.SERVES_GOAL: Dimension.CONTEXT,
    EdgeType.IN_SESSION: Dimension.CONTEXT,
    EdgeType.LINKED: Dimension.CONTEXT,
    EdgeType.SAME_SUBJECT: Dimension.ENTITY,
}


def _short_uuid() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class EntityRef:
    name: str
    type: str = "concept"
    role: str = ""


@dataclass
class MemoryNode:
    """A node in the multi-dimensional memory graph."""

    id: str = field(default_factory=_short_uuid)
    content: str = ""
    node_type: NodeType = NodeType.EVENT

    occurred_at: datetime = field(default_factory=datetime.now)
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    entities: list[EntityRef] = field(default_factory=list)

    action_verb: str = ""
    action_category: str = ""

    session_id: str = ""
    project: str = ""
    goal: str = ""
    agent_id: str = ""
    user_id: str = "default"
    workspace_id: str = "default"

    importance: float = 0.5
    confidence: float = 0.5
    access_count: int = 0
    embedding: bytes | None = None

    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if self.valid_from is None:
            self.valid_from = self.occurred_at


@dataclass
class MemoryEdge:
    """A typed, weighted edge between two memory nodes."""

    id: str = field(default_factory=_short_uuid)
    source_id: str = ""
    target_id: str = ""
    edge_type: EdgeType = EdgeType.RELATED_TO
    dimension: Dimension = Dimension.ENTITY
    weight: float = 0.5
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if self.dimension == Dimension.ENTITY and self.edge_type != EdgeType.RELATED_TO:
            inferred = EDGE_DIMENSION.get(self.edge_type)
            if inferred:
                self.dimension = inferred


@dataclass
class EncodingResult:
    """Output from a single encoding pass."""

    nodes: list[MemoryNode] = field(default_factory=list)
    edges: list[MemoryEdge] = field(default_factory=list)


@dataclass
class RetrievalResult:
    """A scored node returned from graph retrieval."""

    node: MemoryNode
    score: float = 0.0
    path: list[str] = field(default_factory=list)
    dimensions_matched: list[Dimension] = field(default_factory=list)

