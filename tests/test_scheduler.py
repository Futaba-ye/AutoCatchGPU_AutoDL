"""覆盖会导致额外付费或重复创建的调度边界。"""

import asyncio

import pytest

from catchgpu.models import Task
from catchgpu.providers.base import (
    AuthenticationError,
    OutOfStock,
    ProviderError,
    UncertainCreation,
)
from catchgpu.providers.demo import DemoProvider
from catchgpu.scheduler import Scheduler
from tests.conftest import SilentNotifier


async def wait_until(check):
    async with asyncio.timeout(3):
        while not check():
            await asyncio.sleep(0.01)


def prepare(store, rule, provider=None):
    provider = provider or DemoProvider()
    task = Task(name="测试任务", provider=provider.id, rules=[rule])
    store.save(task)
    notifier = SilentNotifier(store)
    scheduler = Scheduler(store, {provider.id: provider}, notifier)
    return task, scheduler, provider, notifier


async def test_multiple_rules_complete_exact_targets(store, rule):
    task, scheduler, provider, notifier = prepare(store, rule)
    second = rule.model_copy(
        update={"id": "second-rule", "gpu_id": "demo-a100", "gpu_name": "A100", "gpu_count": 2}
    )
    task.rules.append(second)
    store.save(task)
    await scheduler.start(task.id)
    await wait_until(lambda: store.get(task.id).status == "completed")
    await scheduler.close()
    result = store.get(task.id)
    assert len(result.instances) == 2
    assert {instance.gpu_count for instance in result.instances} == {1, 2}
    assert not store.pending(task.id)
    assert len(notifier.messages) == 2
    assert len(provider.instances) == 2


async def test_multiple_instances_stop_at_individual_targets(store, rule, monkeypatch):
    rule.instance_count = 3
    task, scheduler, provider, notifier = prepare(store, rule)
    task.rules.append(
        rule.model_copy(
            update={
                "id": "other",
                "gpu_id": "demo-5090",
                "gpu_name": "RTX 5090",
                "instance_count": 2,
            }
        )
    )
    store.save(task)

    async def fast_wait(stop, delay):
        await asyncio.sleep(0)

    monkeypatch.setattr(scheduler, "_wait", fast_wait)
    await scheduler.start(task.id)
    await wait_until(lambda: store.get(task.id).status == "completed")
    await scheduler.close()
    result = store.get(task.id)
    assert result.completed_for(result.rules[0]) == 3
    assert result.completed_for(result.rules[1]) == 2
    assert len(provider.instances) == 5
    with pytest.raises(ProviderError):
        await scheduler.start(task.id)


async def test_creation_timeout_never_retried_automatically(store, rule):
    class TimeoutProvider(DemoProvider):
        calls = 0

        async def create(self, rule, quote, name):
            self.calls += 1
            await super().create(rule, quote, name)
            raise UncertainCreation("模拟服务已接收请求，但响应丢失")

    task, scheduler, provider, notifier = prepare(store, rule, TimeoutProvider())
    await scheduler.start(task.id)
    await wait_until(lambda: store.get(task.id).status == "attention")
    with pytest.raises(UncertainCreation):
        await scheduler.start(task.id)
    assert provider.calls == 1
    assert len(store.pending(task.id)) == 1
    await scheduler.reconcile(task.id)
    assert store.get(task.id).status == "completed"
    assert len(store.get(task.id).instances) == 1
    assert provider.calls == 1
    await scheduler.close()


async def test_pause_during_create_keeps_inflight_result(store, rule):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowProvider(DemoProvider):
        async def create(self, rule, quote, name):
            entered.set()
            await release.wait()
            return await super().create(rule, quote, name)

    rule.instance_count = 2
    task, scheduler, provider, notifier = prepare(store, rule, SlowProvider())
    await scheduler.start(task.id)
    await entered.wait()
    await scheduler.pause(task.id)
    with pytest.raises(ProviderError):
        await scheduler.start(task.id)
    release.set()
    await scheduler.close()
    assert store.get(task.id).status == "paused"
    assert len(store.get(task.id).instances) == 1
    assert len(provider.instances) == 1
    assert not store.pending(task.id)


async def test_pause_while_quote_inflight_does_not_create(store, rule):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowQuote(DemoProvider):
        async def quote(self, rule):
            entered.set()
            await release.wait()
            return await super().quote(rule)

    task, scheduler, provider, notifier = prepare(store, rule, SlowQuote())
    await scheduler.start(task.id)
    await entered.wait()
    await scheduler.pause(task.id)
    release.set()
    await scheduler.close()
    assert not provider.instances


async def test_repeated_start_cannot_launch_two_workers(store, rule):
    task, scheduler, provider, notifier = prepare(store, rule)
    results = await asyncio.gather(
        scheduler.start(task.id), scheduler.start(task.id), return_exceptions=True
    )
    assert sum(isinstance(result, ProviderError) for result in results) == 1
    await wait_until(lambda: store.get(task.id).status == "completed")
    await scheduler.close()
    assert len(provider.instances) == 1


async def test_no_stock_does_not_leave_pending_attempt(store, rule):
    class SoldOut(DemoProvider):
        async def create(self, rule, quote, name):
            raise OutOfStock("模拟库存已被占用")

    task, scheduler, provider, notifier = prepare(store, rule, SoldOut())
    await scheduler.start(task.id)
    await wait_until(lambda: bool(store.get(task.id).last_error))
    await scheduler.close()
    assert not store.pending(task.id)
    assert not store.get(task.id).instances


async def test_authentication_failure_pauses_and_logs(store, rule):
    class Expired(DemoProvider):
        async def quote(self, rule):
            raise AuthenticationError("登录过期")

    task, scheduler, provider, notifier = prepare(store, rule, Expired())
    await scheduler.start(task.id)
    await wait_until(lambda: store.get(task.id).status == "attention")
    await scheduler.close()
    assert any(event["level"] == "error" for event in store.events())
    assert not provider.instances


def test_restart_preserves_ambiguous_attempt(store, rule):
    task, scheduler, provider, notifier = prepare(store, rule)
    task.status = "running"
    store.save(task)
    store.begin_attempt(task, rule.id, "cg-unique-intent")
    scheduler.recover()
    assert store.get(task.id).status == "attention"
    assert store.pending(task.id)[0]["name"] == "cg-unique-intent"


def test_restart_pauses_running_task_without_pending(store, rule):
    task, scheduler, provider, notifier = prepare(store, rule)
    task.status = "running"
    store.save(task)
    scheduler.recover()
    assert store.get(task.id).status == "paused"


async def test_unknown_creation_error_preserves_attempt(store, rule):
    class Unexpected(DemoProvider):
        async def create(self, rule, quote, name):
            raise ValueError("不应在日志出现的敏感数据")

    task, scheduler, provider, notifier = prepare(store, rule, Unexpected())
    await scheduler.start(task.id)
    await wait_until(lambda: store.get(task.id).status == "attention")
    await scheduler.close()
    assert len(store.pending(task.id)) == 1
    assert "敏感数据" not in str(store.events())
