from __future__ import annotations

from typing import Any

from .model import Fact, MatchResult, SignalResult
from .signals import ORDINAL


def _to_comparable(value: Any, score: float | None = None) -> Any:
    if isinstance(value, str) and value in ORDINAL:
        return ORDINAL[value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if score is not None and isinstance(value, str) and value not in {"present", "absent"}:
        return score
    return value


def _operator(left: Any, op: str, right: Any) -> bool:
    if op == "exists":
        return left is not None
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "in":
        return left in right
    if op == "not_in":
        return left not in right
    left_cmp = _to_comparable(left)
    right_cmp = _to_comparable(right)
    if left_cmp is None or right_cmp is None:
        return False
    if op == ">=":
        return left_cmp >= right_cmp
    if op == "<=":
        return left_cmp <= right_cmp
    if op == ">":
        return left_cmp > right_cmp
    if op == "<":
        return left_cmp < right_cmp
    raise ValueError(f"Unsupported operator: {op}")


def _compound_confidence(kind: str, state: str, children: list[dict[str, Any]]) -> float:
    if not children or state == "unknown":
        return 0.0
    if kind == "any":
        if state == "true":
            relevant = [child for child in children if child["state"] == "true"]
            return max((float(child.get("confidence", 0.0)) for child in relevant), default=0.0)
        # Every branch must be false to conclude an OR is false. The weakest
        # false branch bounds confidence in that conclusion.
        return min((float(child.get("confidence", 0.0)) for child in children), default=0.0)
    if state == "false":
        # One confidently false branch is enough to falsify an AND.
        relevant = [child for child in children if child["state"] == "false"]
        return max((float(child.get("confidence", 0.0)) for child in relevant), default=0.0)
    # Every branch must be true to conclude an AND is true.
    return min((float(child.get("confidence", 0.0)) for child in children), default=0.0)


def evaluate_rule(rule: dict[str, Any], facts: dict[str, Fact], signals: dict[str, SignalResult]) -> dict[str, Any]:
    if "any" in rule:
        children = [evaluate_rule(child, facts, signals) for child in rule["any"]]
        if any(child["state"] == "true" for child in children):
            state = "true"
        elif any(child["state"] == "unknown" for child in children):
            state = "unknown"
        else:
            state = "false"
        return {
            "state": state,
            "kind": "any",
            "children": children,
            "confidence": _compound_confidence("any", state, children),
        }
    if "all" in rule:
        children = [evaluate_rule(child, facts, signals) for child in rule["all"]]
        if any(child["state"] == "false" for child in children):
            state = "false"
        elif any(child["state"] == "unknown" for child in children):
            state = "unknown"
        else:
            state = "true"
        return {
            "state": state,
            "kind": "all",
            "children": children,
            "confidence": _compound_confidence("all", state, children),
        }

    source_kind = "signal" if "signal" in rule else "fact"
    source_id = rule.get(source_kind)
    if not source_id:
        raise ValueError(f"Rule must reference signal, fact, any, or all: {rule}")
    if source_kind == "signal":
        item = signals.get(source_id, SignalResult(signal_id=source_id))
        known, value, confidence, score = item.known, item.value, item.confidence, item.score
    else:
        item = facts.get(source_id, Fact(predicate=source_id))
        known, value, confidence, score = item.known, item.value, item.confidence, None
    if not known:
        return {"state": "unknown", "source": source_kind, "id": source_id, "confidence": confidence}
    operator = rule.get("operator", "==")
    expected = rule.get("value")
    matched = _operator(_to_comparable(value, score), operator, _to_comparable(expected))
    return {
        "state": "true" if matched else "false",
        "source": source_kind,
        "id": source_id,
        "value": value,
        "score": score,
        "operator": operator,
        "expected": expected,
        "confidence": confidence,
    }


def evaluate_match(
    subject: dict[str, Any],
    facts: dict[str, Fact],
    signals: dict[str, SignalResult],
    thresholds: dict[str, float] | None = None,
    min_coverage: float = 0.40,
) -> MatchResult:
    subject_id = subject["id"]
    prerequisites = subject.get("prerequisites", []) or []
    prerequisite_trace = [evaluate_rule(rule, facts, signals) for rule in prerequisites]
    failed_prerequisites = [item for item in prerequisite_trace if item["state"] == "false"]
    if failed_prerequisites:
        confidence = max((float(item.get("confidence", 0.0)) for item in failed_prerequisites), default=0.0)
        return MatchResult(
            subject_id,
            "ineligible",
            0.0,
            confidence,
            1.0,
            limiting_factors=["hard prerequisite not met"],
            rule_trace=prerequisite_trace,
        )
    prerequisite_unknown = bool(prerequisites and any(item["state"] == "unknown" for item in prerequisite_trace))

    positives = subject.get("positive_rules", []) or []
    total_weight = sum(float(rule.get("weight", 0.0)) for rule in positives)
    known_weight = weighted_fit = weighted_confidence = 0.0
    positive_factors: list[str] = []
    uncertainties: list[str] = []
    trace = list(prerequisite_trace)
    for rule in positives:
        result = evaluate_rule(rule, facts, signals)
        weight = float(rule.get("weight", 0.0))
        result["weight"] = weight
        trace.append(result)
        source = str(rule.get("signal") or rule.get("fact") or result.get("kind"))
        if result["state"] == "unknown":
            uncertainties.append(f"{source} unknown")
            continue
        known_weight += weight
        weighted_confidence += weight * float(result.get("confidence", 0.0))
        if result["state"] == "true":
            weighted_fit += weight
            positive_factors.append(source)

    coverage = known_weight / total_weight if total_weight else 0.0
    score = weighted_fit / known_weight if known_weight else None
    confidence = (weighted_confidence / known_weight) * coverage if known_weight else 0.0
    limiting_factors: list[str] = []
    for rule in subject.get("limiting_rules", []) or []:
        result = evaluate_rule(rule, facts, signals)
        trace.append(result)
        source = str(rule.get("signal") or rule.get("fact") or result.get("kind"))
        if result["state"] == "unknown":
            uncertainties.append(f"{source} limiting evidence unknown")
        elif result["state"] == "true" and score is not None:
            score = max(0.0, score - float(rule.get("penalty", 0.0)))
            limiting_factors.append(source)

    if prerequisite_unknown or coverage < min_coverage or score is None:
        status = "insufficient_evidence"
    else:
        levels = thresholds or {"candidate": 0.50, "good_fit": 0.70, "strong_fit": 0.85}
        if score >= levels.get("strong_fit", 0.85):
            status = "strong_fit"
        elif score >= levels.get("good_fit", 0.70):
            status = "good_fit"
        elif score >= levels.get("candidate", 0.50):
            status = "candidate"
        else:
            status = "low_fit"
    return MatchResult(
        subject_id,
        status,
        score,
        confidence,
        coverage,
        positive_factors,
        limiting_factors,
        uncertainties,
        trace,
    )
