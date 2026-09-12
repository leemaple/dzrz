from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from deskguard.api import create_app
from deskguard.config import Settings

TEST_PASSWORD = "LocalTestOnly-2026!"


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(tmp_path))
    app.state.auth.setup("admin", TEST_PASSWORD)
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        response = client.post("/api/login", json={"username": "admin", "password": TEST_PASSWORD})
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        yield client


@pytest.fixture
def seeded(client):
    space = client.post("/api/spaces", json={"name": "合成测试域", "description": "仅自动化测试"}).json()
    assets = client.post("/api/demo-assets", json={"space_id": space["id"]}).json()
    baseline = client.post("/api/baselines", json={"name": "固定测试基线"}).json()
    return space, assets, baseline


def run_job(client, seeded, name="测试核查", baseline_id=None):
    space, assets, baseline = seeded
    created = client.post("/api/jobs", json={
        "name": name, "space_id": space["id"], "baseline_id": baseline_id or baseline["id"],
    })
    assert created.status_code == 201, created.text
    result = client.post(f"/api/jobs/{created.json()['id']}/execute")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed", result.text
    return result.json()


def asset_update_payload(asset, config):
    return {key: asset[key] for key in ("asset_key", "name", "role", "owner", "zone")} | {
        "config": config, "expected_revision": asset["revision"],
    }
