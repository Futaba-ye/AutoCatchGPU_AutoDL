"""验证 HTTP 边界、配置持久化和完整演示任务流程。"""

import time

import pytest
from fastapi.testclient import TestClient

from catchgpu.app import create_app
from catchgpu.config import Settings
from catchgpu.locking import WorkspaceLock
from catchgpu.providers.demo import DemoProvider


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), {"demo": DemoProvider()})
    with TestClient(app, base_url="http://localhost") as result:
        result.put("/api/notifications", json={"desktop": False, "speech": False})
        yield result


def test_end_to_end_demo_task(client, rule):
    assert client.get("/").status_code == 200
    assert client.get("/api/catalog/demo").json()["demo"] is True
    created = client.post(
        "/api/tasks",
        json={"name": "完整演示", "provider": "demo", "rules": [rule.model_dump(mode="json")]},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert client.post(f"/api/tasks/{task_id}/start").status_code == 200
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        task = client.get("/api/state").json()["tasks"][0]
        if task["status"] == "completed":
            break
        time.sleep(0.01)
    assert task["status"] == "completed"
    assert len(task["instances"]) == 1
    assert task["instances"][0]["id"].startswith("demo-")
    assert any(event["kind"] == "instance_created" for event in client.get("/api/events").json())


def test_cross_origin_mutation_rejected(client):
    result = client.put(
        "/api/connection/token",
        headers={"Origin": "https://unrelated.example"},
        json={"token": "dummy"},
    )
    assert result.status_code == 403


def test_validation_never_echoes_secret(client):
    secret = "sensitive-value-" * 400
    result = client.put("/api/connection/token", json={"token": secret})
    assert result.status_code == 422
    assert "sensitive-value" not in result.text


def test_connection_state_does_not_return_token(client):
    client.put("/api/connection/token", json={"token": "only-test-secret"})
    result = client.get("/api/state")
    assert result.json()["connection"]["api"]
    assert "only-test-secret" not in result.text


def test_server_access_token_required(tmp_path):
    settings = Settings(data_dir=tmp_path, host="0.0.0.0", access_token="workspace-secret")
    with TestClient(
        create_app(settings, {"demo": DemoProvider()}), base_url="http://localhost"
    ) as client:
        assert client.get("/api/state").status_code == 401
        assert (
            client.get(
                "/api/state", headers={"Authorization": "Bearer workspace-secret"}
            ).status_code
            == 200
        )


def test_server_cannot_bind_public_without_access_token(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(Settings(data_dir=tmp_path, host="0.0.0.0"))


def test_notification_settings_persist_after_restart(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert (
            client.put("/api/notifications", json={"desktop": False, "speech": True}).status_code
            == 200
        )
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.get("/api/state").json()["notifications"] == {
            "desktop": False,
            "speech": True,
        }


def test_single_process_lock(tmp_path):
    lock = WorkspaceLock(tmp_path)
    with pytest.raises(RuntimeError):
        WorkspaceLock(tmp_path)
    lock.close()
    replacement = WorkspaceLock(tmp_path)
    replacement.close()


def test_unknown_task_returns_404(client):
    assert client.post("/api/tasks/missing/start").status_code == 404


def test_event_cursor_does_not_skip_large_batches(store):
    for index in range(450):
        store.event(None, "info", f"日志 {index}")
    first = store.events(after=1)
    second = store.events(after=first[-1]["id"])
    assert first[0]["id"] == 2
    assert first[-1]["id"] + 1 == second[0]["id"]


def test_native_browser_launch_and_confirmation_are_separate(client, monkeypatch):
    browser = client.app.state.connection.browser
    opened, connected = [], []

    async def open_login(browser_id):
        opened.append(browser_id)
        return "Google Chrome"

    async def connect_login():
        connected.append(True)
        return "Google Chrome"

    monkeypatch.setattr(browser, "open_login", open_login)
    monkeypatch.setattr(browser, "connect_login", connect_login)
    result = client.post("/api/connection/login", json={"browser": "chrome"})
    assert result.status_code == 200
    assert opened == ["chrome"]
    assert not connected
    assert "登录完成，连接工具" in result.json()["detail"]
    assert client.post("/api/connection/browser/connect").status_code == 200
    assert connected == [True]


def test_native_browser_selector_rejects_unknown_values(client):
    assert client.post("/api/connection/login", json={"browser": "unknown"}).status_code == 422
