"""通过模拟连接响应检查登录界面，不打开真实登录窗口或处理验证码。"""

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    url = os.getenv("CATCHGPU_TEST_URL", "http://127.0.0.1:8767")
    output = Path(".tmp/browser-artifacts/login-flow.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    phase, selected, errors = "idle", [], []
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1050})
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def state(route):
            response = await route.fetch()
            body = await response.json()
            body["connection"]["browser"] = phase == "connected"
            body["connection"]["browser_login"] = {
                "state": phase,
                "name": "Microsoft Edge" if phase != "idle" else "",
                "can_connect": phase != "idle",
                "error": "",
                "options": [
                    {"id": "chrome", "name": "Google Chrome"},
                    {"id": "edge", "name": "Microsoft Edge"},
                ],
            }
            await route.fulfill(json=body)

        async def launch(route):
            nonlocal phase
            selected.append(route.request.post_data_json["browser"])
            phase = "waiting"
            await route.fulfill(json={"detail": "模拟：等待你手动完成登录"})

        async def connect(route):
            nonlocal phase
            phase = "connected"
            await route.fulfill(json={"detail": "模拟：已完成登录状态校验"})

        await page.route("**/api/state", state)
        await page.route("**/api/connection/login", launch)
        await page.route("**/api/connection/browser/connect", connect)
        await page.goto(url + "/#settings")
        await page.locator('#login-browser option[value="edge"]').wait_for(state="attached")
        assert await page.locator("#browser-connect").is_disabled()
        await page.locator("#login-browser").select_option("edge")
        await page.locator("#browser-login").click()
        await page.wait_for_function("!document.querySelector('#browser-connect').disabled")
        assert selected == ["edge"]
        assert "完成登录和安全认证" in await page.locator("#login-status").inner_text()
        await page.screenshot(path=str(output))
        await page.locator("#browser-connect").click()
        await page.wait_for_function(
            "document.querySelector('#login-status').textContent.includes('已校验')"
        )
        assert "网页登录已连接" in await page.locator("#connection-badge").inner_text()
        await page.set_viewport_size({"width": 390, "height": 844})
        assert not await page.evaluate(
            "document.documentElement.scrollWidth > innerWidth"
        ), "手机登录界面横向溢出"
        await browser.close()
    assert not errors, errors
    print(
        json.dumps(
            {"result": "两步登录界面验收通过", "page_errors": errors, "screenshot": str(output)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
