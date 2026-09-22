from __future__ import annotations

from .model import Fact, SignalResult
from .signals import clamp, combined_confidence, evidence_ids


def _fact(facts: dict[str, Fact], predicate: str) -> Fact:
    return facts.get(predicate, Fact(predicate=predicate))


def augment_review_signals(facts: dict[str, Fact], signals: dict[str, SignalResult]) -> dict[str, SignalResult]:
    slow = _fact(facts, "reviews.slow_response_topic")
    unanswered = _fact(facts, "reviews.unanswered_topic")
    booking = _fact(facts, "reviews.booking_difficulty_topic")
    waiting = _fact(facts, "reviews.wait_time_topic")
    delivery = _fact(facts, "reviews.delivery_problem_topic")

    response_sources = [item for item in (slow, unanswered) if item.known and isinstance(item.value, (int, float))]
    if response_sources:
        strongest = max(response_sources, key=lambda item: float(item.value))
        score = clamp(float(strongest.value))
        signals["responsiveness_gap"] = SignalResult(
            "responsiveness_gap",
            score,
            score,
            strongest.confidence,
            "present",
            strongest.evidence_ids,
            ["recurring response/answering friction observed in public review sample"],
        )

    complaint_sources = [
        item for item in (slow, unanswered, booking, waiting, delivery)
        if item.known and isinstance(item.value, (int, float))
    ]
    if complaint_sources:
        strongest = max(float(item.value) for item in complaint_sources)
        average = sum(float(item.value) for item in complaint_sources) / len(complaint_sources)
        score = clamp(0.70 * strongest + 0.30 * average)
        signals["complaint_intensity"] = SignalResult(
            "complaint_intensity",
            score,
            score,
            combined_confidence(*(item.confidence for item in complaint_sources)),
            "present",
            evidence_ids(*complaint_sources),
            ["review-topic evidence across customer-friction categories"],
        )

        current_gap = signals.get("customer_experience_gap")
        current_score = float(current_gap.score or 0.0) if current_gap and current_gap.known else 0.0
        strengthened = max(current_score, score)
        confidence = combined_confidence(
            current_gap.confidence * 0.6 if current_gap and current_gap.known else 0.0,
            *(item.confidence for item in complaint_sources),
        )
        signals["customer_experience_gap"] = SignalResult(
            "customer_experience_gap",
            strengthened,
            strengthened,
            confidence,
            "present",
            evidence_ids(current_gap, *complaint_sources),
            ["rating baseline strengthened by explicit review-topic evidence"],
        )

    # Booking complaints are useful evidence of customer friction, but they do
    # not prove the internal process is manual. Keep manual_process_indicator
    # unknown until direct operational evidence exists.
    return signals
