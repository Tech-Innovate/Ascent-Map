import json
from pathlib import Path

from ascent_map.config import project_paths
from ascent_map.db import Database
from ascent_map.prospect.pipeline import evaluate_all, ingest_maps
from ascent_map.prospect.report import latest_business_report


def test_end_to_end_maps_pipeline(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    paths = project_paths(root)
    db = Database(tmp_path / "ascent-map.duckdb")
    db.initialize(paths.schema)

    source = tmp_path / "maps.json"
    source.write_text(json.dumps([{
        "title": "Example Dental Clinic",
        "place_id": "example-place-1",
        "category": "Dental clinic",
        "categories": ["Dental clinic"],
        "address": "Example Street",
        "latitude": 21.5,
        "longitude": 39.2,
        "web_site": "https://example-clinic.invalid",
        "phone": "+966500000000",
        "review_count": 1200,
        "review_rating": 4.3,
        "open_hours": {"Sunday": ["9 AM–9 PM"], "Monday": ["9 AM–9 PM"]},
    }]), encoding="utf-8")

    stats = ingest_maps(db, paths, source)
    assert stats["records"] == 1
    assert stats["businesses"] == 1
    assert stats["evidence"] > 0

    evaluations = evaluate_all(db, paths)
    assert len(evaluations) == 1

    reports = latest_business_report(db)
    assert len(reports) == 1
    assert reports[0]["name"] == "Example Dental Clinic"
    assert reports[0]["profile_version"] == 1
    assert reports[0]["services"]
    assert reports[0]["channels"]
    assert reports[0]["profile"]["signals"]["appointment_driven"]["value"] is True
