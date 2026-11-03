"""回归已登录账号的地区库存、按需报价和收藏持久化。"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from catchgpu.app import create_app
from catchgpu.config import Settings
from catchgpu.models import Task
from catchgpu.providers.autodl import AutoDLProvider, GIB
from catchgpu.providers.base import AuthenticationError, ProviderError
from catchgpu.providers.demo import DemoProvider
from catchgpu.storage import Store


def regions():
    return [
        {
            "region_sign": "west-B",
            "region_name": "西北B区",
            "data_center": "westDC3",
            "visible": "all",
        },
        {
            "region_sign": "west-C",
            "region_name": "西北B区",
            "data_center": "westDC3",
            "visible": "all",
        },
        {
            "region_sign": "bj-B1",
            "region_name": "北京B区",
            "data_center": "beijingDC2",
            "visible": "all",
        },
    ]


class MarketConnection:
    def __init__(self, connected=True):
        self.browser = SimpleNamespace(
            connected=connected, request=AsyncMock(side_effect=self.request)
        )
        self.calls = []
        self.fail_price = False

    async def public(self, method, path, payload=None):
        if path.endswith("region/list"):
            return regions()
        if path.endswith("gpu_type"):
            return [
                {"gpu_name": "RTX 4090", "gpu_memory": 24 * GIB},
                {"gpu_name": "A100", "gpu_memory": 80 * GIB},
            ]
        raise AssertionError(path)

    async def request(self, method, path, payload):
        self.calls.append((path, payload))
        if path.endswith("region/gpu_type"):
            assert payload["region_sign_list"], "必须传入具体地区"
            return [{"RTX 4090": {"idle_gpu_num": 5, "total_gpu_num": 24}}]
        if path.endswith("machine/list"):
            return {
                "list": [
                    {
                        "machine_id": "machine-1",
                        "region_name": "西北B区",
                        "gpu_memory": 24 * GIB,
                        "cpu_per_gpu": 12,
                        "mem_per_gpu": 64 * GIB,
                        "gpu_idle_num": 4,
                        "gpu_number": 8,
                        "root_password": "must-not-leak",
                        "jupyter_token": "must-not-leak",
                        "machine_sku_info": [{"type": "payg", "current_price": 5000}],
                    }
                ],
                "result_total": 16,
            }
        if path.endswith("price/preview"):
            if self.fail_price:
                raise ProviderError("模拟报价暂不可用")
            return {
                "discount_list": [
                    {"product_type": "instance", "origin_price": 2400, "total_reduction": 400}
                ]
            }
        raise AssertionError(path)


async def test_connected_catalog_groups_regions_and_passes_physical_ids():
    connection = MarketConnection()
    catalog = await AutoDLProvider(connection).catalog("westDC3")
    assert catalog.authenticated
    assert len(catalog.regions) == 3
    assert len(catalog.region_groups) == 2
    assert catalog.region_groups[0].region_ids == ["west-B", "west-C"]
    assert connection.calls[0][1]["region_sign_list"] == ["west-B", "west-C"]
    assert catalog.gpus[0].available == 5
    assert catalog.gpus[0].total == 24
    assert not any("需先手动登录" in notice for notice in catalog.notices)


async def test_catalog_changes_region_instead_of_using_global_empty_filter():
    connection = MarketConnection()
    catalog = await AutoDLProvider(connection).catalog("beijingDC2")
    assert catalog.active_region == "beijingDC2"
    assert connection.calls[0][1]["region_sign_list"] == ["bj-B1"]


async def test_authenticated_resource_details_use_account_quote_and_pagination():
    connection = MarketConnection()
    result = await AutoDLProvider(connection).resources("RTX 4090", "westDC3", page=2, page_size=5)
    query = connection.calls[0][1]
    assert query["page_index"] == 2 and query["page_size"] == 5
    assert query["gpu_type_name"] == ["RTX 4090"]
    assert query["region_sign_list"] == ["west-B", "west-C"]
    assert result.total == 16
    assert result.offers[0].price_hour == Decimal("2")
    assert result.offers[0].price_source == "account"
    assert result.offers[0].cpu == "12 核 / 卡"
    assert "must-not-leak" not in result.model_dump_json()


async def test_public_fallback_is_labelled_and_not_reported_as_login_failure():
    connection = MarketConnection()
    connection.fail_price = True
    result = await AutoDLProvider(connection).resources("RTX 4090")
    assert result.authenticated
    assert result.offers[0].price_hour == Decimal("5")
    assert result.offers[0].price_source == "public"


async def test_no_login_does_not_fetch_private_resource_details():
    connection = MarketConnection(connected=False)
    with pytest.raises(AuthenticationError):
        await AutoDLProvider(connection).resources("RTX 4090")
    assert not connection.calls


async def test_expired_stock_session_is_not_hidden_by_catalog():
    connection = MarketConnection()
    connection.browser.request = AsyncMock(side_effect=AuthenticationError("会话失效"))
    with pytest.raises(AuthenticationError):
        await AutoDLProvider(connection).catalog()


def test_favorites_persist_are_idempotent_and_separate_providers(tmp_path):
    path = tmp_path / "favorites.db"
    store = Store(path)
    store.save_favorite("autodl", "same", "RTX 4090")
    store.save_favorite("autodl", "same", "RTX 4090")
    store.save_favorite("autodl-pro", "same", "Pro")
    assert len(store.favorites()) == 2
    store.close()
    store = Store(path)
    assert len(store.favorites()) == 2
    store.remove_favorite("autodl", "same")
    assert store.favorites()[0]["provider"] == "autodl-pro"
    store.close()


def test_favorites_do_not_change_existing_task_data(store, rule):
    task = Task(provider="demo", name="保留任务", rules=[rule])
    store.save(task)
    store.save_favorite("demo", "demo-4090", "RTX 4090")
    store.remove_favorite("demo", "demo-4090")
    assert store.get(task.id).name == "保留任务"
    assert store.get(task.id).rules[0] == rule


def test_catalog_cache_is_scoped_to_region_and_login_revision(tmp_path):
    class CountingDemo(DemoProvider):
        calls = 0

        async def catalog(self, region=None):
            self.calls += 1
            return await super().catalog(region)

    provider = CountingDemo()
    app = create_app(Settings(data_dir=tmp_path), {"demo": provider})
    with TestClient(app, base_url="http://localhost") as client:
        assert (
            client.get("/api/catalog/demo?region=demo-east").json()["active_region"] == "demo-east"
        )
        assert (
            client.get("/api/catalog/demo?region=demo-north").json()["active_region"]
            == "demo-north"
        )
        client.get("/api/catalog/demo?region=demo-east")
        assert provider.calls == 2
        app.state.connection.browser.revision += 1
        client.get("/api/catalog/demo?region=demo-east")
        assert provider.calls == 3
        result = client.get("/api/resources/demo?gpu_id=demo-4090&region=demo-east")
        assert result.status_code == 200 and len(result.json()["offers"]) == 3
        saved = client.put(
            "/api/favorites", json={"provider": "demo", "gpu_id": "demo-4090", "name": "RTX 4090"}
        )
        assert len(saved.json()) == 1
        assert len(client.get("/api/state").json()["favorites"]) == 1
        assert client.delete("/api/favorites/demo?gpu_id=demo-4090").json() == []


def test_favorite_unknown_provider_rejected(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), {"demo": DemoProvider()})
    with TestClient(app, base_url="http://localhost") as client:
        response = client.put(
            "/api/favorites", json={"provider": "unknown", "gpu_id": "x", "name": "x"}
        )
        assert response.status_code == 422
