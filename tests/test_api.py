from pathlib import Path
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from conftest import run_job, asset_update_payload, TEST_PASSWORD
from deskguard.importing import demo_assets
from deskguard.database import digest


def test_auth_host_and_csrf(app):
    with TestClient(app) as anon:
        assert anon.get("/api/assets?space_id=x").status_code == 401
        assert anon.get("/health").status_code == 200
        assert anon.get("/health", headers={"host": "attacker.invalid"}).status_code == 400
        login = anon.post("/api/login", json={"username": "admin", "password": TEST_PASSWORD})
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        assert anon.post("/api/spaces", json={"name": "拒绝写入"}).status_code == 403
        anon.headers["X-CSRF-Token"] = login.json()["csrf"]
        assert anon.post("/api/spaces", json={"name": "拒绝跨站"}, headers={"Origin": "https://evil.invalid"}).status_code == 403
        assert anon.get("/").status_code == 200
        assert "frame-ancestors 'none'" in anon.get("/").headers["content-security-policy"]


def test_login_lock_and_reinitialization(app):
    with pytest.raises(ValueError):
        app.state.auth.setup("other", "DifferentPassword123")
    with TestClient(app) as client:
        for _ in range(5):
            assert client.post("/api/login", json={"username": "admin", "password": "bad"}).status_code == 401
        assert client.post("/api/login", json={"username": "admin", "password": TEST_PASSWORD}).status_code == 429


def test_password_revokes_sessions(client, app):
    second_token, _ = app.state.auth.login("admin", TEST_PASSWORD)
    assert client.post("/api/password", json={"old_password": "wrong", "new_password": "NewLocalPassword2026"}).status_code == 403
    assert client.post("/api/password", json={"old_password": TEST_PASSWORD, "new_password": "NewLocalPassword2026"}).status_code == 200
    assert client.get("/api/me").status_code == 401
    from deskguard.security import AccessError
    with pytest.raises(AccessError):
        app.state.auth.authenticate(second_token)


def test_import_atomic_on_duplicate(client, seeded):
    space, assets, baseline = seeded
    rows = demo_assets()
    rows[0]["asset_key"] = "NEW-01"
    response = client.post("/api/assets-import", json={"space_id": space["id"], "format": "json", "content": json.dumps(rows)})
    assert response.status_code == 409
    current = client.get("/api/assets", params={"space_id": space["id"]}).json()
    assert len(current) == 4
    assert not any(row["asset_key"] == "NEW-01" for row in current)


def test_import_atomic_invalid_final_row(client, seeded):
    space, assets, baseline = seeded
    rows = demo_assets()
    rows[0]["asset_key"] = "NEW-01"
    rows[-1]["config"]["tls"] = "yes"
    assert client.post("/api/assets-import", json={"space_id": space["id"], "format": "json", "content": json.dumps(rows)}).status_code == 422
    assert len(client.get("/api/assets", params={"space_id": space["id"]}).json()) == 4


def test_revisions_and_stale_update(client, seeded):
    space, assets, baseline = seeded
    target = assets[0]
    changed = asset_update_payload(target, target["config"] | {"mfa": True})
    response = client.put(f"/api/assets/{target['id']}", json=changed)
    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert client.put(f"/api/assets/{target['id']}", json=changed).status_code == 409
    revisions = client.get(f"/api/assets/{target['id']}/revisions").json()
    assert [row["revision"] for row in revisions] == [1, 2]
    assert all(row["valid"] for row in revisions)
    assert revisions[0]["payload"]["config"]["mfa"] is False
    assert revisions[1]["payload"]["config"]["mfa"] is True


def test_asset_stable_role(client, seeded):
    target = seeded[1][0]
    payload = asset_update_payload(target, target["config"])
    payload["role"] = "terminal"
    assert client.put(f"/api/assets/{target['id']}", json=payload).status_code == 422


def test_snapshot_frozen_before_execute(client, seeded):
    space, assets, baseline = seeded
    job = client.post("/api/jobs", json={"name": "冻结输入", "space_id": space["id"], "baseline_id": baseline["id"]}).json()
    target = assets[0]
    assert client.put(f"/api/assets/{target['id']}", json=asset_update_payload(target, target["config"] | {"mfa": True})).status_code == 200
    result = client.post(f"/api/jobs/{job['id']}/execute").json()
    assert result["snapshot_hash"] == job["snapshot_hash"]
    finding = next(x for x in result["result"]["findings"] if x["asset_id"] == target["id"] and x["rule_id"] == "DG-01")
    assert finding["status"] == "fail"
    assert client.post(f"/api/jobs/{job['id']}/execute").status_code == 409


def test_corrupt_snapshot_explicit_failure(client, seeded, app):
    space, assets, baseline = seeded
    job = client.post("/api/jobs", json={"name": "摘要反例", "space_id": space["id"], "baseline_id": baseline["id"]}).json()
    with app.state.database.transaction() as conn:
        conn.execute("UPDATE jobs SET snapshot_hash=? WHERE id=?", ("0" * 64, job["id"]))
    result = client.post(f"/api/jobs/{job['id']}/execute").json()
    assert result["status"] == "failed"
    assert result["result"] is None
    assert "摘要" in result["error"]


def test_result_corruption_blocks_evidence(client, seeded, app):
    job = run_job(client, seeded)
    with app.state.database.transaction() as conn:
        conn.execute("UPDATE jobs SET result_hash=? WHERE id=?", ("0" * 64, job["id"]))
    assert client.get(f"/api/jobs/{job['id']}/export/json").status_code == 409


def test_archived_domain_stops_mutation(client, seeded):
    space, assets, baseline = seeded
    assert client.patch(f"/api/spaces/{space['id']}", json={"status": "archived"}).status_code == 200
    assert client.post("/api/demo-assets", json={"space_id": space["id"]}).status_code == 409
    assert client.post("/api/jobs", json={"name": "归档拒绝", "space_id": space["id"], "baseline_id": baseline["id"]}).status_code == 409
    assert client.get("/api/assets", params={"space_id": space["id"]}).status_code == 200
    assert client.patch(f"/api/spaces/{space['id']}", json={"status": "active"}).status_code == 200


def test_ticket_full_closure_requires_real_new_version(client, seeded):
    old = run_job(client, seeded, "原始任务")
    target = seeded[1][0]
    ticket = client.post("/api/tickets", json={
        "job_id": old["id"], "asset_id": target["id"], "rule_id": "DG-01",
        "owner": "测试运维组", "due_date": "2099-12-31", "note": "需要启用认证",
    })
    assert ticket.status_code == 201
    key = ticket.json()["id"]
    assert client.post(f"/api/tickets/{key}/transition", json={"status": "resolved", "note": "不允许跳过处理"}).status_code == 409
    assert client.post(f"/api/tickets/{key}/transition", json={"status": "in_progress", "note": "开始核对目标配置"}).status_code == 200
    assert client.post(f"/api/tickets/{key}/transition", json={"status": "resolved", "note": "配置已经人工调整"}).status_code == 200
    assert client.post(f"/api/tickets/{key}/verify", json={"job_id": old["id"], "note": "旧任务不能充当复测"}).status_code == 422
    still_bad = run_job(client, seeded, "未更新的复测")
    assert client.post(f"/api/tickets/{key}/verify", json={"job_id": still_bad["id"], "note": "不通过任务不能关闭"}).status_code == 409
    new_asset = client.put(f"/api/assets/{target['id']}", json=asset_update_payload(target, target["config"] | {"mfa": True})).json()
    assert new_asset["revision"] == 2
    new = run_job(client, seeded, "更新后的复测")
    result = client.post(f"/api/tickets/{key}/verify", json={"job_id": new["id"], "note": "同规则新配置复测通过"})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "closed"
    assert result.json()["verify_job_id"] == new["id"]
    assert len(result.json()["events"]) == 4
    comparison = client.post("/api/compare", json={"before": old["id"], "after": new["id"]}).json()
    assert comparison["fixed"] == 1
    assert client.get(f"/api/jobs/{old['id']}").json()["result"]["summary"]["counts"]["fail"] == 4


def test_changed_threshold_cannot_count_as_fix(client, seeded):
    old = run_job(client, seeded)
    changed = client.post("/api/baselines", json={"name": "降低阈值", "overrides": [{"id": "DG-04", "expected": 1}]}).json()
    new = run_job(client, seeded, "新阈值任务", changed["id"])
    assert client.post("/api/compare", json={"before": old["id"], "after": new["id"]}).status_code == 422


def test_cannot_open_ticket_for_unknown(client, seeded):
    job = run_job(client, seeded)
    finding = next(x for x in job["result"]["findings"] if x["status"] == "unknown")
    assert client.post("/api/tickets", json={"job_id": job["id"], "asset_id": finding["asset_id"], "rule_id": finding["rule_id"], "owner": "test", "due_date": "2099-01-01"}).status_code == 422


def test_exports_audit_backup_and_html_escaping(client, seeded, app):
    job = run_job(client, seeded, '<script>alert("x")</script>')
    html = client.get(f"/api/jobs/{job['id']}/export/html")
    assert html.status_code == 200
    assert '<script>alert' not in html.text
    assert '&lt;script&gt;' in html.text
    assert client.get(f"/api/jobs/{job['id']}/export/json").json()["result_valid"]
    assert "资产编号" in client.get(f"/api/jobs/{job['id']}/export/csv").text
    assert client.get("/api/audit-verify").json()["valid"]
    backup = client.post("/api/backup").json()
    path = app.state.settings.backups / backup["filename"]
    assert path.is_file()
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    assert TEST_PASSWORD not in client.get("/api/audit-export").text


def test_audit_corruption_detected(client, app):
    with app.state.database.transaction() as conn:
        conn.execute("UPDATE audit SET action='tampered' WHERE seq=1")
    assert client.get("/api/audit-verify").json()["valid"] is False


def test_too_large_body_and_unknown_fields(client):
    assert client.post("/api/spaces", content=b"x" * 2_000_001, headers={"content-type": "application/json"}).status_code == 413
    assert client.post("/api/spaces", json={"name": "test", "admin": True}).status_code == 422


def test_zero_assets_not_a_pass(client):
    space = client.post("/api/spaces", json={"name": "空管理域"}).json()
    baseline = client.post("/api/baselines", json={"name": "空域基线"}).json()
    assert client.post("/api/jobs", json={"name": "空集拒绝", "space_id": space["id"], "baseline_id": baseline["id"]}).status_code == 422


def test_limit_200_and_template_roundtrip(client):
    space = client.post("/api/spaces", json={"name": "容量测试域"}).json()
    rows = [dict(demo_assets()[0], asset_key=f"A{i:03}") for i in range(200)]
    result = client.post("/api/assets-import", json={"space_id": space["id"], "format": "json", "content": json.dumps(rows)})
    assert result.status_code == 201
    row = dict(demo_assets()[0], space_id=space["id"], asset_key="overflow")
    assert client.post("/api/assets", json=row).status_code == 422
    exported = client.get(f"/api/spaces/{space['id']}/export/csv").text
    from deskguard.importing import parse_import
    assert len(parse_import("csv", exported)) == 200
