"""用官方字段结构验证适配器，网络请求全部拦截。"""

import json
from decimal import Decimal

import httpx
import pytest

from catchgpu.models import Rule
from catchgpu.providers.autodl import AutoDLProProvider, AutoDLProvider, GIB
from catchgpu.providers.base import AuthenticationError, ProviderError, UncertainCreation
from catchgpu.providers.transport import AutoDLConnection, unpack


def spec():
    return {
        "id": 4,
        "uuid": "pro6000-p",
        "name": "PRO6000-96G",
        "gpu_spec_name": "性能型",
        "gpu_name": "RTX PRO 6000",
        "payg": True,
        "gpu_spec_config_sku_info": [{"type": "payg", "current_price": 7350}],
        "system_disk_price_info": {
            "stage_price": [
                {
                    "charge_type": "daily",
                    "stage_list": [
                        {
                            "staged_charge_type": "fixed",
                            "price": 100,
                            "stage_from": 0,
                            "stage_to": 30 * GIB,
                        },
                        {
                            "staged_charge_type": "payg",
                            "price": 12,
                            "stage_from": 30 * GIB,
                            "stage_to": -1,
                        },
                    ],
                }
            ]
        },
    }


async def test_pro_prefers_official_api_and_maps_create_fields(tmp_path, rule):
    requests = []
    rule.gpu_id, rule.gpu_name, rule.disk_gb = "pro6000-p", "PRO6000", 100
    rule.regions = ["westDC3"]
    rule.image_id = "base-image-l2t43iu6uk"

    def handler(request):
        body = json.loads(request.content or "null")
        requests.append((request.url.host, request.url.path, body))
        if request.url.path.endswith("/gpu_type"):
            assert body["data_center_list"] == ["westDC3"]
            data = [{"gpu_type": "RTX PRO 6000", "gpu_idle_num": 4, "gpu_total_number": 8}]
        elif request.url.path.endswith("/gpu_spec_config"):
            data = [spec()]
        elif request.url.path.endswith("/create"):
            assert request.url.host == "api.autodl.com"
            assert request.headers["Authorization"] == "test-token"
            assert body["expand_system_disk_by_gb"] == 100
            assert body["image_uuid"] == rule.image_id
            assert body["gpu_spec_uuid"] == "pro6000-p"
            assert body["instance_name"] == "cg-test"
            data = "pro-created"
        else:
            raise AssertionError(request.url)
        return httpx.Response(200, json={"code": "Success", "data": data})

    connection = AutoDLConnection(
        tmp_path, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    connection.token = "test-token"
    provider = AutoDLProProvider(connection)
    await provider.validate(rule)
    quote = await provider.quote(rule)
    assert quote.gpu_hour == Decimal("7.35")
    assert quote.disk_hour == Decimal("1.3") / 24
    result = await provider.create(rule, quote, "cg-test")
    assert result.id == "pro-created"
    assert len([request for request in requests if request[0] == "api.autodl.com"]) == 1
    await connection.close()


async def test_pro_price_cap_prevents_create_request(tmp_path, rule):
    rule.gpu_id, rule.max_price = "pro6000-p", Decimal("1")

    def handler(request):
        if request.url.path.endswith("/gpu_type"):
            data = [{"gpu_type": "RTX PRO 6000", "gpu_idle_num": 4}]
        elif request.url.path.endswith("/gpu_spec_config"):
            data = [spec()]
        else:
            raise AssertionError("不应发起创建请求")
        return httpx.Response(200, json={"code": "Success", "data": data})

    connection = AutoDLConnection(
        tmp_path, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    connection.token = "test"
    assert await AutoDLProProvider(connection).quote(rule) is None
    await connection.close()


async def test_standard_web_price_and_prepaid_order_failure(rule):
    calls = []
    rule.gpu_id, rule.gpu_count, rule.disk_gb, rule.billing = "RTX 4090", 2, 40, "day"
    rule.regions, rule.image_id = ["west-B"], "image-private"

    class Browser:
        connected = True

        async def request(self, method, path, body, **kwargs):
            calls.append((method, path, body))
            if path.endswith("machine/list"):
                assert body["gpu_idle_num"] == 2
                assert body["region_sign_list"] == ["west-B"]
                return {
                    "list": [
                        {
                            "machine_id": "machine-1",
                            "region_sign": "west-B",
                            "gpu_idle_num": 2,
                            "on_line": 1,
                        }
                    ]
                }
            if path.endswith("price/preview"):
                assert body["expand_data_disk"] == 40 * GIB
                return {
                    "discount_list": [
                        {"product_type": "instance", "origin_price": 240000, "total_reduction": 0},
                        {"product_type": "data_disk", "origin_price": 24000, "total_reduction": 0},
                    ]
                }
            if path.endswith("create/prepay"):
                assert body["instance_info"]["private_image_uuid"] == "image-private"
                assert body["instance_info"]["instance_name"] == "cg-web"
                return {"uuid": "order-id"}
            if path.endswith("order/pay"):
                assert method == "PUT"
                raise ProviderError("模拟支付中断")
            raise AssertionError(path)

    connection = type("Connection", (), {"browser": Browser()})()
    provider = AutoDLProvider(connection)
    quote = await provider.quote(rule)
    assert quote.gpu_hour == Decimal("5")
    with pytest.raises(UncertainCreation):
        await provider.create(rule, quote, "cg-web")


async def test_pro_prepaid_requires_browser_even_with_token(tmp_path, rule):
    connection = AutoDLConnection(tmp_path)
    connection.token = "test"
    rule.billing = "month"
    with pytest.raises(AuthenticationError):
        await AutoDLProProvider(connection).validate(rule)
    await connection.close()


def test_auth_error_does_not_echo_platform_message():
    with pytest.raises(AuthenticationError) as error:
        unpack({"code": "AuthorizeFailed", "msg": "secret-token"})
    assert "secret-token" not in str(error.value)


async def test_api_timeout_becomes_uncertain_creation(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout("敏感请求信息")

    connection = AutoDLConnection(
        tmp_path, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    connection.token = "test"
    with pytest.raises(UncertainCreation) as error:
        await connection.api("POST", "/api/v1/dev/instance/pro/create", {}, mutation=True)
    assert "敏感" not in str(error.value)
    await connection.close()
