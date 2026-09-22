from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any

from ascent_map.db import Database, decode_json, rows_as_dicts

from .ingest import json_value, stable_id, website_domain

SHARED_HOSTS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "linktr.ee",
    "wa.me",
    "whatsapp.com",
    "google.com",
    "maps.google.com",
    "bit.ly",
    "tinyurl.com",
}


@dataclass(frozen=True)
class EntityFeatures:
    business_id: str
    name: str
    normalized_name: str
    created_at: object
    domains: frozenset[str]
    phones: frozenset[str]


def normalize_phone(value: str) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) < 7:
        return None
    return digits[-9:]


def name_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    sequence = SequenceMatcher(None, left, right).ratio()
    l_tokens, r_tokens = set(left.split()), set(right.split())
    union = l_tokens | r_tokens
    jaccard = len(l_tokens & r_tokens) / len(union) if union else 0.0
    containment = 1.0 if min(len(left), len(right)) >= 5 and (left in right or right in left) else 0.0
    return max(sequence, jaccard, containment * 0.88)


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return []


def _features(con) -> list[EntityFeatures]:
    businesses = rows_as_dicts(con.execute(
        "SELECT business_id, canonical_name, normalized_name, created_at FROM business ORDER BY created_at, business_id"
    ))
    evidence_rows = rows_as_dicts(con.execute(
        """SELECT business_id, predicate, value_json, collected_at
           FROM evidence
           WHERE is_active = true AND predicate IN (
             'digital.website.url', 'contact.phone.value', 'contact.phone.values'
           )
           ORDER BY collected_at DESC"""
    ))
    domains: dict[str, set[str]] = defaultdict(set)
    phones: dict[str, set[str]] = defaultdict(set)
    for row in evidence_rows:
        value = decode_json(row["value_json"])
        if row["predicate"] == "digital.website.url":
            for raw in _flatten_strings(value):
                domain = website_domain(raw)
                if domain:
                    domains[row["business_id"]].add(domain)
        else:
            for raw in _flatten_strings(value):
                phone = normalize_phone(raw)
                if phone:
                    phones[row["business_id"]].add(phone)
    return [
        EntityFeatures(
            row["business_id"],
            row["canonical_name"],
            row["normalized_name"] or "",
            row["created_at"],
            frozenset(domains[row["business_id"]]),
            frozenset(phones[row["business_id"]]),
        )
        for row in businesses
    ]


def _shared_domain(left: EntityFeatures, right: EntityFeatures) -> set[str]:
    return {domain for domain in left.domains & right.domains if domain not in SHARED_HOSTS}


def score_pair(left: EntityFeatures, right: EntityFeatures) -> tuple[float, str, list[str]]:
    similarity = name_similarity(left.normalized_name, right.normalized_name)
    domains = _shared_domain(left, right)
    phones = left.phones & right.phones
    reasons: list[str] = []
    if domains:
        reasons.append(f"shared official domain: {sorted(domains)[0]}")
    if phones:
        reasons.append("shared normalized phone")
    if similarity >= 0.65:
        reasons.append(f"name similarity {similarity:.2f}")

    if domains and phones:
        return 0.99, "linked", reasons
    if domains and similarity >= 0.65:
        return min(0.98, 0.82 + 0.16 * similarity), "linked", reasons
    if phones and similarity >= 0.55:
        return min(0.98, 0.88 + 0.10 * similarity), "linked", reasons
    if domains:
        return 0.60 + 0.20 * similarity, "candidate", reasons or ["shared official domain"]
    if phones:
        return 0.65 + 0.20 * similarity, "candidate", reasons or ["shared normalized phone"]
    if left.normalized_name and left.normalized_name == right.normalized_name:
        return 0.58, "candidate", ["exact normalized name only"]
    return 0.0, "rejected", []


def _bucket_pairs(items: list[EntityFeatures]) -> set[tuple[str, str]]:
    ordered = sorted(items, key=lambda item: (item.normalized_name, item.business_id))
    if len(ordered) <= 50:
        return {
            tuple(sorted((left.business_id, right.business_id)))
            for left, right in combinations(ordered, 2)
        }
    # Very large brands/chains must not turn resolution into O(n²). Compare a
    # stable anchor to each member plus adjacent names. This is conservative:
    # uncertain sub-clusters remain candidates rather than being force-merged.
    anchor = ordered[0]
    pairs = {
        tuple(sorted((anchor.business_id, item.business_id)))
        for item in ordered[1:]
    }
    pairs.update(
        tuple(sorted((ordered[index - 1].business_id, ordered[index].business_id)))
        for index in range(1, len(ordered))
    )
    return pairs


def _candidate_pairs(features: list[EntityFeatures]) -> set[tuple[str, str]]:
    by_domain: dict[str, list[EntityFeatures]] = defaultdict(list)
    by_phone: dict[str, list[EntityFeatures]] = defaultdict(list)
    by_name: dict[str, list[EntityFeatures]] = defaultdict(list)
    for item in features:
        for domain in item.domains:
            if domain not in SHARED_HOSTS:
                by_domain[domain].append(item)
        for phone in item.phones:
            by_phone[phone].append(item)
        if item.normalized_name:
            by_name[item.normalized_name].append(item)

    pairs: set[tuple[str, str]] = set()
    for bucket in (*by_domain.values(), *by_phone.values(), *by_name.values()):
        if len(bucket) > 1:
            pairs.update(_bucket_pairs(bucket))
    return pairs


class _UnionFind:
    def __init__(self, ids: list[str]):
        self.parent = {item: item for item in ids}

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            next_item = self.parent[item]
            self.parent[item] = root
            item = next_item
        return root

    def union(self, left: str, right: str) -> None:
        l_root, r_root = self.find(left), self.find(right)
        if l_root != r_root:
            self.parent[max(l_root, r_root)] = min(l_root, r_root)


def resolve_organizations(db: Database) -> dict[str, int]:
    with db.connect() as con:
        features = _features(con)
        by_id = {item.business_id: item for item in features}
        ids = list(by_id)
        uf = _UnionFind(ids)
        edges: list[tuple[EntityFeatures, EntityFeatures, float, str, list[str]]] = []
        for left_id, right_id in sorted(_candidate_pairs(features)):
            left, right = by_id[left_id], by_id[right_id]
            score, decision, reasons = score_pair(left, right)
            if decision == "rejected":
                continue
            edges.append((left, right, score, decision, reasons))
            if decision == "linked":
                uf.union(left.business_id, right.business_id)

        components: dict[str, list[EntityFeatures]] = defaultdict(list)
        for item in features:
            components[uf.find(item.business_id)].append(item)

        # DuckDB's FK implementation does not permit deleting child and parent
        # rows in the same explicit transaction. These tables are entirely
        # derived and reproducible, so perform the dependency-ordered cleanup
        # as autocommitted statements, then insert the newly resolved graph atomically.
        con.execute("DELETE FROM entity_resolution_edge")
        con.execute("DELETE FROM organization_member")
        con.execute("DELETE FROM organization")

        con.execute("BEGIN TRANSACTION")
        try:
            for left, right, score, decision, reasons in edges:
                left_id, right_id = sorted((left.business_id, right.business_id))
                edge_id = stable_id("edge", left_id, right_id)
                con.execute(
                    """INSERT INTO entity_resolution_edge
                       (edge_id, left_business_id, right_business_id, score, decision, reasons_json)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [edge_id, left_id, right_id, score, decision, json_value(reasons)],
                )

            linked_edges = [edge for edge in edges if edge[3] == "linked"]
            for members in components.values():
                ordered = sorted(members, key=lambda item: (item.created_at, item.business_id))
                representative = ordered[0]
                organization_id = stable_id("org", representative.business_id)
                member_ids = {item.business_id for item in members}
                component_scores = [
                    score for left, right, score, _, _ in linked_edges
                    if left.business_id in member_ids and right.business_id in member_ids
                ]
                confidence = min(component_scores) if component_scores else 1.0
                con.execute(
                    """INSERT INTO organization
                       (organization_id, canonical_name, normalized_name, representative_business_id,
                        resolution_confidence)
                       VALUES (?, ?, ?, ?, ?)""",
                    [organization_id, representative.name, representative.normalized_name,
                     representative.business_id, confidence],
                )
                for member in ordered:
                    reasons: list[str] = []
                    for left, right, _, _, edge_reasons in linked_edges:
                        if member.business_id in {left.business_id, right.business_id} and (
                            left.business_id in member_ids and right.business_id in member_ids
                        ):
                            reasons.extend(edge_reasons)
                    con.execute(
                        """INSERT INTO organization_member
                           (business_id, organization_id, confidence, reasons_json)
                           VALUES (?, ?, ?, ?)""",
                        [member.business_id, organization_id,
                         1.0 if len(members) == 1 else confidence,
                         json_value(sorted(set(reasons)))],
                    )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    return {
        "businesses": len(features),
        "organizations": len(components),
        "linked_edges": sum(1 for edge in edges if edge[3] == "linked"),
        "candidate_edges": sum(1 for edge in edges if edge[3] == "candidate"),
    }


def organization_scope(con, business_id: str) -> tuple[str | None, str, list[str]]:
    row = con.execute(
        """SELECT o.organization_id, o.representative_business_id
           FROM organization_member m
           JOIN organization o ON o.organization_id = m.organization_id
           WHERE m.business_id = ?""",
        [business_id],
    ).fetchone()
    if not row:
        return None, business_id, [business_id]
    organization_id, representative = row
    members = [
        item[0] for item in con.execute(
            "SELECT business_id FROM organization_member WHERE organization_id = ? ORDER BY business_id",
            [organization_id],
        ).fetchall()
    ]
    return organization_id, representative, members


def organization_representatives(con) -> list[str]:
    rows = con.execute(
        "SELECT representative_business_id FROM organization ORDER BY canonical_name, organization_id"
    ).fetchall()
    return [row[0] for row in rows]
