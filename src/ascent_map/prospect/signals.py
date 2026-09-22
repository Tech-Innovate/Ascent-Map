from __future__ import annotations

import math
from typing import Any

from .model import Fact, SignalResult

ORDINAL = {"very_low": 0.10, "low": 0.30, "medium": 0.50, "high": 0.75, "very_high": 0.95}
CONSUMER = ("restaurant", "cafe", "clinic", "medical", "dental", "salon", "spa", "barber", "hotel", "gym", "pharmacy", "shop", "store", "school", "veterinary", "workshop")
B2B = ("consult", "software", "manufacturer", "wholesale", "logistics", "industrial", "contractor", "engineering", "accounting", "supplier", "distribution", "freight")
APPOINTMENT = ("clinic", "medical", "dental", "dentist", "salon", "spa", "barber", "veterinary", "therapy", "physiotherapy", "consult")


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def ordinal(score: float) -> str:
    if score >= 0.85: return "very_high"
    if score >= 0.65: return "high"
    if score >= 0.40: return "medium"
    if score >= 0.20: return "low"
    return "very_low"


def combined_confidence(*values: float) -> float:
    usable = [clamp(v) for v in values if v > 0]
    if not usable:
        return 0.0
    miss = 1.0
    for value in usable:
        miss *= 1.0 - value
    return clamp(1.0 - miss)


def evidence_ids(*items: Fact | SignalResult | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item is None:
            continue
        for evidence_id in item.evidence_ids:
            if evidence_id not in seen:
                seen.add(evidence_id)
                result.append(evidence_id)
    return result


def unknown(signal_id: str, reason: str) -> SignalResult:
    return SignalResult(signal_id=signal_id, reasons=[reason])


class Context:
    def __init__(self, facts: dict[str, Fact]):
        self.facts = facts
        self.signals: dict[str, SignalResult] = {}

    def fact(self, predicate: str) -> Fact:
        return self.facts.get(predicate, Fact(predicate=predicate))

    def signal(self, signal_id: str) -> SignalResult:
        return self.signals.get(signal_id, SignalResult(signal_id=signal_id))

    def categories(self) -> tuple[str, Fact]:
        categories = self.fact("organization.categories")
        if categories.known and isinstance(categories.value, list):
            return " ".join(map(str, categories.value)).casefold(), categories
        category = self.fact("organization.category")
        return str(category.value or "").casefold(), category


def derive_baseline(ctx: Context) -> None:
    locations = ctx.fact("organization.location_count")
    if locations.known:
        count = int(locations.value or 0)
        ctx.signals["multi_location"] = SignalResult("multi_location", count >= 2, 1.0 if count >= 2 else 0.0, locations.confidence, "present", locations.evidence_ids, [f"{count} known location(s)"])

    category_text, category_fact = ctx.categories()
    reviews = ctx.fact("reputation.review_count")
    rating = ctx.fact("reputation.review_rating")
    reservations = ctx.fact("maps.reservations_present")
    ordering = ctx.fact("maps.order_online_present")
    menu = ctx.fact("maps.menu_present")
    website = ctx.fact("digital.website_present")
    phone = ctx.fact("contact.phone.state")
    email = ctx.fact("contact.email.state")
    hours = ctx.fact("operations.open_hours")

    if category_text or reviews.known:
        b2c = 0.80 if any(term in category_text for term in CONSUMER) else (0.60 if reviews.known and int(reviews.value or 0) >= 100 else 0.30)
        ctx.signals["b2c_orientation"] = SignalResult("b2c_orientation", b2c, b2c, combined_confidence(category_fact.confidence, reviews.confidence), "present", evidence_ids(category_fact, reviews), ["category/review footprint"])
    if category_text:
        b2b = 0.80 if any(term in category_text for term in B2B) else (0.20 if any(term in category_text for term in CONSUMER) else 0.45)
        ctx.signals["b2b_orientation"] = SignalResult("b2b_orientation", b2b, b2b, category_fact.confidence * 0.8, "present", category_fact.evidence_ids, ["category orientation"])

    if reviews.known or reservations.known or ordering.known:
        count = int(reviews.value or 0) if reviews.known else 0
        interaction = 0.95 if count >= 1000 else 0.78 if count >= 200 else 0.58 if count >= 50 else 0.32 if count >= 1 else 0.25
        if reservations.value is True: interaction = min(1.0, interaction + 0.08)
        if ordering.value is True: interaction = min(1.0, interaction + 0.08)
        ctx.signals["customer_interaction_intensity"] = SignalResult("customer_interaction_intensity", ordinal(interaction), interaction, combined_confidence(reviews.confidence, reservations.confidence, ordering.confidence), "present", evidence_ids(reviews, reservations, ordering), [f"{count} public reviews"])

    if reservations.value is True or any(term in category_text for term in APPOINTMENT):
        confidence = reservations.confidence if reservations.value is True else category_fact.confidence * 0.8
        ctx.signals["appointment_driven"] = SignalResult("appointment_driven", True, 1.0 if reservations.value is True else 0.85, confidence, "present", evidence_ids(reservations, category_fact), ["reservation/category evidence"])
    if ordering.value is True or menu.value is True:
        src = ordering if ordering.value is True else menu
        ctx.signals["transaction_driven"] = SignalResult("transaction_driven", True, 0.95 if ordering.value is True else 0.65, src.confidence, "present", src.evidence_ids, ["ordering/menu evidence"])
    if reservations.value is True:
        ctx.signals["online_booking"] = SignalResult("online_booking", True, 1.0, reservations.confidence, "present", reservations.evidence_ids, ["Maps reservation link observed"])
    if ordering.value is True:
        ctx.signals["ecommerce_capability"] = SignalResult("ecommerce_capability", True, 0.90, ordering.confidence, "present", ordering.evidence_ids, ["online ordering observed"])

    digital_inputs = [item for item in (website, reservations, ordering, email) if item.known]
    if digital_inputs:
        digital = (0.40 if website.value is True else 0.0) + (0.22 if reservations.value is True else 0.0) + (0.23 if ordering.value is True else 0.0) + (0.10 if email.value == "present" else 0.0)
        digital = clamp(digital)
        ctx.signals["digital_maturity"] = SignalResult("digital_maturity", ordinal(digital), digital, combined_confidence(*(item.confidence for item in digital_inputs)), "present", evidence_ids(*digital_inputs), ["observable digital capabilities"])

    whatsapp = ctx.fact("contact.whatsapp.state")
    if whatsapp.known:
        present = whatsapp.value == "present"
        ctx.signals["whatsapp_presence"] = SignalResult("whatsapp_presence", "present" if present else "absent", 1.0 if present else 0.0, whatsapp.confidence, whatsapp.state, whatsapp.evidence_ids, ["resolved WhatsApp state"])
    if phone.value == "present":
        interaction = ctx.signal("customer_interaction_intensity")
        score = 0.78 if interaction.known and (interaction.score or 0) >= 0.65 else 0.62
        ctx.signals["phone_reliance"] = SignalResult("phone_reliance", score, score, combined_confidence(phone.confidence, interaction.confidence * 0.5), "present", evidence_ids(phone, interaction), ["public phone observed"])
    if email.value == "present":
        ctx.signals["email_reliance"] = SignalResult("email_reliance", 0.65, 0.65, email.confidence * 0.8, "present", email.evidence_ids, ["public email observed"])

    if hours.known and isinstance(hours.value, dict):
        texts = [str(v).casefold() for values in hours.value.values() for v in (values if isinstance(values, list) else [values])]
        extended = any("24 hour" in value for value in texts) or len(hours.value) >= 7
        ctx.signals["extended_hours"] = SignalResult("extended_hours", extended, 1.0 if extended else 0.0, hours.confidence * 0.9, "present", hours.evidence_ids, ["opening-hours pattern"])

    known_ops = [ctx.signal(x) for x in ("multi_location", "appointment_driven", "transaction_driven", "extended_hours", "customer_interaction_intensity") if ctx.signal(x).known]
    if known_ops:
        complexity = 0.15
        if ctx.signal("multi_location").value is True: complexity += 0.28
        if ctx.signal("appointment_driven").value is True: complexity += 0.16
        if ctx.signal("transaction_driven").value is True: complexity += 0.14
        if ctx.signal("extended_hours").value is True: complexity += 0.12
        if ctx.signal("customer_interaction_intensity").known: complexity += 0.25 * float(ctx.signal("customer_interaction_intensity").score or 0)
        complexity = clamp(complexity)
        ctx.signals["operational_complexity"] = SignalResult("operational_complexity", ordinal(complexity), complexity, combined_confidence(*(s.confidence for s in known_ops)), "present", evidence_ids(*known_ops), ["operating footprint"])

    if locations.known or reviews.known:
        loc_count = int(locations.value or 0) if locations.known else 0
        review_count = int(reviews.value or 0) if reviews.known else 0
        size = 0.85 if loc_count >= 5 else 0.65 if loc_count >= 2 else 0.70 if review_count >= 2000 else 0.55 if review_count >= 500 else 0.25
        ctx.signals["organization_size"] = SignalResult("organization_size", ordinal(size), size, combined_confidence(locations.confidence * 0.8, reviews.confidence * 0.45), "present", evidence_ids(locations, reviews), ["public footprint proxy; not employee count"])

    if rating.known:
        volume = min(1.0, math.log10(max(1, int(reviews.value or 0))) / 4.0) if reviews.known else 0.25
        reputation = clamp(0.75 * clamp((float(rating.value) - 2.5) / 2.5) + 0.25 * volume)
        ctx.signals["reputation_strength"] = SignalResult("reputation_strength", reputation, reputation, combined_confidence(rating.confidence, reviews.confidence * 0.5), "present", evidence_ids(rating, reviews), ["rating and review volume"])
        gap = clamp((4.5 - float(rating.value)) / 2.0)
        gap_conf = rating.confidence * 0.45 if reviews.known and int(reviews.value or 0) < 20 else combined_confidence(rating.confidence, reviews.confidence * 0.6)
        ctx.signals["customer_experience_gap"] = SignalResult("customer_experience_gap", gap, gap, gap_conf, "present", evidence_ids(rating, reviews), ["coarse rating-based baseline"])

    digital = ctx.signal("digital_maturity")
    interaction = ctx.signal("customer_interaction_intensity")
    appointment = ctx.signal("appointment_driven")
    transaction = ctx.signal("transaction_driven")
    tech_known = [s for s in (digital, interaction, appointment, transaction) if s.known]
    if tech_known:
        tech = 0.15 + (0.40 * float(digital.score or 0) if digital.known else 0) + (0.18 if appointment.value is True else 0) + (0.18 if transaction.value is True else 0) + (0.18 * float(interaction.score or 0) if interaction.known else 0)
        tech = clamp(tech)
        ctx.signals["technology_dependency"] = SignalResult("technology_dependency", tech, tech, combined_confidence(*(s.confidence for s in tech_known)), "present", evidence_ids(*tech_known), ["digital and operating dependencies"])
    if digital.known and interaction.known:
        gap = clamp(float(interaction.score or 0) - float(digital.score or 0) + 0.20)
        ctx.signals["digital_gap"] = SignalResult("digital_gap", gap, gap, min(digital.confidence, interaction.confidence) * 0.8, "present", evidence_ids(digital, interaction), ["interaction demand relative to digital capability"])


def derive_signals(facts: dict[str, Fact], signal_definitions: list[dict[str, Any]]) -> dict[str, SignalResult]:
    ctx = Context(facts)
    derive_baseline(ctx)
    explicit_unknowns = {
        "responsiveness_gap": "review-topic evidence not observed",
        "manual_process_indicator": "current sources cannot prove a workflow is manual",
        "social_messaging_presence": "social-channel enrichment not yet observed",
        "contact_form_presence": "website form enrichment not yet observed",
    }
    for signal_id, reason in explicit_unknowns.items():
        ctx.signals.setdefault(signal_id, unknown(signal_id, reason))

    from .web_signals import augment_web_signals
    augment_web_signals(facts, ctx.signals)

    from .review_signals import augment_review_signals
    augment_review_signals(facts, ctx.signals)

    for definition in signal_definitions:
        signal_id = definition["id"]
        ctx.signals.setdefault(signal_id, unknown(signal_id, "no evaluator implemented for available evidence"))
    return ctx.signals
