"""Pure configuration evaluation without probes, commands, or network access."""
from collections import Counter
from copy import deepcopy
from .catalog import CONFIG_FIELDS, default_rules
from .database import digest

STATUS_LABELS = {
    "pass": "通过", "fail": "不通过",
    "unknown": "待补证", "na": "不适用", "disabled": "未启用",
}


def build_rules(overrides: list[dict]) -> list[dict]:
    rules = default_rules()
    lookup = {item["id"]: item for item in rules}
    for override in overrides:
        current = lookup[override["id"]]
        current["enabled"] = override["enabled"]
        expected = override.get("expected")
        if expected is not None:
            kind = CONFIG_FIELDS[current["field"]][1]
            if kind == "bool" and type(expected) is not bool:
                raise ValueError("布尔规则阈值必须为true或false")
            if kind == "int":
                if type(expected) is not int or not 1 <= expected <= 36500:
                    raise ValueError("数值规则阈值必须为1至36500的整数")
            current["expected"] = expected
    if not any(item["enabled"] for item in rules):
        raise ValueError("至少需要启用一条规则")
    return rules


def evaluate_rule(asset: dict, rule: dict) -> dict:
    field = rule["field"]
    present = field in asset["config"]
    observed = asset["config"].get(field)
    status = "unknown"
    reason = "缺少有效配置证据，不按通过处理"
    if not rule["enabled"]:
        status, reason = "disabled", "当前基线未启用该项"
    elif asset["role"] not in rule["roles"]:
        status, reason = "na", "资产角色不在规则适用范围"
    elif present and observed is not None:
        kind = CONFIG_FIELDS[field][1]
        type_ok = (kind == "bool" and type(observed) is bool) or (
            kind == "int" and type(observed) is int and 0 <= observed <= 36500
        )
        if type_ok:
            expected = rule["expected"]
            if rule["operator"] == "eq":
                valid = observed == expected
            elif rule["operator"] == "ge":
                valid = observed >= expected
            elif rule["operator"] == "le":
                valid = observed <= expected
            else:
                raise ValueError("未知规则操作符")
            if field == "idle_minutes" and observed == 0:
                valid = False
            status = "pass" if valid else "fail"
            reason = "满足当前自定义基线" if valid else "不满足当前自定义基线"
        else:
            reason = "配置值类型或范围无效，需补充证据"
    return {
        "asset_id": asset["id"], "asset_key": asset["asset_key"],
        "asset_name": asset["name"], "asset_revision": asset["revision"],
        "role": asset["role"], "rule_id": rule["id"],
        "rule_name": rule["name"], "rule_hash": digest(rule),
        "field": field, "operator": rule["operator"],
        "expected": rule["expected"], "observed": observed,
        "present": present, "status": status, "reason": reason,
        "severity": rule["severity"], "weight": rule["weight"],
        "guidance": rule["guidance"],
    }


def summarize(findings: list[dict]) -> dict:
    counts = Counter(item["status"] for item in findings)
    active = counts["pass"] + counts["fail"] + counts["unknown"]
    known = counts["pass"] + counts["fail"]
    possible = sum(item["weight"] for item in findings
                   if item["status"] in {"pass", "fail", "unknown"})
    earned = sum(item["weight"] for item in findings if item["status"] == "pass")
    return {
        "counts": {status: counts[status] for status in STATUS_LABELS},
        "applicable": active, "known": known,
        "coverage_percent": round(100 * known / active, 2) if active else None,
        "pass_percent_known": round(100 * counts["pass"] / known, 2) if known else None,
        "evidence_score": round(100 * earned / possible, 2) if possible else None,
        "high_failures": sum(item["status"] == "fail" and item["severity"] == "high"
                             for item in findings),
    }


def evaluate(snapshot: dict) -> dict:
    assets = snapshot["assets"]
    rules = snapshot["baseline"]["rules"]
    findings = [evaluate_rule(asset, rule) for asset in assets for rule in rules]
    return {
        "engine": "deskguard-rules-v1", "scope": "用户填报配置证据",
        "summary": summarize(findings), "findings": findings,
        "by_asset": [
            {"asset_id": asset["id"], "asset_key": asset["asset_key"],
             **summarize([item for item in findings if item["asset_id"] == asset["id"]])}
            for asset in assets
        ],
        "limits": ["不连接目标设备", "不证明实际措施已实施", "不替代安全认证"],
    }


def compare_results(before: dict, after: dict) -> dict:
    a, b = before["snapshot"], after["snapshot"]
    if before["id"] == after["id"]:
        raise ValueError("请选择两个不同任务")
    if a["space_id"] != b["space_id"]:
        raise ValueError("不同管理域不能比较")
    if a["baseline"]["content_hash"] != b["baseline"]["content_hash"]:
        raise ValueError("规则阈值或启用状态不同，不能直接比较")
    if b["space_revision"] < a["space_revision"]:
        raise ValueError("复测配置版本不能早于原始版本")
    old = {(item["asset_id"], item["rule_id"]): item
           for item in before["result"]["findings"]}
    new = {(item["asset_id"], item["rule_id"]): item
           for item in after["result"]["findings"]}
    transitions = []
    for key in sorted(old.keys() & new.keys()):
        left, right = old[key], new[key]
        if left["role"] != right["role"]:
            raise ValueError("资产角色变化，请拆分比较，不作为整改通过")
        if left["status"] != right["status"]:
            transitions.append({
                "asset_key": right["asset_key"], "rule_id": right["rule_id"],
                "rule_name": right["rule_name"], "before": left["status"],
                "after": right["status"], "before_value": left["observed"],
                "after_value": right["observed"],
            })
    return {
        "before": before["id"], "after": after["id"],
        "common_checks": len(old.keys() & new.keys()),
        "added_checks": len(new.keys() - old.keys()),
        "removed_checks": len(old.keys() - new.keys()),
        "transitions": transitions,
        "fixed": sum(item["before"] == "fail" and item["after"] == "pass"
                     for item in transitions),
        "regressed": sum(item["after"] == "fail" and item["before"] != "fail"
                         for item in transitions),
    }
