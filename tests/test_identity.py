import json
from pathlib import Path

from ascent_map.config import project_paths
from ascent_map.db import Database
from ascent_map.prospect.identity import resolve_organizations
from ascent_map.prospect.pipeline import evaluate_all, ingest_maps
from ascent_map.prospect.report import latest_business_report


def _db(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    paths = project_paths(root)
    db = Database(tmp_path / "identity.duckdb")
    db.initialize(paths.schema)
    return paths, db


def test_same_brand_domain_groups_branches_without_merging_listing_identity(tmp_path: Path):
    paths, db = _db(tmp_path)
    source = tmp_path / "branches.json"
    source.write_text(json.dumps([
        {
            "title": "Example Dental Clinic - Jeddah",
            "place_id": "place-jed",
            "category": "Dental clinic",
            "categories": ["Dental clinic"],
            "address": "Jeddah Branch",
            "latitude": 21.50,
            "longitude": 39.20,
            "web_site": "https://example-clinic.invalid",
            "phone": "+966500000001",
            "review_count": 300,
            "review_rating": 4.2,
        },
        {
            "title": "Example Dental Clinic - Riyadh",
            "place_id": "place-ruh",
            "category": "Dental clinic",
            "categories": ["Dental clinic"],
            "address": "Riyadh Branch",
            "latitude": 24.70,
            "longitude": 46.70,
            "web_site": "https://example-clinic.invalid",
            "phone": "+966500000002",
            "review_count": 200,
            "review_rating": 4.4,
        },
    ]), encoding="utf-8")

    stats = ingest_maps(db, paths, source)
    assert stats["businesses"] == 2
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM business").fetchone()[0] == 2

    resolution = resolve_organizations(db)
    assert resolution["organizations"] == 1
    assert resolution["linked_edges"] == 1

    evaluate_all(db, paths)
    reports = latest_business_report(db)
    assert len(reports) == 1
    profile = reports[0]["profile"]
    assert len(profile["identity"]["member_business_ids"]) == 2
    assert profile["facts"]["organization.location_count"]["value"] == 2
    assert profile["facts"]["reputation.review_count"]["value"] == 500
    assert abs(profile["facts"]["reputation.review_rating"]["value"] - 4.28) < 0.001
    assert profile["signals"]["multi_location"]["value"] is True


def test_shared_domain_with_dissimilar_names_is_candidate_not_auto_linked(tmp_path: Path):
    paths, db = _db(tmp_path)
    source = tmp_path / "shared-host.json"
    source.write_text(json.dumps([
        {
            "title": "Alpha Medical Center",
            "place_id": "alpha-place",
            "web_site": "https://shared-host.invalid/alpha",
            "phone": "+966500000010",
        },
        {
            "title": "Completely Different Restaurant",
            "place_id": "restaurant-place",
            "web_site": "https://shared-host.invalid/restaurant",
            "phone": "+966500000020",
        },
    ]), encoding="utf-8")
    ingest_maps(db, paths, source)
    resolution = resolve_organizations(db)
    assert resolution["organizations"] == 2
    assert resolution["linked_edges"] == 0
    assert resolution["candidate_edges"] == 1
