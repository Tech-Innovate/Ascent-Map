from datetime import datetime, timezone

import pytest

from ascent_map.prospect.pipeline import _resolve_items


def _item(evidence_id: str, source_entity_id: str, quality: float):
    return {
        "evidence_id": evidence_id,
        "source_entity_id": source_entity_id,
        "value": True,
        "quality": quality,
        "collected_at": datetime(2026, 9, 22, tzinfo=timezone.utc),
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
