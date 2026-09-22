from __future__ import annotations

import json
from typing import Any

from ascent_map.db import Database, decode_json, rows_as_dicts

from .identity import organization_representatives, organization_scope


def _report_ids(con, business_id: str | None) -> list[str]:
    if business_id:
        _, representative, _ = organization_scope(con, business_id)
        return [representative]
    organizations = organization_representatives(con)
    if organizations:
        return organizations
    # Before entity resolution/evaluation has run, retain the useful V0.2
    # behavior rather than returning an empty report.
    return [row[0] for row in con.execute("SELECT business_id FROM business ORDER BY canonical_name").fetchall()]


def _candidate_edges(con, members: list[str]) -> list[dict[str, Any]]:
    if not members:
        return []
    placeholders = ",".join("?" for _ in members)
    rows = rows_as_dicts(con.execute(
        f"""SELECT left_business_id, right_business_id, score, reasons_json
            FROM entity_resolution_edge
            WHERE decision = 'candidate'
              AND (left_business_id IN ({placeholders}) OR right_business_id IN ({placeholders}))
            ORDER BY score DESC""",
        [*members, *members],
    ))
    for row in rows:
        row["reasons"] = decode_json(row.pop("reasons_json"))
    return rows


def latest_business_report(db: Database, business_id: str | None = None) -> list[dict[str, Any]]:
    with db.connect() as con:
        ids = _report_ids(con, business_id)
        reports: list[dict[str, Any]] = []
        for item in ids:
            if not item:
                continue
            organization_id, representative, members = organization_scope(con, item)
            business = con.execute("SELECT canonical_name FROM business WHERE business_id = ?", [representative]).fetchone()
            if not business:
                continue
            profile = con.execute(
                """SELECT profile_id, profile_version, as_of, profile_json, completeness, contradiction_count
                   FROM profile_snapshot WHERE business_id = ? ORDER BY profile_version DESC LIMIT 1""",
                [representative],
            ).fetchone()
            candidates = _candidate_edges(con, members)
            if not profile:
                reports.append({
                    "business_id": representative,
                    "organization_id": organization_id,
                    "member_business_ids": members,
                    "name": business[0],
                    "profile": None,
                    "services": [],
                    "channels": [],
                    "entity_candidates": candidates,
                })
                continue
            profile_id = profile[0]
            profile_json = decode_json(profile[3])
            services = rows_as_dicts(con.execute(
                """SELECT service_id, status, fit_score, evidence_confidence,
                          positive_factors_json, limiting_factors_json, uncertainties_json
                   FROM service_match WHERE profile_id = ? ORDER BY fit_score DESC NULLS LAST""", [profile_id]))
            channels = rows_as_dicts(con.execute(
                """SELECT channel_id, suitability, evidence_confidence, positive_factors_json, cautions_json
                   FROM channel_match WHERE profile_id = ? ORDER BY suitability DESC NULLS LAST""", [profile_id]))
            for row in services:
                for key in ("positive_factors_json", "limiting_factors_json", "uncertainties_json"):
                    row[key.removesuffix("_json")] = decode_json(row.pop(key))
            for row in channels:
                for key in ("positive_factors_json", "cautions_json"):
                    row[key.removesuffix("_json")] = decode_json(row.pop(key))
            name = profile_json.get("identity", {}).get("name") or business[0]
            reports.append({
                "business_id": representative,
                "organization_id": organization_id,
                "member_business_ids": members,
                "name": name,
                "profile_id": profile_id,
                "profile_version": profile[1],
                "as_of": str(profile[2]),
                "profile": profile_json,
                "completeness": float(profile[4] or 0),
                "contradictions": int(profile[5] or 0),
                "services": services,
                "channels": channels,
                "entity_candidates": candidates,
            })
        return reports


def render_text(reports: list[dict[str, Any]]) -> str:
    output: list[str] = []
    for report in reports:
        heading = f"{report['name']}  [{report['business_id']}]"
        output.extend([heading, "=" * min(88, max(20, len(heading)))])
        if report.get("organization_id"):
            output.append(
                f"Organization {report['organization_id']} | locations/listings {len(report.get('member_business_ids', []))}"
            )
        if not report.get("profile"):
            output.extend(["No profile yet. Run: ascent-map evaluate", ""])
            continue
        output.append(f"Profile v{report['profile_version']} | completeness {report['completeness']:.0%} | contradictions {report['contradictions']}")
        review_signals = report["profile"].get("signals", {})
        visible_review_signals = [
            key for key in ("responsiveness_gap", "complaint_intensity", "customer_experience_gap")
            if review_signals.get(key, {}).get("state") == "present"
        ]
        if visible_review_signals:
            output.append("Review intelligence:")
            for key in visible_review_signals:
                item = review_signals[key]
                score = item.get("score")
                output.append(f"  {key:<34} {'—' if score is None else f'{float(score):.0%}':>5}  confidence {float(item.get('confidence') or 0):.0%}")
        output.append("Services:")
        for item in report["services"]:
            score = "—" if item["fit_score"] is None else f"{float(item['fit_score']):.0%}"
            confidence = "—" if item["evidence_confidence"] is None else f"{float(item['evidence_confidence']):.0%}"
            output.append(f"  {item['service_id']:<34} {score:>5}  confidence {confidence:>5}  {item['status']}")
        output.append("Channels:")
        for item in report["channels"]:
            score = "—" if item["suitability"] is None else f"{float(item['suitability']):.0%}"
            confidence = "—" if item["evidence_confidence"] is None else f"{float(item['evidence_confidence']):.0%}"
            output.append(f"  {item['channel_id']:<34} {score:>5}  confidence {confidence:>5}")
        if report.get("entity_candidates"):
            output.append(f"Entity-resolution candidates requiring review: {len(report['entity_candidates'])}")
        output.append("")
    return "\n".join(output).rstrip() + "\n"


def render_json(reports: list[dict[str, Any]]) -> str:
    return json.dumps(reports, ensure_ascii=False, indent=2, default=str) + "\n"
