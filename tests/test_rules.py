from copy import deepcopy
import pytest
from deskguard.catalog import default_rules, CONFIG_FIELDS
from deskguard.checking import evaluate_rule, evaluate, build_rules, summarize
from deskguard.importing import demo_assets
from deskguard.database import digest


def asset(role, config):
    return {"id": "a", "asset_key": "A01", "name": "合成资产", "role": role, "revision": 1, "config": config}


@pytest.mark.parametrize("rule", default_rules(), ids=lambda rule: rule["id"])
@pytest.mark.parametrize("mode", ["pass", "fail", "unknown", "disabled", "na"])
def test_rule_all_statuses(rule, mode):
    rule = deepcopy(rule)
    role = rule["roles"][0]
    expected = rule["expected"]
    value = expected
    if mode == "fail":
        if type(expected) is bool:
            value = not expected
        elif rule["operator"] == "ge":
            value = expected - 1
        else:
            value = expected + 1
    elif mode == "unknown":
        value = None
    elif mode == "disabled":
        rule["enabled"] = False
    elif mode == "na":
        role = "not-a-target-role"
    row = evaluate_rule(asset(role, {rule["field"]: value}), rule)
    assert row["status"] == mode
    assert row["rule_hash"] == digest(rule)


@pytest.mark.parametrize("value", [True, False, "15", 1.0, [], {}, -1, 36501])
def test_wrong_numeric_types_become_unknown(value):
    rule = default_rules()[4]
    row = evaluate_rule(asset("desktop", {"idle_minutes": value}), rule)
    assert row["status"] == "unknown"


@pytest.mark.parametrize("value", [0, 1, "true", "false", [], {}])
def test_boolean_no_coercion(value):
    assert evaluate_rule(asset("host", {"mfa": value}), default_rules()[0])["status"] == "unknown"


def test_idle_zero_is_not_safe():
    assert evaluate_rule(asset("desktop", {"idle_minutes": 0}), default_rules()[4])["status"] == "fail"


def test_unknown_not_a_pass_and_na_not_in_denominator():
    findings = [
        {"status": "pass", "weight": 10, "severity": "high"},
        {"status": "unknown", "weight": 10, "severity": "high"},
        {"status": "na", "weight": 10, "severity": "high"},
    ]
    result = summarize(findings)
    assert result["coverage_percent"] == 50
    assert result["evidence_score"] == 50
    assert result["pass_percent_known"] == 100
    assert result["counts"]["unknown"] == 1


def test_empty_summary_is_not_100_percent():
    result = summarize([])
    assert result["evidence_score"] is None
    assert result["coverage_percent"] is None


@pytest.mark.parametrize("expected", [0, -1, 36501, "180", True, 1.2])
def test_invalid_override_threshold(expected):
    with pytest.raises(ValueError):
        build_rules([{"id": "DG-04", "enabled": True, "expected": expected}])


def test_all_rules_disabled_rejected():
    with pytest.raises(ValueError):
        build_rules([{"id": rule["id"], "enabled": False} for rule in default_rules()])


def test_demo_exact_results_and_no_input_mutation():
    records = [dict(item, id=str(index), revision=1) for index, item in enumerate(demo_assets())]
    snapshot = {"assets": records, "baseline": {"rules": default_rules()}}
    checksum = digest(snapshot)
    a, b = evaluate(snapshot), evaluate(snapshot)
    assert a == b
    assert digest(snapshot) == checksum
    assert a["summary"]["counts"] == {"pass": 26, "fail": 4, "unknown": 1, "na": 17, "disabled": 0}
