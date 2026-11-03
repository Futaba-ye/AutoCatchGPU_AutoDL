"""验收地区筛选、紧凑型号、收藏和按需详情，仅操作演示平台。"""

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    url = os.getenv("CATCHGPU_TEST_URL", "http://127.0.0.1:8768")
    output = Path(".tmp/browser-artifacts")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    detail_requests = []
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: (
                detail_requests.append(request.url) if "/api/resources/" in request.url else None
            ),
        )
        await page.goto(url + "/#resources")
        await page.locator("#provider option").first.wait_for(state="attached")
        await page.locator("#provider").select_option("demo")
        await page.locator(".gpu-option").nth(5).wait_for()
        assert not detail_requests, "未选择型号时不应请求详细资源"
        assert await page.locator("#resource-details").is_hidden()
        await page.locator('#region-options [data-region="demo-north"]').click()
        await page.wait_for_function(
            "document.querySelector('#region-options [data-region=\"demo-north\"]').getAttribute('aria-pressed') === 'true'"
        )
        favorite = page.locator('#gpu-grid [data-favorite="demo-4090"]')
        if await favorite.get_attribute("aria-pressed") == "true":
            await favorite.click()
            await page.wait_for_function(
                "document.querySelector('#gpu-grid [data-favorite=\"demo-4090\"]').getAttribute('aria-pressed') === 'false'"
            )
        await page.locator('#gpu-grid [data-favorite="demo-4090"]').click()
        await page.locator('#favorite-strip [data-quick="demo-4090"]').wait_for()
        await page.locator("#favorite-only").check()
        assert await page.locator(".gpu-option").count() == 1
        await page.reload()
        await page.locator("#provider option").first.wait_for(state="attached")
        await page.locator("#provider").select_option("demo")
        await page.locator(".gpu-option").nth(5).wait_for()
        await page.locator('#favorite-strip [data-quick="demo-4090"]').wait_for()
        assert (
            await page.locator('#gpu-grid [data-favorite="demo-4090"]').get_attribute(
                "aria-pressed"
            )
            == "true"
        )
        await page.locator('#favorite-strip [data-quick="demo-4090"]').click()
        await page.locator(".resource-table tbody tr").first.wait_for()
        assert len(detail_requests) > 0
        assert await page.locator(".resource-table tbody tr").count() == 3
        assert "登录后获取报价" not in await page.locator("#resource-details").inner_text()
        await page.locator("#configure-rules").click()
        selected_regions = await page.locator('#rule-rows [data-field="regions"]').first.evaluate(
            "select => [...select.selectedOptions].map(option => option.value)"
        )
        assert selected_regions == ["demo-east"], selected_regions
        await page.locator('[data-close="rule-dialog"]').click()
        await page.screenshot(path=str(output / "market.png"), full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        assert not await page.evaluate(
            "document.documentElement.scrollWidth > innerWidth"
        ), "手机资源区横向溢出"
        await page.screenshot(path=str(output / "market-mobile.png"), full_page=True)
        await browser.close()
    assert not errors, errors
    print(
        json.dumps(
            {"result": "地区、收藏、按需详情和手机布局验收通过", "page_errors": errors},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
