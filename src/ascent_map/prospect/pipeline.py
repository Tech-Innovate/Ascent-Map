from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ascent_map.config import ProjectPaths, load_yaml
from ascent_map.db import Database, decode_json, rows_as_dicts

from .ingest import json_value, parse_business, read_maps_records, stable_id, utc_now, value_type
from .model import Fact, MatchResult, SignalResult
from .rules import evaluate_match
from .signals import derive_signals


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

    stats = {"records": 0, "businesses": 0, "evidence": 0}
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
           VALUES (?, ?, ?, ?, ?, ?, ?, 'v0.1', ?, now())""",
        [fact_id, business_id, fact.predicate, json_value(fact.value), fact.state,
         fact.confidence, method, json_value(fact.evidence_ids)],
    )


def resolve_facts(con, business_id: str) -> dict[str, Fact]:
    rows = rows_as_dicts(con.execute(
        """SELECT evidence_id, predicate, value_json, collected_at,
                  source_reliability, directness, extraction_confidence
           FROM evidence WHERE business_id = ? AND is_active = true
           ORDER BY predicate, collected_at DESC""", [business_id]))
    by_predicate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        row["value"] = decode_json(row["value_json"])
        row["quality"] = quality_confidence(float(row["source_reliability"] or 0),
                                             float(row["directness"] or 0),
                                             float(row["extraction_confidence"] or 0))
        by_predicate[row["predicate"]].append(row)

    facts: dict[str, Fact] = {}
    for predicate, items in by_predicate.items():
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            groups[json_value(item["value"])].append(item)
        ranked = sorted(
            ((combined_group_confidence([float(x["quality"]) for x in group]), group)
             for group in groups.values()), key=lambda pair: pair[0], reverse=True)
        top_confidence, top_items = ranked[0]
        state = "conflicting" if len(ranked) > 1 and ranked[1][0] >= top_confidence - 0.08 else "present"
        fact = Fact(predicate, top_items[0]["value"], state, top_confidence,
                    [item["evidence_id"] for item in top_items])
        facts[predicate] = fact
        _write_fact(con, business_id, fact, "evidence_resolution_v0.1")

    count = int(con.execute("SELECT count(*) FROM location WHERE business_id = ?", [business_id]).fetchone()[0])
    location_fact = Fact("organization.location_count", count, "present", 1.0, [])
    facts[location_fact.predicate] = location_fact
    _write_fact(con, business_id, location_fact, "database_aggregate")
    return facts


def _store_signal(con, business_id: str, signal: SignalResult) -> None:
    signal_fact_id = stable_id("sig", business_id, signal.signal_id, datetime.now(timezone.utc).isoformat())
    con.execute(
        """INSERT INTO signal_fact
           (signal_fact_id, business_id, signal_id, value_json, score, confidence, state,
            method_type, method_id, method_version, evidence_ids_json, explanation_json, evaluated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'rule', ?, 'v0.1', ?, ?, now())""",
        [signal_fact_id, business_id, signal.signal_id, json_value(signal.value), signal.score,
         signal.confidence, signal.state, f"baseline:{signal.signal_id}",
         json_value(signal.evidence_ids), json_value(signal.reasons)],
    )


def _profile(name: str, facts: dict[str, Fact], signals: dict[str, SignalResult], completeness: float, contradictions: int) -> dict[str, Any]:
    return {
        "identity": {"name": name},
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
            business = con.execute("SELECT canonical_name FROM business WHERE business_id = ?", [business_id]).fetchone()
            if not business:
                raise KeyError(f"Unknown business_id: {business_id}")
            facts = resolve_facts(con, business_id)
            signals = derive_signals(facts, signal_defs)
            for signal in signals.values():
                _store_signal(con, business_id, signal)

            completeness = sum(1 for signal in signals.values() if signal.known) / len(signal_defs) if signal_defs else 0.0
            contradictions = sum(1 for fact in facts.values() if fact.state == "conflicting")
            version = int(con.execute("SELECT coalesce(max(profile_version), 0) + 1 FROM profile_snapshot WHERE business_id = ?",
                                      [business_id]).fetchone()[0])
            profile_id = stable_id("prof", business_id, version)
            profile = _profile(business[0], facts, signals, completeness, contradictions)
            con.execute(
                """INSERT INTO profile_snapshot
                   (profile_id, business_id, profile_version, as_of, profile_json, completeness,
                    freshness, contradiction_count)
                   VALUES (?, ?, ?, now(), ?, ?, 1.0, ?)""",
                [profile_id, business_id, version, json_value(profile), completeness, contradictions],
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
                    [stable_id("sm", profile_id, service["id"]), business_id, profile_id, service["id"],
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
                    [stable_id("cm", profile_id, channel["id"]), business_id, profile_id, channel["id"],
                     result.score, result.evidence_confidence, json_value(result.positive_factors),
                     json_value(channel.get("cautions", [])), json_value(result.rule_trace),
                     bool(channel_cfg.get("require_human_review", True))],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {"business_id": business_id, "profile_id": profile_id, "profile_version": version}


def evaluate_all(db: Database, paths: ProjectPaths, business_id: str | None = None) -> list[dict[str, Any]]:
    with db.connect() as con:
        ids = [business_id] if business_id else [row[0] for row in con.execute("SELECT business_id FROM business ORDER BY canonical_name").fetchall()]
    return [evaluate_business(db, paths, item) for item in ids if item]
