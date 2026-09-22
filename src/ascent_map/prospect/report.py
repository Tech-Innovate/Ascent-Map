from __future__ import annotations

import json
from typing import Any

from ascent_map.db import Database, decode_json, rows_as_dicts


def latest_business_report(db: Database, business_id: str | None = None) -> list[dict[str, Any]]:
    with db.connect() as con:
        ids = [business_id] if business_id else [row[0] for row in con.execute("SELECT business_id FROM business ORDER BY canonical_name").fetchall()]
        reports: list[dict[str, Any]] = []
        for item in ids:
            if not item:
                continue
            business = con.execute("SELECT canonical_name FROM business WHERE business_id = ?", [item]).fetchone()
            if not business:
                continue
            profile = con.execute(
                """SELECT profile_id, profile_version, as_of, profile_json, completeness, contradiction_count
                   FROM profile_snapshot WHERE business_id = ? ORDER BY profile_version DESC LIMIT 1""",
                [item],
            ).fetchone()
            if not profile:
                reports.append({"business_id": item, "name": business[0], "profile": None, "services": [], "channels": []})
                continue
            profile_id = profile[0]
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
            reports.append({
                "business_id": item,
                "name": business[0],
                "profile_id": profile_id,
                "profile_version": profile[1],
                "as_of": str(profile[2]),
                "profile": decode_json(profile[3]),
                "completeness": float(profile[4] or 0),
                "contradictions": int(profile[5] or 0),
                "services": services,
                "channels": channels,
            })
        return reports


def render_text(reports: list[dict[str, Any]]) -> str:
    output: list[str] = []
    for report in reports:
        heading = f"{report['name']}  [{report['business_id']}]"
        output.extend([heading, "=" * min(88, max(20, len(heading)))])
        if not report.get("profile"):
            output.extend(["No profile yet. Run: ascent-map evaluate", ""])
            continue
        output.append(f"Profile v{report['profile_version']} | completeness {report['completeness']:.0%} | contradictions {report['contradictions']}")
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
        output.append("")
    return "\n".join(output).rstrip() + "\n"


def render_json(reports: list[dict[str, Any]]) -> str:
    return json.dumps(reports, ensure_ascii=False, indent=2, default=str) + "\n"
