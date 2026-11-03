"""用独立空白配置验证本机 Chrome/Edge 的连接与断开，不登录任何账号。"""

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright

from catchgpu.providers.browser_launcher import NativeBrowserLauncher


async def main():
    root = Path(".tmp/native-browser-check") / uuid4().hex
    launcher = NativeBrowserLauncher(root)
    command = launcher.command

    def blank_command(browser, profile, port):
        args = command(browser, profile, port)
        return args[:-1] + ["--headless=new", "about:blank"]

    launcher.command = blank_command
    runtime = None
    try:
        name = await launcher.open(os.getenv("CATCHGPU_TEST_BROWSER", "auto"))
        runtime = await async_playwright().start()
        browser = await runtime.chromium.connect_over_cdp(launcher.endpoint)
        assert browser.contexts
        page = browser.contexts[0].pages[0]
        await page.goto("data:text/html,<meta charset=utf-8><title>本机浏览器连接检查</title>")
        assert await page.title() == "本机浏览器连接检查"
        await runtime.stop()
        runtime = None
        assert await launcher.available(), "断开工具后，用户浏览器应保持运行"
        runtime = await async_playwright().start()
        browser = await runtime.chromium.connect_over_cdp(launcher.endpoint)
        assert await browser.contexts[0].pages[0].title() == "本机浏览器连接检查"
        # 只关闭本脚本创建的独立空白浏览器，不操作其他浏览器进程。
        session = await browser.new_browser_cdp_session()
        await session.send("Browser.close")
        print(
            json.dumps(
                {"browser": name, "result": "本机浏览器连接、断开保留及重连检查通过"},
                ensure_ascii=False,
            )
        )
    finally:
        with suppress(Exception):
            if await launcher.available():
                runtime = runtime or await async_playwright().start()
                cleanup_browser = await runtime.chromium.connect_over_cdp(launcher.endpoint)
                cleanup_session = await cleanup_browser.new_browser_cdp_session()
                await cleanup_session.send("Browser.close")
        if runtime:
            await runtime.stop()
        if launcher.process and launcher.process.poll() is None:
            await asyncio.to_thread(launcher.process.wait, 10)


if __name__ == "__main__":
    asyncio.run(main())
