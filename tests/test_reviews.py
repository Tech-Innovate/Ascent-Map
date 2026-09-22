import json
from pathlib import Path

from ascent_map.config import project_paths
from ascent_map.db import Database
from ascent_map.prospect.pipeline import evaluate_all, ingest_maps
from ascent_map.prospect.report import latest_business_report
from ascent_map.prospect.reviews import normalize_review


def test_normalize_gosom_review_omits_reviewer_identity():
    raw = {
        "Name": "Should Not Be Persisted",
        "ProfilePicture": "https://example.invalid/avatar.jpg",
        "Rating": 2,
        "Description": "No response for two days",
        "When": "a week ago",
        "review_id": "review-123",
        "posted_at_unix_micros": 1_700_000_000_000_000,
        "language": "en",
        "reply_text": "Please contact us",
    }
    review = normalize_review(raw, "user_reviews_extended")
    assert review is not None
    assert review.rating == 2.0
    assert review.text == "No response for two days"
    assert review.external_review_id == "review-123"
    assert review.has_owner_reply is True
    assert not hasattr(review, "name")
    assert "Should Not Be Persisted" not in repr(review)


def test_review_topics_feed_responsiveness_and_service_fit(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    paths = project_paths(root)
    db = Database(tmp_path / "reviews.duckdb")
    db.initialize(paths.schema)
    source = tmp_path / "reviews.json"
    source.write_text(json.dumps([{
        "title": "Example Dental Clinic",
        "place_id": "review-place-1",
        "category": "Dental clinic",
        "categories": ["Dental clinic"],
        "status": "Open",
        "web_site": "https://example-clinic.invalid",
        "phone": "+966500000000",
        "review_count": 1200,
        "review_rating": 4.2,
        "user_reviews_extended": [
            {"review_id": "r1", "Rating": 1, "Description": "No response to calls or messages"},
            {"review_id": "r2", "Rating": 2, "Description": "Very slow response and difficult to book an appointment"},
            {"review_id": "r3", "Rating": 2, "Description": "لا أحد يرد وتأخر الرد كثيراً"},
            {"review_id": "r4", "Rating": 5, "Description": "Excellent doctor and friendly staff"},
            {"review_id": "r5", "Rating": 5, "Description": "Great experience"},
        ],
    }]), encoding="utf-8")

    stats = ingest_maps(db, paths, source)
    assert stats["reviews"] == 5
    evaluate_all(db, paths)
    report = latest_business_report(db)[0]
    signals = report["profile"]["signals"]
    assert signals["responsiveness_gap"]["state"] == "present"
    assert signals["responsiveness_gap"]["score"] >= 0.5
    assert signals["complaint_intensity"]["state"] == "present"
    assert signals["manual_process_indicator"]["state"] == "unknown"

    messaging = next(item for item in report["services"] if item["service_id"] == "customer_messaging_automation")
    assert "responsiveness_gap" in messaging["positive_factors"]
    assert messaging["fit_score"] is not None


def test_review_topic_refresh_supersedes_stale_higher_score(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    paths = project_paths(root)
    db = Database(tmp_path / "temporal-reviews.duckdb")
    db.initialize(paths.schema)

    first = tmp_path / "first.json"
    complaints = [
        {"review_id": f"c{index}", "Rating": 1, "Description": "No response at all"}
        for index in range(3)
    ]
    base = {
        "title": "Temporal Clinic",
        "place_id": "temporal-place",
        "category": "Dental clinic",
        "status": "Open",
        "phone": "+966500000099",
        "review_count": 3,
        "review_rating": 2.0,
    }
    first.write_text(json.dumps([{**base, "user_reviews_extended": complaints}]), encoding="utf-8")
    ingest_maps(db, paths, first)
    evaluate_all(db, paths)
    first_score = latest_business_report(db)[0]["profile"]["signals"]["responsiveness_gap"]["score"]

    second = tmp_path / "second.json"
    positives = [
        {"review_id": f"p{index}", "Rating": 5, "Description": "Excellent service"}
        for index in range(27)
    ]
    second.write_text(json.dumps([{
        **base,
        "review_count": 30,
        "review_rating": 4.5,
        "user_reviews_extended": complaints + positives,
    }]), encoding="utf-8")
    ingest_maps(db, paths, second)
    evaluate_all(db, paths)
    second_score = latest_business_report(db)[0]["profile"]["signals"]["responsiveness_gap"]["score"]

    assert first_score > second_score
    with db.connect() as con:
        active = con.execute(
            """SELECT count(*) FROM evidence
               WHERE business_id = (SELECT business_id FROM business LIMIT 1)
                 AND predicate = 'reviews.slow_response_topic'
                 AND collector = 'ascent-map/review-rules' AND is_active = true"""
        ).fetchone()[0]
    assert active == 1
