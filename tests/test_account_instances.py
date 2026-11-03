"""平台实例字段、官方 API 优先级、敏感信息隔离和旧库迁移。"""

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from catchgpu.app import create_app
from catchgpu.config import Settings
from catchgpu.models import AccountInstancePage, AccountInstance
from catchgpu.providers.account_instances import AutoDLInstances
from catchgpu.providers.base import ProviderError
from catchgpu.providers.demo import DemoProvider
from catchgpu.storage import Store


@pytest.mark.parametrize(
    "pro,token,prefix",
    [
        (False, "", "/api/v1/instance"),
        (False, "configured", "/api/v1/instance"),
        (True, "", "/api/v1/instance/pro"),
        (True, "configured", "/api/v1/dev/instance/pro"),
    ],
)
async def test_platform_list_and_power_payloads(pro, token, prefix):
    calls = []

    async def request(method, path, payload, **kwargs):
        calls.append((method, path, payload, kwargs))
        if path.endswith("/list") or path == prefix:
            return {
                "list": [
                    {
                        "uuid": "owned-instance",
                        "name": "训练环境",
                        "snapshot_gpu_alias_name": "RTX 3090",
                        "req_gpu_amount": 2,
                        "status": "shutdown",
                        "start_mode": "gpu",
                        "region_name": "西北B区",
                        "root_password": "SECRET-PASSWORD",
                        "jupyter_token": "SECRET-TOKEN",
                    }
                ],
                "result_total": 1,
            }
        return None

    conn = SimpleNamespace(token=token, api=request, browser=SimpleNamespace(request=request))
    adapter = AutoDLInstances(conn, pro=pro)
    page = await adapter.list_instances(2, 20)
    assert page.items[0].can_start and not page.items[0].can_stop
    assert "SECRET" not in page.model_dump_json() and "password" not in page.model_dump_json()
    await adapter.power_on("owned-instance")
    await adapter.power_off("owned-instance")
    assert calls[0][1] == prefix + ("/list" if pro and token else "")
    assert calls[0][2] == {"page_index": 2, "page_size": 20}
    assert calls[1][1] == prefix + "/power_on" and calls[1][3]["mutation"]
    assert calls[1][2] == {"instance_uuid": "owned-instance", **({"payload": "gpu"} if pro else {})}
    assert calls[2][1] == prefix + "/power_off"
    assert all("create" not in row[1] and "release" not in row[1] for row in calls)


async def test_selection_reads_later_pages_and_rejects_unowned_ids():
    class Pages(DemoProvider):
        async def list_instances(self, page=1, page_size=100):
            return AccountInstancePage(
                items=[AccountInstance(id="other" if page == 1 else "wanted", name="测试")],
                total=101,
                page=page,
                page_size=page_size,
            )

    provider = Pages()
    assert list(await provider.selected_instances(["wanted"])) == ["wanted"]
    with pytest.raises(ProviderError, match="不属于"):
        await provider.selected_instances(["unowned"])


def test_old_event_schema_is_migrated_without_losing_logs(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT,level TEXT NOT NULL,message TEXT NOT NULL,kind TEXT NOT NULL,created_at TEXT NOT NULL)"
    )
    db.execute("INSERT INTO events VALUES(1,NULL,'info','原有日志','log','2026-11-02')")
    db.commit()
    db.close()
    store = Store(path)
    assert store.events()[0]["message"] == "原有日志" and store.events()[0]["subject"] == ""
    store.event(None, "info", "RTX 3090：等待库存", subject="RTX 3090")
    store.close()
    reopened = Store(path)
    assert reopened.events()[-1]["subject"] == "RTX 3090"
    reopened.close()


def test_instance_api_validates_selection_and_exposes_start_task(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), {"demo": DemoProvider()})
    with TestClient(app, base_url="http://localhost") as client:
        client.put("/api/notifications", json={"desktop": False, "speech": False})
        page = client.get("/api/instances/demo").json()
        assert page["total"] == 2 and page["demo"]
        assert client.get("/api/instances/missing").status_code == 404
        assert client.get("/api/instances/demo?page_size=101").status_code == 422
        assert (
            client.post(
                "/api/instances/demo/start-monitor", json={"instance_ids": ["unowned"]}
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/instances/demo/start-monitor", json={"instance_ids": ["demo-existing-4090"]}
            ).status_code
            == 409
        )
        assert client.post("/api/instances/demo/stop", json={"instance_ids": []}).status_code == 422
        created = client.post(
            "/api/instances/demo/start-monitor",
            json={"instance_ids": ["demo-existing-3090"], "interval": 10},
        )
        assert created.status_code == 201
        task = created.json()
        assert task["action"] == "start" and task["rules"] == []
        assert task["start_targets"][0]["instance_id"] == "demo-existing-3090"
        assert (
            client.post(
                "/api/tasks",
                json={key: task[key] for key in ["name", "provider", "action", "start_targets"]},
            ).status_code
            == 422
        )
        stopped = client.post(
            "/api/instances/demo/stop", json={"instance_ids": ["demo-existing-4090"]}
        )
        assert stopped.json()["results"][0]["success"]


def test_pro_catalog_returns_region_groups_separately_from_gpu_specs():
    # 地区按钮使用 RegionGroup，不应被并发读取的 GPU 列表覆盖。
    import asyncio
    from catchgpu.providers.autodl import AutoDLProProvider

    class Connection:
        token = ""
        browser = SimpleNamespace(connected=False)

        async def public(self, method, path, payload=None):
            if path.endswith("gpu_type"):
                return [{"gpu_type": "RTX 4090", "gpu_idle_num": 1, "gpu_total_number": 8}]
            return [
                {
                    "uuid": "4090-p",
                    "id": 1,
                    "name": "4090-24G",
                    "gpu_spec_name": "通用型",
                    "gpu_spec_config_sku_info": [{"type": "payg", "current_price": 2080}],
                }
            ]

    result = asyncio.run(AutoDLProProvider(Connection()).catalog())
    assert result.region_groups[0].id == "westDC3" and result.gpus[0].id == "4090-p"
