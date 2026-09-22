from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ascent_map.config import ProjectPaths, load_yaml
from ascent_map.db import Database, decode_json, rows_as_dicts

from .identity import organization_representatives, organization_scope, resolve_organizations
from .ingest import json_value, parse_business, read_maps_records, stable_id, utc_now, value_type
from .model import Fact, SignalResult
from .reviews import refresh_review_topic_evidence, store_reviews
from .rules import evaluate_match
from .signals import derive_signals

BRANCH_LOCAL_PREFIXES = (
    "location.",
    "source.google_maps.",
    "operations.open_hours",
    "operations.popular_times",
    "maps.reservations",
    "maps.order_online",
    "maps.menu",
    "reputation.reviews_per_rating",
)
PRESENCE_PREDICATES = {
    "digital.website_present",
    "maps.phone_present",
    "maps.reservations_present",
    "maps.order_online_present",
    "maps.menu_present",
    "website.booking_present",
    "website.ecommerce_present",
    "website.checkout_present",
    "website.customer_portal_present",
    "website.language_switcher",
    "digital.multilingual",
    "digital.mobile_ready",
    "organization.linkedin_company_page",
}
STATE_PREDICATES = {
    "contact.phone.state",
    "contact.email.state",
    "contact.whatsapp.state",
    "contact.form.state",
    "contact.instagram.state",
    "contact.facebook.state",
    "contact.linkedin.state",
}


def quality_confidence(reliability: float, directness: float, extraction: float) -> float:
    return (reliability * directness * extraction) ** (1 / 3)


def combined_group_confidence(values: list[float]) -> float:
    miss = 1.0
    for value in values:
        miss *= 1.0 - max(0.0, min(1.0, value))
    return 1.0 - miss


def ingest_maps(db: Database, paths: ProjectPaths, source: Path, collected_at: datetime | None = None) -> dict[str, int]:
    mapping: dict[str, str] = load_yaml(paths.config / "maps-field-mapping.yaml")["mappings"]
    records = read_maps_records(source)
    collected = collected_at or utc_now()
    raw = source.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    artifact_id = stable_id("art", sha256)
    raw_dir = paths.root / ".ascent-map" / "raw" / "maps"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{sha256}.json"
    if not raw_path.exists():
        raw_path.write_bytes(raw)

    stats = {"records": 0, "businesses": 0, "evidence": 0, "reviews": 0}
    seen_businesses: set[str] = set()
    with db.connect() as con:
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(
                """INSERT INTO raw_artifact
                   (artifact_id, source_type, media_type, storage_path, content_sha256,
                    collector, collector_version, collected_at)
                   VALUES (?, 'google_maps', 'application/json', ?, ?,
                           'gosom/google-maps-scraper', 'unknown', ?)
                   ON CONFLICT DO NOTHING""",
                [artifact_id, str(raw_path.relative_to(paths.root)), sha256, collected],
            )
            for record in records:
                parsed = parse_business(record, mapping)
                stats["records"] += 1
                seen_businesses.add(parsed.business_id)
                con.execute(
                    "INSERT INTO business (business_id, canonical_name, normalized_name) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    [parsed.business_id, parsed.canonical_name, parsed.normalized_name],
                )
                con.execute(
                    """INSERT INTO location
                       (location_id, business_id, address_text, street, city, region, postal_code,
                        country, latitude, longitude)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                    [parsed.location_id, parsed.business_id,
                     parsed.predicates.get("location.address_text"), parsed.predicates.get("location.street"),
                     parsed.predicates.get("location.city"), parsed.predicates.get("location.region"),
                     parsed.predicates.get("location.postal_code"), parsed.predicates.get("location.country"),
                     parsed.predicates.get("location.latitude"), parsed.predicates.get("location.longitude")],
                )
                con.execute(
                    """INSERT INTO source_entity
                       (source_entity_id, business_id, location_id, source_type, external_id_type,
                        external_id, source_url, first_seen_at, last_seen_at)
                       VALUES (?, ?, ?, 'google_maps', ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                    [parsed.source_entity_id, parsed.business_id, parsed.location_id, parsed.external_id_type,
                     parsed.external_id, parsed.source_url, collected, collected],
                )
                con.execute("UPDATE source_entity SET last_seen_at = ? WHERE source_entity_id = ?",
                            [collected, parsed.source_entity_id])
                for predicate, value in parsed.predicates.items():
                    encoded = json_value(value)
                    evidence_id = stable_id("ev", artifact_id, parsed.source_entity_id, predicate, encoded)
                    existed = con.execute("SELECT count(*) FROM evidence WHERE evidence_id = ?", [evidence_id]).fetchone()[0]
                    con.execute(
                        """INSERT INTO evidence
                           (evidence_id, business_id, location_id, source_entity_id, artifact_id,
                            predicate, value_json, value_type, observed_at, collected_at,
                            source_reliability, directness, extraction_confidence, collector, collector_version)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.90, 0.95, 0.95,
                                   'gosom/google-maps-scraper', 'unknown')
                           ON CONFLICT DO NOTHING""",
                        [evidence_id, parsed.business_id, parsed.location_id, parsed.source_entity_id,
                         artifact_id, predicate, encoded, value_type(value), collected, collected],
                    )
                    if not existed:
                        stats["evidence"] += 1
                stats["reviews"] += store_reviews(
                    con, parsed.business_id, parsed.source_entity_id, artifact_id, record, collected
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    stats["businesses"] = len(seen_businesses)
    return stats


def _write_fact(con, business_id: str, fact: Fact, method: str) -> None:
    current = con.execute(
        """SELECT resolved_value_json, state, confidence FROM resolved_fact
           WHERE business_id = ? AND predicate = ? AND valid_to IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        [business_id, fact.predicate],
    ).fetchone()
    if current and decode_json(current[0]) == fact.value and current[1] == fact.state and abs(float(current[2]) - fact.confidence) < 1e-9:
        return
    con.execute("UPDATE resolved_fact SET valid_to = now() WHERE business_id = ? AND predicate = ? AND valid_to IS NULL",
                [business_id, fact.predicate])
    fact_id = stable_id("fact", business_id, fact.predicate, json_value(fact.value), fact.state,
                        datetime.now(timezone.utc).isoformat())
    con.execute(
        """INSERT INTO resolved_fact
           (fact_id, business_id, predicate, resolved_value_json, state, confidence,
            resolution_method, rule_version, evidence_ids_json, valid_from)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'v0.3', ?, now())""",
        [fact_id, business_id, fact.predicate, json_value(fact.value), fact.state,
         fact.confidence, method, json_value(fact.evidence_ids)],
    )


def _dedupe_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, dict[str, Any]] = {}
    for item in items:
        source_key = str(item.get("source_entity_id") or item["evidence_id"])
        current = by_source.get(source_key)
        if current is None or (float(item["quality"]), item["collected_at"]) > (
            float(current["quality"]), current["collected_at"]
        ):
            by_source[source_key] = item
    return list(by_source.values())


def _resolve_items(predicate: str, items: list[dict[str, Any]]) -> Fact:
    if predicate in PRESENCE_PREDICATES:
        positives = [item for item in items if item["value"] is True]
        if positives:
            chosen = _dedupe_sources(positives)
            return Fact(predicate, True, "present",
                        combined_group_confidence([float(item["quality"]) for item in chosen]),
                        [item["evidence_id"] for item in chosen])
    if predicate in STATE_PREDICATES:
        positives = [item for item in items if item["value"] == "present"]
        if positives:
            chosen = _dedupe_sources(positives)
            return Fact(predicate, "present", "present",
                        combined_group_confidence([float(item["quality"]) for item in chosen]),
                        [item["evidence_id"] for item in chosen])
    if predicate.startswith("reviews.") and predicate.endswith("_topic"):
        numeric = [item for item in items if isinstance(item["value"], (int, float))]
        if numeric:
            strongest = max(numeric, key=lambda item: (float(item["value"]), float(item["quality"])))
            return Fact(predicate, float(strongest["value"]), "present", float(strongest["quality"]),
                        [strongest["evidence_id"]])

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[json_value(item["value"])].append(item)
    ranked: list[tuple[float, list[dict[str, Any]]]] = []
    for group in groups.values():
        independent = _dedupe_sources(group)
        ranked.append((combined_group_confidence([float(item["quality"]) for item in independent]), independent))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    top_confidence, top_items = ranked[0]
    state = "conflicting" if len(ranked) > 1 and ranked[1][0] >= top_confidence - 0.08 else "present"
    return Fact(predicate, top_items[0]["value"], state, top_confidence,
                [item["evidence_id"] for item in top_items])


def _aggregate_reputation(con, representative: str, members: list[str], facts: dict[str, Fact]) -> None:
    placeholders = ",".join("?" for _ in members)
    rows = rows_as_dicts(con.execute(
        f"""SELECT business_id, predicate, value_json, evidence_id, source_entity_id, collected_at,
                   source_reliability, directness, extraction_confidence
            FROM evidence
            WHERE business_id IN ({placeholders}) AND is_active = true
              AND predicate IN ('reputation.review_count', 'reputation.review_rating')
            ORDER BY business_id, predicate, collected_at DESC""",
        members,
    ))
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["business_id"], row["predicate"])
        if key in latest:
            continue
        row["value"] = decode_json(row["value_json"])
        row["quality"] = quality_confidence(float(row["source_reliability"] or 0),
                                             float(row["directness"] or 0),
                                             float(row["extraction_confidence"] or 0))
        latest[key] = row

    counts = {business_id: latest.get((business_id, "reputation.review_count")) for business_id in members}
    known_counts = [row for row in counts.values() if row is not None]
    if known_counts:
        total = sum(max(0, int(row["value"] or 0)) for row in known_counts)
        coverage = len(known_counts) / len(members)
        confidence = (sum(float(row["quality"]) for row in known_counts) / len(known_counts)) * coverage
        fact = Fact("reputation.review_count", total, "present", confidence,
                    [row["evidence_id"] for row in known_counts])
        facts[fact.predicate] = fact
        _write_fact(con, representative, fact, "organization_aggregate_v0.3")

    ratings = [latest.get((business_id, "reputation.review_rating")) for business_id in members]
    known_ratings = [row for row in ratings if row is not None]
    if known_ratings:
        weighted_sum = weight_total = 0.0
        for row in known_ratings:
            count_row = counts.get(row["business_id"])
            weight = max(1.0, float(count_row["value"])) if count_row else 1.0
            weighted_sum += float(row["value"]) * weight
            weight_total += weight
        value = weighted_sum / weight_total
        coverage = len(known_ratings) / len(members)
        confidence = (sum(float(row["quality"]) for row in known_ratings) / len(known_ratings)) * coverage
        fact = Fact("reputation.review_rating", value, "present", confidence,
                    [row["evidence_id"] for row in known_ratings])
        facts[fact.predicate] = fact
        _write_fact(con, representative, fact, "organization_aggregate_v0.3")


def resolve_facts(con, business_id: str) -> tuple[dict[str, Fact], str | None, list[str]]:
    organization_id, representative, members = organization_scope(con, business_id)
    placeholders = ",".join("?" for _ in members)
    rows = rows_as_dicts(con.execute(
        f"""SELECT evidence_id, business_id, source_entity_id, predicate, value_json, collected_at,
                   source_reliability, directness, extraction_confidence
            FROM evidence WHERE business_id IN ({placeholders}) AND is_active = true
            ORDER BY predicate, collected_at DESC""",
        members,
    ))
    by_predicate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if len(members) > 1 and row["business_id"] != representative and row["predicate"].startswith(BRANCH_LOCAL_PREFIXES):
            continue
        if row["predicate"] in {"reputation.review_count", "reputation.review_rating", "organization.name", "organization.categories", "organization.category"}:
            continue
        row["value"] = decode_json(row["value_json"])
        row["quality"] = quality_confidence(float(row["source_reliability"] or 0),
                                             float(row["directness"] or 0),
                                             float(row["extraction_confidence"] or 0))
        by_predicate[row["predicate"]].append(row)

    facts: dict[str, Fact] = {}
    for predicate, items in by_predicate.items():
        fact = _resolve_items(predicate, items)
        facts[predicate] = fact
        _write_fact(con, representative, fact, "evidence_resolution_v0.3")

    count = int(con.execute(
        f"SELECT count(*) FROM location WHERE business_id IN ({placeholders})", members
    ).fetchone()[0])
    location_fact = Fact("organization.location_count", count, "present", 1.0, [])
    facts[location_fact.predicate] = location_fact
    _write_fact(con, representative, location_fact, "organization_aggregate_v0.3")

    if organization_id:
        org = con.execute(
            "SELECT canonical_name, resolution_confidence FROM organization WHERE organization_id = ?",
            [organization_id],
        ).fetchone()
        if org:
            name_fact = Fact("organization.name", org[0], "present", float(org[1]), [])
            facts[name_fact.predicate] = name_fact
            _write_fact(con, representative, name_fact, "entity_resolution_v0.3")

    category_rows = con.execute(
        f"""SELECT value_json, evidence_id, source_reliability, directness, extraction_confidence
            FROM evidence WHERE business_id IN ({placeholders}) AND is_active = true
              AND predicate = 'organization.category' ORDER BY collected_at DESC""",
        members,
    ).fetchall()
    if category_rows:
        values = [str(decode_json(row[0])) for row in category_rows if decode_json(row[0])]
        if values:
            common, occurrences = Counter(values).most_common(1)[0]
            quality = sum(quality_confidence(float(row[2] or 0), float(row[3] or 0), float(row[4] or 0)) for row in category_rows) / len(category_rows)
            confidence = quality * (occurrences / len(values))
            fact = Fact("organization.category", common, "present", confidence, [row[1] for row in category_rows if str(decode_json(row[0])) == common])
            facts[fact.predicate] = fact
            _write_fact(con, representative, fact, "organization_aggregate_v0.3")

    categories_rows = con.execute(
        f"""SELECT value_json, evidence_id FROM evidence WHERE business_id IN ({placeholders})
            AND is_active = true AND predicate = 'organization.categories'""",
        members,
    ).fetchall()
    categories: set[str] = set()
    category_evidence: list[str] = []
    for value_json, evidence_id in categories_rows:
        value = decode_json(value_json)
        if isinstance(value, list):
            categories.update(str(item) for item in value if item)
            category_evidence.append(evidence_id)
    if categories:
        fact = Fact("organization.categories", sorted(categories), "present", 0.90, category_evidence)
        facts[fact.predicate] = fact
        _write_fact(con, representative, fact, "organization_aggregate_v0.3")

    _aggregate_reputation(con, representative, members, facts)
    return facts, organization_id, members


def _store_signal(con, business_id: str, signal: SignalResult) -> None:
    signal_fact_id = stable_id("sig", business_id, signal.signal_id, datetime.now(timezone.utc).isoformat())
    con.execute(
        """INSERT INTO signal_fact
           (signal_fact_id, business_id, signal_id, value_json, score, confidence, state,
            method_type, method_id, method_version, evidence_ids_json, explanation_json, evaluated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'rule', ?, 'v0.3', ?, ?, now())""",
        [signal_fact_id, business_id, signal.signal_id, json_value(signal.value), signal.score,
         signal.confidence, signal.state, f"baseline:{signal.signal_id}",
         json_value(signal.evidence_ids), json_value(signal.reasons)],
    )


def _profile(name: str, organization_id: str | None, members: list[str], facts: dict[str, Fact], signals: dict[str, SignalResult], completeness: float, contradictions: int) -> dict[str, Any]:
    return {
        "identity": {
            "name": name,
            "organization_id": organization_id,
            "member_business_ids": members,
        },
        "facts": {key: {"value": fact.value, "state": fact.state, "confidence": round(fact.confidence, 4)}
                  for key, fact in sorted(facts.items())},
        "signals": {key: {"value": signal.value, "score": None if signal.score is None else round(signal.score, 4),
                          "confidence": round(signal.confidence, 4), "state": signal.state,
                          "reasons": signal.reasons}
                    for key, signal in sorted(signals.items())},
        "data_quality": {"completeness": round(completeness, 4), "contradictions": contradictions},
    }


def evaluate_business(db: Database, paths: ProjectPaths, business_id: str) -> dict[str, Any]:
    signal_doc = load_yaml(paths.config / "signals.yaml")
    service_doc = load_yaml(paths.config / "services.yaml")
    channel_doc = load_yaml(paths.config / "channels.yaml")
    signal_defs = signal_doc["signals"]
    services = [item for item in service_doc["services"] if item.get("enabled", True)]
    channels = [item for item in channel_doc["channels"] if item.get("enabled", True)]

    with db.connect() as con:
        con.execute("BEGIN TRANSACTION")
        try:
            organization_id, representative, members = organization_scope(con, business_id)
            business = con.execute("SELECT canonical_name FROM business WHERE business_id = ?", [representative]).fetchone()
            if not business:
                raise KeyError(f"Unknown business_id: {business_id}")
            facts, organization_id, members = resolve_facts(con, representative)
            signals = derive_signals(facts, signal_defs)
            for signal in signals.values():
                _store_signal(con, representative, signal)

            completeness = sum(1 for signal in signals.values() if signal.known) / len(signal_defs) if signal_defs else 0.0
            contradictions = sum(1 for fact in facts.values() if fact.state == "conflicting")
            version = int(con.execute("SELECT coalesce(max(profile_version), 0) + 1 FROM profile_snapshot WHERE business_id = ?",
                                      [representative]).fetchone()[0])
            profile_id = stable_id("prof", representative, version)
            display_name = facts.get("organization.name", Fact("organization.name", business[0], "present", 1.0)).value
            profile = _profile(str(display_name), organization_id, members, facts, signals, completeness, contradictions)
            con.execute(
                """INSERT INTO profile_snapshot
                   (profile_id, business_id, profile_version, as_of, profile_json, completeness,
                    freshness, contradiction_count)
                   VALUES (?, ?, ?, now(), ?, ?, 1.0, ?)""",
                [profile_id, representative, version, json_value(profile), completeness, contradictions],
            )

            for service in services:
                con.execute(
                    """INSERT INTO service_definition
                       (service_id, service_version, name, portfolio, definition_json, enabled)
                       VALUES (?, '1', ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                    [service["id"], service["name"], service.get("portfolio"), json_value(service),
                     bool(service.get("enabled", True))],
                )
                result = evaluate_match(service, facts, signals, thresholds=service_doc.get("classifications", {}))
                con.execute(
                    """INSERT INTO service_match
                       (service_match_id, business_id, profile_id, service_id, service_version,
                        eligible, status, fit_score, evidence_confidence, positive_factors_json,
                        limiting_factors_json, uncertainties_json, rule_trace_json, evaluated_at)
                       VALUES (?, ?, ?, ?, '1', ?, ?, ?, ?, ?, ?, ?, ?, now())""",
                    [stable_id("sm", profile_id, service["id"]), representative, profile_id, service["id"],
                     result.status != "ineligible", result.status, result.score, result.evidence_confidence,
                     json_value(result.positive_factors), json_value(result.limiting_factors),
                     json_value(result.uncertainties), json_value(result.rule_trace)],
                )

            channel_cfg = channel_doc.get("selection", {})
            for channel in channels:
                result = evaluate_match(
                    channel, facts, signals,
                    thresholds={"candidate": channel_cfg.get("min_suitability", 0.45),
                                "good_fit": channel_cfg.get("strong_suitability", 0.75),
                                "strong_fit": 0.90},
                    min_coverage=channel_cfg.get("confidence_floor", 0.50),
                )
                con.execute(
                    """INSERT INTO channel_match
                       (channel_match_id, business_id, profile_id, channel_id, suitability,
                        evidence_confidence, positive_factors_json, cautions_json, rule_trace_json,
                        requires_human_review, evaluated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())""",
                    [stable_id("cm", profile_id, channel["id"]), representative, profile_id, channel["id"],
                     result.score, result.evidence_confidence, json_value(result.positive_factors),
                     json_value(channel.get("cautions", [])), json_value(result.rule_trace),
                     bool(channel_cfg.get("require_human_review", True))],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {"business_id": representative, "organization_id": organization_id, "profile_id": profile_id, "profile_version": version}


def evaluate_all(db: Database, paths: ProjectPaths, business_id: str | None = None) -> list[dict[str, Any]]:
    resolve_organizations(db)
    with db.connect() as con:
        all_business_ids = [row[0] for row in con.execute("SELECT business_id FROM business").fetchall()]
        con.execute("BEGIN TRANSACTION")
        try:
            refresh_review_topic_evidence(con, all_business_ids, utc_now())
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        if business_id:
            _, representative, _ = organization_scope(con, business_id)
            ids = [representative]
        else:
            ids = organization_representatives(con)
    return [evaluate_business(db, paths, item) for item in ids if item]
