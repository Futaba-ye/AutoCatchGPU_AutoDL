"""已有实例开机不得触发新建；验证重试、在途意图、停止优先与恢复。"""

import asyncio

import pytest

from catchgpu.models import InstanceSelection
from catchgpu.providers.base import OutOfStock, ProviderError, UncertainCreation
from catchgpu.providers.demo import DemoProvider
from catchgpu.scheduler import Scheduler
from tests.conftest import SilentNotifier
from tests.test_scheduler import wait_until
from tests.test_task_management import task_input

INSTANCE = "demo-existing-3090"


def prepare(store, provider):
    notifier = SilentNotifier(store)
    scheduler = Scheduler(store, {"demo": provider}, notifier)

    async def fast_wait(stop, delay):
        await asyncio.sleep(0.005)

    scheduler._wait = fast_wait
    return scheduler, notifier


class ExistingOnly(DemoProvider):
    def __init__(self):
        super().__init__()
        self.starts = 0

    async def quote(self, rule):
        raise AssertionError("已有实例任务不能查询新建资源")

    async def create(self, rule, quote, name):
        raise AssertionError("已有实例任务不能新建实例")

    async def power_on(self, instance_id):
        self.starts += 1
        await super().power_on(instance_id)


async def test_stock_retry_completes_original_instance_and_notifies_once(store):
    class Stock(ExistingOnly):
        async def power_on(self, instance_id):
            if not self.starts:
                self.starts += 1
                raise OutOfStock("GPU 不足")
            await super().power_on(instance_id)

    provider = Stock()
    scheduler, notifier = prepare(store, provider)
    task = await scheduler.add_start_task("demo", InstanceSelection(instance_ids=[INSTANCE]))
    await wait_until(lambda: store.get(task.id).status == "completed")
    await scheduler.close()
    assert provider.starts == 2 and not provider.instances
    assert [row.id for row in store.get(task.id).instances] == [INSTANCE]
    assert not store.pending(task.id) and len(notifier.messages) == 1
    assert any(row["kind"] == "instance_started" for row in store.events())
    assert any(row["subject"] == "RTX 3090" and "下轮" in row["message"] for row in store.events())


async def test_accepted_start_is_polled_and_never_sent_twice(store):
    class Delayed(ExistingOnly):
        async def power_on(self, instance_id):
            self.starts += 1
            self.account_instances[instance_id].status = "starting"

    provider = Delayed()
    scheduler, notifier = prepare(store, provider)
    task = await scheduler.add_start_task("demo", InstanceSelection(instance_ids=[INSTANCE]))
    await wait_until(lambda: store.get(task.id).checks >= 3)
    assert provider.starts == 1 and not notifier.messages and store.pending(task.id)
    provider.account_instances[INSTANCE].status = "running"
    await wait_until(lambda: store.get(task.id).status == "completed")
    await scheduler.close()
    assert provider.starts == 1 and len(notifier.messages) == 1


async def test_timeout_retains_intent_resume_only_observes_and_reconcile_completes(store):
    class Timeout(ExistingOnly):
        async def power_on(self, instance_id):
            self.starts += 1
            raise UncertainCreation("响应丢失")

    provider = Timeout()
    scheduler, notifier = prepare(store, provider)
    task = await scheduler.add_start_task("demo", InstanceSelection(instance_ids=[INSTANCE]))
    await wait_until(lambda: store.get(task.id).status == "attention")
    assert provider.starts == 1 and store.pending(task.id)
    await scheduler.start(task.id)
    await wait_until(lambda: store.get(task.id).checks >= 3)
    await scheduler.pause(task.id)
    await scheduler.close()
    assert provider.starts == 1 and not notifier.messages
    provider.account_instances[INSTANCE].status = "running"
    result = await scheduler.reconcile(task.id)
    assert result.status == "completed" and len(notifier.messages) == 1
    assert provider.starts == 1 and not store.pending(task.id)


async def test_restart_keeps_pending_intent_and_cannot_confirm_running_as_missing(store):
    provider = ExistingOnly()
    scheduler, _ = prepare(store, provider)
    original = scheduler.instance_monitor.round

    async def interrupt(task, provider, stop):
        store.begin_attempt(task, INSTANCE, "power-interrupted")
        raise UncertainCreation("模拟进程中断")

    scheduler.instance_monitor.round = interrupt
    task = await scheduler.add_start_task("demo", InstanceSelection(instance_ids=[INSTANCE]))
    await wait_until(lambda: store.get(task.id).status == "attention")
    await scheduler.close()
    replacement, _ = prepare(store, provider)
    replacement.recover()
    attempt = store.pending(task.id)[0]
    provider.account_instances[INSTANCE].status = "starting"
    with pytest.raises(ProviderError, match="关机状态"):
        await replacement.confirm_missing(task.id, attempt["id"])
    provider.account_instances[INSTANCE].status = "shutdown"
    await replacement.confirm_missing(task.id, attempt["id"])
    assert not store.pending(task.id) and provider.starts == 0


async def test_duplicate_start_jobs_rejected_and_paused_job_is_editable(store):
    class EmptyStock(ExistingOnly):
        async def power_on(self, instance_id):
            raise OutOfStock("无库存")

    scheduler, _ = prepare(store, EmptyStock())
    selected = InstanceSelection(instance_ids=[INSTANCE])
    task = await scheduler.add_start_task("demo", selected)
    with pytest.raises(ProviderError, match="已有未完成"):
        await scheduler.add_start_task("demo", selected)
    await wait_until(lambda: store.get(task.id).checks >= 1)
    await scheduler.pause(task.id)
    await scheduler.close()
    saved = await scheduler.update(task.id, task_input(task, name="等待原实例", interval=10))
    assert saved.start_targets == task.start_targets and saved.interval == 10


async def test_shutdown_waits_for_inflight_start_then_prevents_reopening(store):
    entered, release = asyncio.Event(), asyncio.Event()

    class Inflight(ExistingOnly):
        async def power_on(self, instance_id):
            self.starts += 1
            entered.set()
            await release.wait()
            await DemoProvider.power_on(self, instance_id)

    provider = Inflight()
    scheduler, _ = prepare(store, provider)
    task = await scheduler.add_start_task("demo", InstanceSelection(instance_ids=[INSTANCE]))
    await entered.wait()
    stopping = asyncio.create_task(scheduler.power_off("demo", [INSTANCE]))
    await asyncio.sleep(0.01)
    assert not stopping.done()
    release.set()
    results = await stopping
    assert results[0]["success"] and provider.account_instances[INSTANCE].status == "shutdown"
    await scheduler.close()
    assert provider.starts == 1 and store.get(task.id).status != "running"


async def test_non_gpu_running_is_never_counted_as_success(store):
    class WrongMode(ExistingOnly):
        async def power_on(self, instance_id):
            self.starts += 1
            self.account_instances[instance_id].status = "running"
            self.account_instances[instance_id].start_mode = "non_gpu"

    provider = WrongMode()
    scheduler, notifier = prepare(store, provider)
    task = await scheduler.add_start_task("demo", InstanceSelection(instance_ids=[INSTANCE]))
    await wait_until(lambda: store.get(task.id).status == "attention")
    await scheduler.close()
    assert not store.get(task.id).instances and not notifier.messages and provider.starts == 1


async def test_multiple_targets_complete_without_affecting_other_instances(store):
    provider = ExistingOnly()
    provider.account_instances["demo-existing-4090"].status = "shutdown"
    scheduler, notifier = prepare(store, provider)
    task = await scheduler.add_start_task(
        "demo", InstanceSelection(instance_ids=list(provider.account_instances))
    )
    await wait_until(lambda: store.get(task.id).status == "completed")
    await scheduler.close()
    assert len(store.get(task.id).instances) == 2 and len(notifier.messages) == 2
    assert provider.starts == 2
