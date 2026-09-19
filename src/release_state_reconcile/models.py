from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Evidence:
    source_url: str
    object_type: str
    object_id: str
    observed_state: str
    confidence: str = "factual"


@dataclass
class Finding:
    name: str
    status: str
    summary: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class PullRecord:
    number: int
    url: str
    title: str
    state: str
    merged: bool
    merged_at: str | None
    merge_sha: str | None
    head_sha: str | None
    base: str
    labels: list[str]
    relation: str
    relation_evidence: list[Evidence] = field(default_factory=list)
    changed_files: int = 0


@dataclass
class RelationshipRecord:
    url: str
    provider: str
    repository: str | None
    object_type: str
    object_id: str
    scope: str
    relation: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class CandidateReport:
    candidate_url: str
    repo: str
    number: int
    observed_at: str
    target_release_branch: str
    mainline_branch: str
    findings: list[Finding]
    related_pulls: list[PullRecord]
    relationships: list[RelationshipRecord]
    contradictions: list[Finding]
    unknowns: list[Finding]
    materially_useful: bool
    api_requests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
