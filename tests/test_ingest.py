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


def test_parse_business_groups_by_domain_and_emits_presence_facts():
    record = {
        "title": "Example Clinic",
        "place_id": "pid-1",
        "web_site": "https://www.example.sa/",
        "phone": "+966500000000",
        "review_count": 800,
        "category": "Medical clinic",
    }
    parsed = parse_business(record, MAPPING)
    assert parsed.business_id.startswith("biz_")
    assert parsed.predicates["digital.website_present"] is True
    assert parsed.predicates["contact.phone.state"] == "present"
