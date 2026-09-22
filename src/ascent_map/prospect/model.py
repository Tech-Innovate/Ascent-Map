from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


EVIDENCE_STATES = {"present", "absent", "unknown", "conflicting", "not_applicable"}


@dataclass
class Fact:
    predicate: str
    value: Any = None
    state: str = "unknown"
    confidence: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.state in {"present", "absent"}


@dataclass
class SignalResult:
    signal_id: str
    value: Any = None
    score: float | None = None
    confidence: float = 0.0
    state: str = "unknown"
    evidence_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.state in {"present", "absent"}


@dataclass
class MatchResult:
    subject_id: str
    status: str
    score: float | None
    evidence_confidence: float
    coverage: float
    positive_factors: list[str] = field(default_factory=list)
    limiting_factors: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    rule_trace: list[dict[str, Any]] = field(default_factory=list)
