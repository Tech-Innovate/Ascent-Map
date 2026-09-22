import json
from pathlib import Path

from ascent_map.prospect.ingest import parse_business, read_maps_records


MAPPING = {
    "title": "organization.name",
    "category": "organization.category",
    "review_count": "reputation.review_count",
    "web_site": "digital.website.url",
}


def test_read_json_array(tmp_path: Path):
    path = tmp_path / "maps.json"
    path.write_text(json.dumps([{"title": "A"}, {"title": "B"}]), encoding="utf-8")
    assert [item["title"] for item in read_maps_records(path)] == ["A", "B"]


def test_read_jsonl(tmp_path: Path):
    path = tmp_path / "maps.jsonl"
    path.write_text('{"title":"A"}\n{"title":"B"}\n', encoding="utf-8")
    assert len(read_maps_records(path)) == 2


def test_parse_business_keeps_listings_distinct_and_emits_presence_facts():
    first = {
        "title": "Example Clinic - Jeddah",
        "place_id": "pid-1",
        "web_site": "https://www.example.sa/",
        "phone": "+966500000000",
        "review_count": 800,
        "category": "Medical clinic",
    }
    second = {**first, "title": "Example Clinic - Riyadh", "place_id": "pid-2"}
    parsed = parse_business(first, MAPPING)
    parsed_second = parse_business(second, MAPPING)
    assert parsed.business_id.startswith("biz_")
    assert parsed.business_id != parsed_second.business_id
    assert parsed.predicates["digital.website_present"] is True
    assert parsed.predicates["contact.phone.state"] == "present"


def test_fallback_identity_ignores_volatile_review_fields():
    first = {
        "title": "No Place ID Business",
        "address": "Example Street",
        "latitude": 21.5,
        "longitude": 39.2,
        "review_count": 10,
        "review_rating": 4.0,
    }
    second = {**first, "review_count": 50, "review_rating": 4.5}
    assert parse_business(first, MAPPING).business_id == parse_business(second, MAPPING).business_id
