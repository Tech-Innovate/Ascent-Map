from __future__ import annotations

from .model import Fact, SignalResult
from .signals import clamp, combined_confidence, evidence_ids, ordinal


def _fact(facts: dict[str, Fact], predicate: str) -> Fact:
    return facts.get(predicate, Fact(predicate=predicate))


def augment_web_signals(facts: dict[str, Fact], signals: dict[str, SignalResult]) -> dict[str, SignalResult]:
    booking = _fact(facts, "website.booking_present")
    ecommerce = _fact(facts, "website.ecommerce_present")
    checkout = _fact(facts, "website.checkout_present")
    portal = _fact(facts, "website.customer_portal_present")
    multilingual = _fact(facts, "digital.multilingual")
    languages = _fact(facts, "website.languages")
    form = _fact(facts, "contact.form.state")
    instagram = _fact(facts, "contact.instagram.state")
    facebook = _fact(facts, "contact.facebook.state")

    if booking.value is True:
        signals["online_booking"] = SignalResult(
            "online_booking", True, 1.0, booking.confidence, "present", booking.evidence_ids,
            ["official website booking capability observed"],
        )
        current = signals.get("appointment_driven")
        confidence = combined_confidence(booking.confidence, current.confidence if current and current.known else 0.0)
        signals["appointment_driven"] = SignalResult(
            "appointment_driven", True, 1.0, confidence, "present",
            evidence_ids(booking, current) if current else booking.evidence_ids,
            ["official website booking capability observed"],
        )

    if ecommerce.value is True or checkout.value is True:
        source = checkout if checkout.value is True else ecommerce
        signals["ecommerce_capability"] = SignalResult(
            "ecommerce_capability", True, 1.0 if checkout.value is True else 0.90,
            source.confidence, "present", source.evidence_ids,
            ["official website commerce capability observed"],
        )
        signals["transaction_driven"] = SignalResult(
            "transaction_driven", True, 0.95, source.confidence, "present", source.evidence_ids,
            ["official website transaction path observed"],
        )

    if portal.value is True:
        signals["customer_portal"] = SignalResult(
            "customer_portal", True, 1.0, portal.confidence, "present", portal.evidence_ids,
            ["official website customer portal observed"],
        )

    if multilingual.value is True or (languages.known and isinstance(languages.value, list) and len(languages.value) >= 2):
        source = multilingual if multilingual.value is True else languages
        signals["multilingual_digital_presence"] = SignalResult(
            "multilingual_digital_presence", 0.95, 0.95, source.confidence, "present", source.evidence_ids,
            ["multiple public website languages observed"],
        )

    if form.value == "present":
        signals["contact_form_presence"] = SignalResult(
            "contact_form_presence", "present", 1.0, form.confidence, "present", form.evidence_ids,
            ["official website contact form observed"],
        )

    social_sources = [item for item in (instagram, facebook) if item.value == "present"]
    if social_sources:
        score = 0.85 if len(social_sources) >= 2 else 0.70
        signals["social_messaging_presence"] = SignalResult(
            "social_messaging_presence", score, score,
            combined_confidence(*(item.confidence for item in social_sources)), "present",
            evidence_ids(*social_sources), ["official social channel links observed"],
        )

    # Digital maturity is a conservative observed-capability score. Missing
    # website features do not count as false; they simply contribute no evidence.
    capability_weights = [
        ("digital.website_present", 0.20),
        ("digital.https", 0.10),
        ("digital.mobile_ready", 0.10),
        ("website.booking_present", 0.15),
        ("website.ecommerce_present", 0.15),
        ("website.customer_portal_present", 0.10),
        ("contact.whatsapp.state", 0.08),
        ("contact.form.state", 0.05),
        ("digital.multilingual", 0.07),
    ]
    positive = 0.0
    known_weight = 0.0
    confidence_weighted = 0.0
    contributing: list[Fact] = []
    for predicate, weight in capability_weights:
        item = _fact(facts, predicate)
        if not item.known:
            continue
        known_weight += weight
        confidence_weighted += weight * item.confidence
        contributing.append(item)
        is_positive = item.value is True or item.value == "present"
        if is_positive:
            positive += weight

    if contributing:
        existing = signals.get("digital_maturity")
        observed_score = clamp(positive)
        score = max(observed_score, float(existing.score or 0.0) if existing and existing.known else 0.0)
        coverage = known_weight / sum(weight for _, weight in capability_weights)
        confidence = (confidence_weighted / known_weight) * coverage if known_weight else 0.0
        if existing and existing.known:
            confidence = combined_confidence(confidence, existing.confidence * 0.6)
        signals["digital_maturity"] = SignalResult(
            "digital_maturity", ordinal(score), score, confidence, "present",
            evidence_ids(*contributing), ["official website observable capability set"],
        )

    return signals
