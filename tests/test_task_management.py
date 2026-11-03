"""验证任务编辑和删除的历史保留、并发与接口约束。"""

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from catchgpu.app import create_app
from catchgpu.config import Settings
from catchgpu.models import Instance, Task, TaskInput
from catchgpu.providers.base import ProviderError
from catchgpu.providers.demo import DemoProvider
from catchgpu.scheduler import Scheduler
from tests.conftest import SilentNotifier
from tests.test_scheduler import wait_until


def task_input(task, **changes):
    data = {key: value for key, value in task.model_dump().items() if key in TaskInput.model_fields}
    return TaskInput.model_validate({**data, **changes})


async def test_edit_preserves_results_and_allows_increased_target(store, rule):
    task = Task(provider="demo", rules=[rule])
    result = Instance(
        id="already-created",
        name="已创建实例",
        rule_id=rule.id,
        gpu_name=rule.gpu_name,
        gpu_count=1,
    )
    task.instances.append(result)
    task.status = "completed"
    store.save(task)
    scheduler = Scheduler(store, {"demo": DemoProvider()}, SilentNotifier(store))
    modified = rule.model_copy(update={"instance_count": 2, "max_price": Decimal("8")})
    saved = await scheduler.update(
        task.id, task_input(task, name="修改后", interval=15, rules=[modified.model_dump()])
    )
    assert saved.id == task.id and saved.created_at == task.created_at
    assert saved.rules[0].id == rule.id and saved.completed_for(saved.rules[0]) == 1
    assert saved.instances == [result] and saved.status == "paused"
    assert saved.name == "修改后" and saved.interval == 15


@pytest.mark.parametrize("change", [{"gpu_count": 2}, {"gpu_id": "demo-a100"}])
async def test_edit_cannot_relabel_completed_hardware(store, rule, change):
    task = Task(
        provider="demo",
        rules=[rule],
        instances=[
            Instance(
                id="existing", name="保留", rule_id=rule.id, gpu_name=rule.gpu_name, gpu_count=1
            )
        ],
    )
    store.save(task)
    scheduler = Scheduler(store, {"demo": DemoProvider()}, SilentNotifier(store))
    modified = rule.model_copy(update=change)
    with pytest.raises(ProviderError, match="不能更换"):
        await scheduler.update(task.id, task_input(task, rules=[modified.model_dump()]))
    assert store.get(task.id).instances == task.instances


async def test_cannot_remove_rule_with_success_or_reduce_below_progress(store, rule):
    task = Task(
        provider="demo",
        rules=[rule],
        instances=[
            Instance(
                id=f"existing-{index}",
                name="保留",
                rule_id=rule.id,
                gpu_name=rule.gpu_name,
                gpu_count=1,
            )
            for index in range(2)
        ],
    )
    store.save(task)
    scheduler = Scheduler(store, {"demo": DemoProvider()}, SilentNotifier(store))
    other = rule.model_copy(update={"id": "other"})
    for replacement in [other, rule.model_copy(update={"instance_count": 1})]:
        with pytest.raises(ProviderError, match="已有结果"):
            await scheduler.update(task.id, task_input(task, rules=[replacement.model_dump()]))


async def test_paused_inflight_cannot_edit_or_delete_and_never_resurrects(store, rule):
    sent, release = asyncio.Event(), asyncio.Event()

    class SlowProvider(DemoProvider):
        async def create(self, rule, quote, name):
            sent.set()
            await release.wait()
            return await super().create(rule, quote, name)

    provider = SlowProvider()
    task = Task(provider="demo", rules=[rule])
    store.save(task)
    scheduler = Scheduler(store, {"demo": provider}, SilentNotifier(store))
    await scheduler.start(task.id)
    await sent.wait()
    await scheduler.pause(task.id)
    try:
        for action in [
            lambda: scheduler.delete(task.id),
            lambda: scheduler.update(task.id, task_input(task)),
        ]:
            with pytest.raises(ProviderError, match="在途"):
                await action()
    finally:
        release.set()
        await scheduler.close()
    await scheduler.delete(task.id)
    assert not store.tasks() and len(provider.instances) == 1
    assert any("已删除" in row["message"] for row in store.events())


async def test_pending_requests_prevent_losing_reconciliation(store, rule):
    task = Task(provider="demo", rules=[rule])
    store.save(task)
    store.begin_attempt(task, rule.id, "待核对")
    scheduler = Scheduler(store, {"demo": DemoProvider()}, SilentNotifier(store))
    with pytest.raises(ProviderError, match="待核对"):
        await scheduler.delete(task.id)
    with pytest.raises(ProviderError, match="待核对"):
        await scheduler.update(task.id, task_input(task))
    assert store.get(task.id) and store.pending(task.id)


def test_http_edit_delete_and_unknown_task(tmp_path, rule):
    provider = DemoProvider()
    with TestClient(
        create_app(Settings(data_dir=tmp_path), {"demo": provider}), base_url="http://localhost"
    ) as client:
        payload = {"provider": "demo", "rules": [rule.model_dump(mode="json")]}
        task = client.post("/api/tasks", json=payload).json()
        payload.update(name="接口修改", interval=20)
        updated = client.put(f"/api/tasks/{task['id']}", json=payload)
        assert updated.status_code == 200 and updated.json()["rules"][0]["id"] == rule.id
        before = list(provider.account_instances)
        assert client.delete(f"/api/tasks/{task['id']}").status_code == 200
        assert client.get("/api/state").json()["tasks"] == []
        assert list(provider.account_instances) == before
        assert client.put("/api/tasks/missing", json=payload).status_code == 404
        assert client.delete("/api/tasks/missing").status_code == 404
