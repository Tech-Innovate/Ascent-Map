from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .ingest import json_value, stable_id, value_type

RULE_VERSION = "review-topics-v1"


@dataclass(frozen=True)
class NormalizedReview:
    external_review_id: str | None
    source_field: str
    rating: float | None
    text: str | None
    language: str | None
    published_at: datetime | None
    published_label: str | None
    has_owner_reply: bool
    content_sha256: str


def _pick(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _rating(data: dict[str, Any]) -> float | None:
    value = _pick(data, "rating_float", "Rating", "rating")
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    return rating if 0 <= rating <= 5 else None


def _published_at(data: dict[str, Any]) -> datetime | None:
    iso = _pick(data, "published_at", "PublishedAt")
    if isinstance(iso, str):
        try:
            parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    micros = _pick(data, "posted_at_unix_micros", "PostedAtUnixMicros")
    try:
        micros_int = int(micros)
    except (TypeError, ValueError):
        return None
    if micros_int <= 0:
        return None
    try:
        return datetime.fromtimestamp(micros_int / 1_000_000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def normalize_review(data: dict[str, Any], source_field: str) -> NormalizedReview | None:
    text_value = _pick(
        data,
        "text_original",
        "TextOriginal",
        "Description",
        "description",
        "text_translated",
        "TextTranslated",
        "text",
    )
    text = " ".join(str(text_value).split()) if text_value is not None else None
    rating = _rating(data)
    external_id = _pick(data, "review_id", "ReviewID")
    language = _pick(data, "language", "Language", "translated_lang", "TranslatedLang")
    label = _pick(data, "When", "when")
    reply = _pick(data, "reply_text", "ReplyText", "reply_text_original", "ReplyTextOriginal")
    published_at = _published_at(data)
    if not text and rating is None and not external_id:
        return None
    canonical = {
        "external_review_id": str(external_id) if external_id else None,
        "rating": rating,
        "text": text,
        "published_at": published_at.isoformat() if published_at else None,
        "published_label": str(label) if label else None,
    }
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return NormalizedReview(
        external_review_id=str(external_id) if external_id else None,
        source_field=source_field,
        rating=rating,
        text=text,
        language=str(language) if language else None,
        published_at=published_at,
        published_label=str(label) if label else None,
        has_owner_reply=bool(reply),
        content_sha256=digest,
    )


def extract_reviews(record: dict[str, Any]) -> list[NormalizedReview]:
    reviews: list[NormalizedReview] = []
    seen: set[str] = set()
    for field in ("user_reviews", "user_reviews_extended"):
        values = record.get(field)
        if not isinstance(values, list):
            continue
        for raw in values:
            if not isinstance(raw, dict):
                continue
            review = normalize_review(raw, field)
            if review and review.content_sha256 not in seen:
                seen.add(review.content_sha256)
                reviews.append(review)
    return reviews


def store_reviews(con, business_id: str, source_entity_id: str, artifact_id: str, record: dict[str, Any], collected_at: datetime) -> int:
    added = 0
    for review in extract_reviews(record):
        review_id = stable_id("rev", business_id, review.content_sha256)
        existed = con.execute(
            "SELECT count(*) FROM review_observation WHERE review_observation_id = ?", [review_id]
        ).fetchone()[0]
        con.execute(
            """INSERT INTO review_observation
               (review_observation_id, business_id, source_entity_id, artifact_id,
                external_review_id, source_field, rating, text, language, published_at,
                published_label, has_owner_reply, content_sha256, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT DO NOTHING""",
            [
                review_id,
                business_id,
                source_entity_id,
                artifact_id,
                review.external_review_id,
                review.source_field,
                review.rating,
                review.text,
                review.language,
                review.published_at,
                review.published_label,
                review.has_owner_reply,
                review.content_sha256,
                collected_at,
            ],
        )
        if not existed:
            added += 1
    return added


def _normalize_text(value: str) -> str:
    text = value.casefold()
    text = re.sub(r"[^\w\s\u0600-\u06ff]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


TOPICS: dict[str, dict[str, Any]] = {
    "slow_response": {
        "phrases": [
            "no response", "no reply", "never replied", "never responded", "does not answer",
            "doesn t answer", "did not answer", "nobody answers", "slow response", "late response",
            "لا يرد", "لا يجيب", "عدم الرد", "تأخر الرد", "تاخر الرد", "بطيء في الرد", "بطيء بالرد",
            "ما يردون", "لا أحد يرد", "لا احد يرد",
        ],
        "groups": [
            ["response", "reply", "answer", "respond", "رد", "يرد", "يجيب"],
            ["slow", "late", "delay", "never", "not", "no", "بطيء", "تأخر", "تاخر", "عدم", "لا"],
        ],
    },
    "unanswered": {
        "phrases": [
            "no response", "no reply", "never replied", "never responded", "does not answer",
            "doesn t answer", "did not answer", "nobody answers", "لا يرد", "لا يجيب", "عدم الرد",
            "ما يردون", "لا أحد يرد", "لا احد يرد",
        ],
        "groups": [],
    },
    "booking_difficulty": {
        "phrases": [
            "can t book", "cannot book", "couldn t book", "booking problem", "booking issue",
            "appointment problem", "appointment issue", "difficult to book", "hard to book",
            "مشكلة في الحجز", "مشكلة بالحجز", "صعوبة الحجز", "صعب الحجز", "تعذر الحجز",
            "مشكلة في الموعد", "مشكلة بالموعد",
        ],
        "groups": [
            ["book", "booking", "appointment", "reservation", "حجز", "موعد"],
            ["problem", "issue", "difficult", "hard", "cannot", "can t", "unable", "مشكلة", "صعب", "صعوبة", "تعذر"],
        ],
    },
    "wait_time": {
        "phrases": [
            "long wait", "waiting time", "waited too long", "very late", "long queue", "too much waiting",
            "وقت انتظار طويل", "انتظار طويل", "طول الانتظار", "تأخير طويل", "تاخير طويل", "تأخر الموعد", "تاخر الموعد",
        ],
        "groups": [
            ["wait", "waiting", "queue", "delay", "late", "انتظار", "تأخير", "تاخير", "تأخر", "تاخر"],
            ["long", "too long", "very", "hours", "طويل", "كثير", "ساعة", "ساعات"],
        ],
    },
    "delivery_problem": {
        "phrases": [
            "late delivery", "delivery problem", "delivery issue", "order never arrived", "missing order",
            "تأخر التوصيل", "تاخر التوصيل", "مشكلة التوصيل", "الطلب لم يصل", "الطلب ما وصل",
        ],
        "groups": [
            ["delivery", "order", "توصيل", "طلب"],
            ["late", "delay", "missing", "never arrived", "problem", "issue", "تأخر", "تاخر", "مشكلة", "لم يصل", "ما وصل"],
        ],
    },
}


def _matches_topic(text: str, rating: float | None, rule: dict[str, Any]) -> bool:
    normalized = _normalize_text(text)
    if any(phrase in normalized for phrase in rule["phrases"]):
        return True
    groups = rule.get("groups") or []
    if groups and (rating is None or rating <= 3.0):
        return all(any(term in normalized for term in group) for group in groups)
    return False


def _topic_strength(matches: int, sample_size: int) -> float:
    if matches <= 0 or sample_size <= 0:
        return 0.0
    ratio = matches / sample_size
    volume_component = min(1.0, matches / 5.0)
    prevalence_component = min(1.0, ratio / 0.20)
    score = 0.5 * volume_component + 0.5 * prevalence_component
    if matches == 1:
        score = min(score, 0.25)
    return max(0.0, min(1.0, score))


def _topic_confidence(sample_size: int, matches: int) -> float:
    if matches <= 0:
        return 0.0
    sample_component = min(1.0, sample_size / 30.0)
    match_component = min(1.0, matches / 5.0)
    return min(0.90, 0.45 + 0.30 * sample_component + 0.15 * match_component)


def refresh_review_topic_evidence(con, business_ids: list[str], collected_at: datetime) -> dict[str, int]:
    stats = {"reviews": 0, "topic_evidence": 0}
    for business_id in business_ids:
        # Derived topic state is temporal. Historical summaries remain in
        # review_topic_summary, but only evidence from the latest refresh may
        # participate in current fact resolution.
        con.execute(
            """UPDATE evidence SET is_active = false
               WHERE business_id = ? AND collector = 'ascent-map/review-rules' AND is_active = true""",
            [business_id],
        )
        rows = con.execute(
            """SELECT review_observation_id, source_entity_id, artifact_id, rating, text
               FROM review_observation
               WHERE business_id = ? AND text IS NOT NULL AND length(trim(text)) > 0
               ORDER BY collected_at DESC, review_observation_id""",
            [business_id],
        ).fetchall()
        stats["reviews"] += len(rows)
        if not rows:
            continue
        source_entity_id = next((row[1] for row in rows if row[1]), None)
        artifact_id = next((row[2] for row in rows if row[2]), None)
        for topic_id, rule in TOPICS.items():
            matched = [row for row in rows if _matches_topic(str(row[4]), row[3], rule)]
            if not matched:
                continue
            score = _topic_strength(len(matched), len(rows))
            confidence = _topic_confidence(len(rows), len(matched))
            review_ids = [row[0] for row in matched]
            predicate = f"reviews.{topic_id}_topic"
            summary_id = stable_id(
                "rts", business_id, topic_id, RULE_VERSION, len(rows), len(matched), json_value(review_ids), score
            )
            con.execute(
                """INSERT INTO review_topic_summary
                   (summary_id, business_id, topic_id, score, confidence, sample_size,
                    matched_count, review_ids_json, rule_version, evaluated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT DO NOTHING""",
                [summary_id, business_id, topic_id, score, confidence, len(rows), len(matched),
                 json_value(review_ids), RULE_VERSION, collected_at],
            )
            encoded = json_value(score)
            evidence_id = stable_id("ev", "review_topic", summary_id, predicate, encoded)
            existed = con.execute("SELECT count(*) FROM evidence WHERE evidence_id = ?", [evidence_id]).fetchone()[0]
            notes = json.dumps({"sample_size": len(rows), "matched_count": len(matched), "review_ids": review_ids})
            con.execute(
                """INSERT INTO evidence
                   (evidence_id, business_id, source_entity_id, artifact_id, predicate, value_json,
                    value_type, observed_at, collected_at, source_reliability, directness,
                    extraction_confidence, collector, collector_version, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.90, 0.72, ?,
                           'ascent-map/review-rules', ?, ?)
                   ON CONFLICT DO NOTHING""",
                [evidence_id, business_id, source_entity_id, artifact_id, predicate, encoded,
                 value_type(score), collected_at, collected_at, confidence, RULE_VERSION, notes],
            )
            con.execute(
                """UPDATE evidence
                   SET is_active = true, observed_at = ?, collected_at = ?, notes = ?
                   WHERE evidence_id = ?""",
                [collected_at, collected_at, notes, evidence_id],
            )
            if not existed:
                stats["topic_evidence"] += 1
    return stats
