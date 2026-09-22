from ascent_map.prospect.model import Fact, SignalResult
from ascent_map.prospect.rules import evaluate_match, evaluate_rule


def signal(signal_id, value, score, confidence=0.9):
    return SignalResult(signal_id, value, score, confidence, "present")


def test_ordinal_rule_comparison():
    result = evaluate_rule(
        {"signal": "digital_maturity", "operator": ">=", "value": "medium"},
        {},
        {"digital_maturity": signal("digital_maturity", "high", 0.75)},
    )
    assert result["state"] == "true"


def test_match_separates_fit_from_evidence_coverage():
    subject = {
        "id": "svc",
        "positive_rules": [
            {"signal": "known", "operator": ">=", "value": 0.5, "weight": 0.5},
            {"signal": "missing", "operator": ">=", "value": 0.5, "weight": 0.5},
        ],
    }
    result = evaluate_match(subject, {}, {"known": signal("known", 0.9, 0.9)}, min_coverage=0.6)
    assert result.score == 1.0
    assert result.coverage == 0.5
    assert result.status == "insufficient_evidence"


def test_failed_prerequisite_is_ineligible():
    subject = {
        "id": "svc",
        "prerequisites": [{"fact": "organization.status", "operator": "not_in", "value": ["permanently_closed"]}],
        "positive_rules": [],
    }
    facts = {"organization.status": Fact("organization.status", "permanently_closed", "present", 1.0)}
    result = evaluate_match(subject, facts, {})
    assert result.status == "ineligible"
