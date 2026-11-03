"""回归从未连接到已连接后，选中型号与账号报价自动刷新的流程。"""

import asyncio
import json
import os

from playwright.async_api import async_playwright


async def main():
    url = os.getenv("CATCHGPU_TEST_URL", "http://127.0.0.1:8768")
    connected, catalog_calls, errors = False, 0, []
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def state(route):
            response = await route.fetch()
            data = await response.json()
            data["connection"]["browser"] = connected
            data["connection"]["browser_login"].update(
                {"state": "connected" if connected else "idle", "revision": 2 if connected else 0}
            )
            await route.fulfill(json=data)

        async def catalog(route):
            nonlocal catalog_calls
            catalog_calls += 1
            await route.fulfill(
                json={
                    "gpus": [
                        {
                            "id": "RTX 4090",
                            "name": "RTX 4090",
                            "memory_gb": 24,
                            "available": 4 if connected else None,
                            "total": 8 if connected else None,
                        }
                    ],
                    "regions": [{"id": "west-B", "name": "西北B区"}],
                    "region_groups": [
                        {"id": "westDC3", "name": "西北B区", "region_ids": ["west-B"]}
                    ],
                    "active_region": "westDC3",
                    "authenticated": connected,
                    "images": [],
                    "billing": [],
                    "notices": [],
                    "demo": False,
                    "fetched_at": "2026-11-02T00:00:00Z",
                }
            )

        async def connect(route):
            nonlocal connected
            connected = True
            await route.fulfill(json={"detail": "模拟：已连接现有窗口"})

        async def resources(route):
            if not connected:
                await route.fulfill(status=409, json={"detail": "请同步已登录窗口"})
                return
            await route.fulfill(
                json={
                    "gpu_id": "RTX 4090",
                    "offers": [
                        {
                            "id": "mock-machine",
                            "gpu_id": "RTX 4090",
                            "gpu_name": "RTX 4090",
                            "region_name": "西北B区",
                            "memory_gb": 24,
                            "cpu": "12 核 / 卡",
                            "ram": "64 GB / 卡",
                            "available": 4,
                            "total": 8,
                            "price_hour": "2.08",
                            "price_source": "account",
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "page_size": 10,
                    "authenticated": True,
                    "demo": False,
                    "notices": [],
                }
            )

        await page.route("**/api/state", state)
        await page.route("**/api/catalog/*", catalog)
        await page.route("**/api/resources/*", resources)
        await page.route("**/api/connection/browser/connect", connect)
        await page.goto(url + "/#resources")
        await page.locator('.gpu-option input[value="RTX 4090"]').check()
        await page.locator("#resource-connection [data-connect]").click()
        await page.locator(".resource-table .price-source").filter(has_text="账号报价").wait_for()
        assert catalog_calls >= 2, "连接后必须重新读取地区库存"
        assert await page.locator('.gpu-option input[value="RTX 4090"]').is_checked()
        assert "网页登录已连接" in await page.locator("#resource-connection").inner_text()
        assert "登录后获取报价" not in await page.locator(".resources-panel").inner_text()
        await browser.close()
    assert not errors, errors
    print(
        json.dumps(
            {"result": "同步会话后资源与账号报价自动刷新通过", "page_errors": errors},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
