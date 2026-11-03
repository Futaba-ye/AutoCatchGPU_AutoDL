"""演示账号页面验收：编辑删除、实例开关机、开机监控、日志高亮和手机布局。"""

import asyncio
import json
import os
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, expect


async def main():
    url = os.getenv("CATCHGPU_TEST_URL", "http://127.0.0.1:8770")
    output = Path(".tmp/browser-artifacts")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    async with httpx.AsyncClient(base_url=url) as api:
        await api.put("/api/notifications", json={"desktop": False, "speech": False})
        old = (await api.get("/api/state")).json()["tasks"]
        for task in old:
            if task["provider"] == "demo":
                await api.post(f"/api/tasks/{task['id']}/pause")
                await asyncio.sleep(0.05)
                await api.delete(f"/api/tasks/{task['id']}")
        rows = (await api.get("/api/instances/demo")).json()["items"]
        original_instance_ids = {row["id"] for row in rows}
        for row in rows:
            if row["id"] == "demo-existing-3090" and row["can_stop"]:
                await api.post("/api/instances/demo/stop", json={"instance_ids": [row["id"]]})
            if row["id"] == "demo-existing-4090" and row["can_start"]:
                await api.post(
                    "/api/instances/demo/start-monitor", json={"instance_ids": [row["id"]]}
                )
                await asyncio.sleep(0.1)
        created = await api.post(
            "/api/tasks",
            json={
                "name": "待修改任务 · 演示",
                "provider": "demo",
                "rules": [
                    {
                        "id": "edit-3090-rule",
                        "gpu_id": "demo-3090",
                        "gpu_name": "RTX 3090",
                        "image_id": "demo-pytorch",
                        "max_price": "0.1",
                    }
                ],
            },
        )
        created.raise_for_status()
        task_id = created.json()["id"]

        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1440, "height": 1050})
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(url + "/#tasks")
            card = page.locator(f'[data-task-card="{task_id}"]')
            await card.get_by_role("button", name="修改", exact=True).click()
            await expect(page.locator("#task-name")).to_have_value("待修改任务 · 演示")
            await page.locator("#task-name").fill("RTX 3090 等待合适价格 · 已修改")
            await page.locator("#task-interval").fill("10")
            await page.get_by_role("button", name="仅保存任务").click()
            await page.locator("#rule-dialog").wait_for(state="hidden")
            saved = next(
                task
                for task in (await api.get("/api/state")).json()["tasks"]
                if task["id"] == task_id
            )
            assert saved["rules"][0]["id"] == "edit-3090-rule" and saved["interval"] == 10
            await card.get_by_role("button", name="恢复监控").click()
            await expect(page.locator("#log-list")).to_contain_text(
                "暂无符合库存、地区及价格限制的资源", timeout=15000
            )
            await card.get_by_role("button", name="暂停监控").click()
            await card.get_by_role("button", name="修改", exact=True).wait_for()

            await page.get_by_role("link", name="账号实例", exact=True).click()
            await page.locator("#instance-provider").select_option("demo")
            await page.locator('[data-instance="demo-existing-4090"]').check()
            await expect(page.locator("#stop-selected-instances")).to_be_enabled()
            page.once("dialog", lambda dialog: dialog.accept())
            await page.locator("#stop-selected-instances").click()
            stopped_row = page.locator("#account-instance-list tr").filter(
                has=page.locator('[data-instance="demo-existing-4090"]')
            )
            await expect(stopped_row).to_contain_text("已关机")
            await page.locator(".toast").last.wait_for(state="hidden", timeout=15000)
            await page.screenshot(path=str(output / "account-instances.png"), full_page=True)
            await page.locator('[data-instance="demo-existing-3090"]').check()
            await page.locator("#instance-interval").fill("10")
            await page.locator("#start-selected-instances").click()
            await expect(page).to_have_url(url + "/#tasks")
            started = page.locator(".task-card").filter(has_text="已有实例有卡开机").first
            await expect(started.locator(".task-heading .tag").last).to_have_text(
                "已完成", timeout=15000
            )
            await started.get_by_role("button", name="修改", exact=True).click()
            await page.locator("#start-task-name").fill("原训练环境有卡开机 · 已修改")
            await page.locator("#start-task-interval").fill("20")
            await page.locator("#start-task-form").get_by_role("button", name="保存修改").click()
            await page.locator("#start-task-dialog").wait_for(state="hidden")
            await started.locator("summary").click()
            await page.locator(".toast").last.wait_for(state="hidden", timeout=15000)
            await page.screenshot(path=str(output / "task-management.png"), full_page=True)
            state = (await api.get("/api/state")).json()
            startup = next(
                task
                for task in state["tasks"]
                if task["action"] == "start"
                and task["instances"]
                and task["instances"][0]["id"] == "demo-existing-3090"
            )
            assert startup["interval"] == 20 and startup["status"] == "completed"
            assert {
                row["id"] for row in (await api.get("/api/instances/demo")).json()["items"]
            } == original_instance_ids

            await page.get_by_role("link", name="运行日志", exact=True).click()
            await expect(page.locator(".log-gpu").filter(has_text="RTX 3090").first).to_be_visible()
            color = await page.locator(".log-gpu").first.evaluate(
                "node => getComputedStyle(node).color"
            )
            normal = await page.locator(".log-gpu").first.evaluate(
                "node => getComputedStyle(node.parentElement).color"
            )
            assert color != normal
            safe = await page.evaluate(
                "async () => (await import('/static/js/ui.js')).logMessage({subject:'RTX <img onerror=alert(1)>', message:'RTX <img onerror=alert(1)>：等待'})"
            )
            assert "<img" not in safe and "&lt;img" in safe
            await page.locator("#log-search").fill("RTX")
            await page.screenshot(path=str(output / "gpu-log-colors.png"), full_page=True)

            await page.locator('[data-nav="tasks"]').click()
            page.once("dialog", lambda dialog: dialog.accept())
            await card.get_by_role("button", name="删除", exact=True).click()
            await expect(card).to_have_count(0)
            assert not any(
                task["id"] == task_id for task in (await api.get("/api/state")).json()["tasks"]
            )
            assert any(log["task_id"] == task_id for log in (await api.get("/api/events")).json())

            await page.set_viewport_size({"width": 390, "height": 844})
            await page.get_by_role("link", name="账号实例", exact=True).click()
            await page.locator('[data-instance="demo-existing-3090"]').wait_for()
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > innerWidth"
            ), "手机实例页存在横向溢出"
            await page.locator(".toast").last.wait_for(state="hidden", timeout=15000)
            await page.screenshot(path=str(output / "account-instances-mobile.png"), full_page=True)
            await page.locator('[data-nav="tasks"]').click()
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > innerWidth"
            ), "手机任务页存在横向溢出"
            await browser.close()
    assert not errors, errors
    print(
        json.dumps(
            {
                "result": "任务修改删除、账号实例开关机、有卡监控、日志高亮与手机布局通过",
                "page_errors": errors,
                "screenshots": str(output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
