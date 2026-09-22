from ascent_map.prospect.model import Fact
from ascent_map.prospect.signals import derive_signals


def fact(predicate, value, confidence=0.9):
    return Fact(predicate, value, "present", confidence, [f"ev:{predicate}"])


def test_maps_baseline_derives_interaction_and_appointment_signals():
    facts = {
        "organization.category": fact("organization.category", "Dental clinic"),
        "reputation.review_count": fact("reputation.review_count", 1200),
        "reputation.review_rating": fact("reputation.review_rating", 4.3),
        "digital.website_present": fact("digital.website_present", True),
        "contact.phone.state": fact("contact.phone.state", "present"),
        "organization.location_count": fact("organization.location_count", 1, 1.0),
    }
    definitions = [
        {"id": "customer_interaction_intensity"},
        {"id": "appointment_driven"},
        {"id": "digital_maturity"},
    ]
    signals = derive_signals(facts, definitions)
    assert signals["customer_interaction_intensity"].value == "very_high"
    assert signals["appointment_driven"].value is True
    assert signals["digital_maturity"].known


def test_unavailable_semantic_enrichment_stays_unknown():
    signals = derive_signals({}, [{"id": "responsiveness_gap"}])
    assert signals["responsiveness_gap"].state == "unknown"
    assert signals["responsiveness_gap"].score is None
