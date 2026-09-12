"""Independent second-pass regression cases; all fixtures are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
import json
import random
import pytest
from deskguard.catalog import CONFIG_FIELDS, default_rules
from deskguard.checking import evaluate_rule, summarize
from deskguard.importing import COLUMNS, assets_csv, demo_assets, parse_import
from deskguard.security import AccessError
from conftest import TEST_PASSWORD, asset_update_payload, run_job


def ready_ticket(client, seeded):
    old = run_job(client, seeded, "初始失败证据")
    target = seeded[1][0]
    response = client.post("/api/tickets", json={
        "job_id": old["id"], "asset_id": target["id"], "rule_id": "DG-01",
        "owner": "复核运维组", "due_date": "2099-12-31", "note": "整改验证回归测试",
    })
    assert response.status_code == 201
    key = response.json()["id"]
    for status in ("in_progress", "resolved"):
        result = client.post(f"/api/tickets/{key}/transition", json={
            "status": status, "note": "根据合成配置核对进度",
        })
        assert result.status_code == 200
    return key, target


def test_obsolete_passing_evidence_cannot_close_regressed_asset(client, seeded):
    key, target = ready_ticket(client, seeded)
    updated = client.put(f"/api/assets/{target['id']}", json=asset_update_payload(
        target, target["config"] | {"mfa": True})).json()
    passed = run_job(client, seeded, "中间版本通过证据")
    latest = client.put(f"/api/assets/{target['id']}", json=asset_update_payload(
        updated, updated["config"] | {"mfa": False})).json()
    assert latest["revision"] == 3
    response = client.post(f"/api/tickets/{key}/verify", json={
        "job_id": passed["id"], "note": "过时通过快照不得关闭当前失败",
    })
    assert response.status_code == 409, response.text
    assert client.get(f"/api/tickets/{key}").json()["status"] == "resolved"


def test_unrelated_asset_update_does_not_invalidate_target_evidence(client, seeded):
    key, target = ready_ticket(client, seeded)
    client.put(f"/api/assets/{target['id']}", json=asset_update_payload(
        target, target["config"] | {"mfa": True}))
    passed = run_job(client, seeded, "当前目标配置证据")
    other = seeded[1][1]
    client.put(f"/api/assets/{other['id']}", json=asset_update_payload(
        other, other["config"] | {"log_days": 200}))
    response = client.post(f"/api/tickets/{key}/verify", json={
        "job_id": passed["id"], "note": "只核对工单对应资产当前版本",
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"


@pytest.mark.parametrize("body", [
    '{"name":"first","name":"second"}',
    '{"name":"first","name":"first"}',
    '{"name":"valid","description":"\\ud800"}',
    '{"name":"valid","description":"\\udfff"}',
])
def test_ambiguous_or_non_scalar_json_rejected_without_write(client, body):
    before = client.get("/api/spaces").json()
    response = client.post("/api/spaces", content=body,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert client.get("/api/spaces").json() == before


def test_duplicate_nested_config_keys_rejected(client, seeded):
    target = dict(demo_assets()[0], space_id=seeded[0]["id"], asset_key="DUP-JSON")
    text = json.dumps(target).replace('"mfa": false', '"mfa": false, "mfa": true')
    response = client.post("/api/assets", content=text,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("body", [
    '{"name":"valid","description":NaN}',
    '{"name":"valid","description":Infinity}',
    '{"name":"valid","description":-Infinity}',
    '{"name":"broken"',
    b'{"name":"broken\xff"}',
])
def test_invalid_json_is_explicit_client_error(client, body):
    response = client.post("/api/spaces", content=body,
                           headers={"Content-Type": "application/json"})
    assert response.status_code in (400, 422)


def test_deep_json_rejected_without_server_error(client):
    text = '{"name":"test","description":' + '[' * 1200 + '0' + ']' * 1200 + '}'
    response = client.post("/api/spaces", content=text,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_valid_surrogate_pair_and_vendor_json_accepted(client):
    response = client.post("/api/spaces", content='{"name":"valid\\ud83d\\udd12"}',
                           headers={"Content-Type": "application/vnd.deskguard+json"})
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "valid🔒"


@pytest.mark.parametrize("cell", ['"unfinished', '"valid"garbage'])
def test_malformed_csv_quotes_rejected(client, seeded, cell):
    text = ','.join(COLUMNS) + '\nNEW-CSV,' + cell + ',host,owner,zone' + ',' * 12 + '\n'
    response = client.post("/api/assets-import", json={
        "space_id": seeded[0]["id"], "format": "csv", "content": text,
    })
    assert response.status_code == 422, response.text
    assert len(client.get("/api/assets", params={"space_id": seeded[0]["id"]}).json()) == 4


def test_oversized_csv_field_is_validation_error(client, seeded):
    text = ','.join(COLUMNS) + '\nNEW-CSV,' + 'X' * 150000 + ',host,owner,zone' + ',' * 12 + '\n'
    response = client.post("/api/assets-import", json={
        "space_id": seeded[0]["id"], "format": "csv", "content": text,
    })
    assert response.status_code == 422, response.text
    assert len(client.get("/api/assets", params={"space_id": seeded[0]["id"]}).json()) == 4


def test_concurrent_password_change_has_one_winner(app, monkeypatch):
    """Synchronize after both read the old hash, without bypassing authentication."""
    import deskguard.security as security
    auth = app.state.auth
    _, identity = auth.login("admin", TEST_PASSWORD)
    barrier = Barrier(2, timeout=10)
    original = security.hash_password
    def synchronized_hash(value):
        result = original(value)
        barrier.wait()
        return result
    monkeypatch.setattr(security, "hash_password", synchronized_hash)
    def change(new):
        try:
            auth.change_password(identity, TEST_PASSWORD, new)
            return "success", new
        except AccessError:
            return "conflict", new
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, ["First-New-Password2026!", "Second-New-Password2026!"]))
    assert sorted(status for status, _ in results) == ["conflict", "success"], results
    winner = next(p for status, p in results if status == "success")
    with pytest.raises(AccessError):
        auth.authenticate(auth.login("admin", winner)[0] + "invalid")
    assert len(app.state.database.all("SELECT * FROM audit WHERE action='auth.password'")) == 1


@pytest.mark.parametrize("seed", range(12))
def test_rule_and_csv_property_roundtrips(seed):
    rng = random.Random(seed)
    records = []
    for index, record in enumerate(demo_assets()):
        row = deepcopy(record)
        row["asset_key"] = f"A{seed}-{index}"
        row["name"] = '合成,带引号"资产'
        config = {}
        for field, (_, kind) in CONFIG_FIELDS.items():
            mode = rng.randrange(4)
            if mode == 0:
                continue
            config[field] = None if mode == 1 else (
                bool(rng.randrange(2)) if kind == "bool" else rng.choice([0, 1, 15, 180, 36500]))
        row["config"] = config
        records.append(row)
    assert parse_import("csv", assets_csv(records)) == records
    assert parse_import("json", json.dumps(records)) == records
    findings = []
    for record in records:
        asset = record | {"id": record["asset_key"], "revision": 1}
        for rule in default_rules():
            actual = evaluate_rule(asset, rule)
            value = record["config"].get(rule["field"])
            if record["role"] not in rule["roles"]:
                expected = "na"
            elif value is None:
                expected = "unknown"
            else:
                okay = {"eq": lambda: value == rule["expected"],
                        "ge": lambda: value >= rule["expected"],
                        "le": lambda: value <= rule["expected"]}[rule["operator"]]()
                if rule["field"] == "idle_minutes" and value == 0:
                    okay = False
                expected = "pass" if okay else "fail"
            assert actual["status"] == expected
            findings.append(actual)
    result = summarize(findings)
    assert sum(result["counts"].values()) == 48
    assert result["known"] + result["counts"]["unknown"] == result["applicable"]
    for key in ["coverage_percent", "pass_percent_known", "evidence_score"]:
        assert result[key] is None or 0 <= result[key] <= 100


@pytest.mark.parametrize("origin", ["http://[invalid", "null", "https://untrusted.example",
                                   "http://testserver/invalid"])
def test_malformed_or_foreign_origin_denied(client, origin):
    response = client.post("/api/spaces", json={"name": "不得创建"}, headers={"Origin": origin})
    assert response.status_code == 403


def test_non_ascii_csrf_is_denied_not_server_error(client):
    response = client.post("/api/spaces", json={"name": "不得创建"},
                           headers={b"X-CSRF-Token": b"invalid\xff"})
    assert response.status_code == 403


def test_duplicate_json_without_content_type_is_rejected(client):
    response = client.post("/api/spaces", content='{"name":"first","name":"second"}')
    assert response.status_code == 422
