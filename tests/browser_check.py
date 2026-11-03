"""独立页面验收脚本，显式使用演示环境；运行前启动隔离服务。"""

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    url = os.getenv("CATCHGPU_TEST_URL", "http://127.0.0.1:8766")
    output = Path(".tmp/browser-artifacts")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1440, "height": 1100}, device_scale_factor=1
        )
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(url)
        await page.locator("#provider option").first.wait_for(state="attached")
        await page.locator("#provider").select_option("demo")
        await page.locator(".gpu-option").nth(5).wait_for()
        await page.get_by_role("link", name="连接与通知").click()
        if await page.locator("#notify-desktop").is_checked():
            await page.locator("#notify-desktop").uncheck()
            await page.get_by_text("通知偏好已保存", exact=True).wait_for()
        await page.get_by_role("link", name="监控概览").click()
        await page.wait_for_function(
            "document.querySelector('#page-name').textContent === '监控概览'"
        )
        await page.locator('.gpu-option input[value="demo-4090"]').check()
        await page.locator('.gpu-option input[value="demo-5090"]').check()
        await page.locator("#configure-rules").click()
        await page.locator('#batch-fields [data-field="max_price"]').fill("10")
        await page.locator('#batch-fields [data-field="gpu_count"]').fill("2")
        await page.locator('#batch-fields [data-field="price_scope"]').select_option("instance")
        await page.locator('#batch-fields [data-field="include_disk"]').check()
        await page.locator('#batch-fields [data-field="disk_gb"]').fill("50")
        await page.locator("#apply-batch").click()
        caps = await page.locator('#rule-rows [data-field="max_price"]').evaluate_all(
            "nodes => nodes.map(node => node.value)"
        )
        assert caps == ["10", "10"], caps
        await page.locator("#task-name").fill("双型号算力目标 · 演示验收")
        await page.screenshot(path=str(output / "batch-rules.png"), full_page=True)
        await page.get_by_role("button", name="保存并开始监控").click()
        await page.locator("#rule-dialog").wait_for(state="hidden")
        await page.locator(".task-card .tag.green").filter(has_text="已完成").first.wait_for(
            timeout=15000
        )
        assert await page.locator(".task-card .instance-list code").count() >= 2
        await page.get_by_role("link", name="监控概览").click()
        await page.wait_for_function(
            "document.querySelector('#page-name').textContent === '监控概览'"
        )
        await page.locator(".toast").last.wait_for(state="hidden", timeout=15000)
        await page.screenshot(path=str(output / "dashboard.png"), full_page=True)
        await page.get_by_role("link", name="运行日志").click()
        await page.locator("#log-level").select_option("success")
        assert await page.locator("#log-list .log-level.success").count() >= 2
        await page.get_by_role("link", name="连接与通知").click()
        assert not await page.locator("#notify-desktop").is_checked()
        await page.reload()
        await page.locator("#provider option").first.wait_for(state="attached")
        assert not await page.locator("#notify-desktop").is_checked()
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.get_by_role("link", name="监控概览").click()
        await page.locator("#provider").select_option("demo")
        await page.locator(".gpu-option").nth(5).wait_for()
        overflow = await page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert not overflow, "手机视图存在横向溢出"
        await page.screenshot(path=str(output / "mobile.png"), full_page=True)
        await browser.close()
    assert not errors, errors
    print(
        json.dumps(
            {"result": "通过", "screenshots": str(output), "page_errors": errors},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
