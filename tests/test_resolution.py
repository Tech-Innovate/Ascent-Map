from datetime import datetime, timedelta, timezone

import pytest

from ascent_map.prospect.pipeline import _resolve_items


BASE_TIME = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _item(
    evidence_id: str,
    source_entity_id: str,
    quality: float,
    *,
    value=True,
    collected_at=BASE_TIME,
    source_type=None,
    source_url=None,
):
    return {
        "evidence_id": evidence_id,
        "source_entity_id": source_entity_id,
        "value": value,
        "quality": quality,
        "collected_at": collected_at,
        "source_type": source_type,
        "source_url": source_url,
    }


def test_repeated_observation_from_same_source_does_not_inflate_confidence():
    fact = _resolve_items(
        "digital.website_present",
        [_item("ev1", "source-a", 0.8), _item("ev2", "source-a", 0.8)],
    )
    assert fact.confidence == pytest.approx(0.8)
    assert len(fact.evidence_ids) == 1


def test_distinct_sources_can_increase_confidence():
    fact = _resolve_items(
        "digital.website_present",
        [_item("ev1", "source-a", 0.8), _item("ev2", "source-b", 0.8)],
    )
    assert fact.confidence == pytest.approx(0.96)
    assert len(fact.evidence_ids) == 2


def test_latest_value_from_same_source_supersedes_old_value_without_conflict():
    fact = _resolve_items(
        "reputation.review_rating",
        [
            _item("old", "maps-place", 0.9, value=4.2, collected_at=BASE_TIME),
            _item("new", "maps-place", 0.9, value=4.4, collected_at=BASE_TIME + timedelta(hours=1)),
        ],
    )
    assert fact.value == 4.4
    assert fact.state == "present"
    assert fact.evidence_ids == ["new"]


def test_same_official_domain_across_branch_crawls_is_one_independent_web_source():
    fact = _resolve_items(
        "digital.website_present",
        [
            _item(
                "web-a", "web-source-a", 0.8,
                source_type="official_website", source_url="https://example.invalid/contact",
            ),
            _item(
                "web-b", "web-source-b", 0.8,
                source_type="official_website", source_url="https://example.invalid/about",
            ),
        ],
    )
    assert fact.confidence == pytest.approx(0.8)
    assert len(fact.evidence_ids) == 1
