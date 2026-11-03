"""单进程异步调度器：串行创建、可中止等待、先记意图再发送请求。"""

import asyncio
from datetime import datetime, timedelta, timezone

from catchgpu.models import Task, now, uid
from catchgpu.notifications import Notifier
from catchgpu.providers.base import (
    AuthenticationError,
    OutOfStock,
    Provider,
    ProviderError,
    UncertainCreation,
    UnsupportedError,
)
from catchgpu.storage import Store


class Scheduler:
    def __init__(self, store: Store, providers: dict[str, Provider], notifier: Notifier):
        self.store, self.providers, self.notifier = store, providers, notifier
        self.workers: dict[str, asyncio.Task] = {}
        self.stops: dict[str, asyncio.Event] = {}
        self.control = asyncio.Lock()

    def recover(self):
        for task in self.store.tasks():
            if task.status == "running" or self.store.pending(task.id):
                task.status = "attention" if self.store.pending(task.id) else "paused"
                task.next_check_at = None
                task.last_error = (
                    "服务已重启，请核对未完成的创建请求后继续"
                    if task.status == "attention"
                    else "服务已重启，可手动恢复监控"
                )
                self.store.save(task)
                self.store.event(task.id, "warning", task.last_error)

    async def start(self, task_id):
        async with self.control:
            worker = self.workers.get(task_id)
            if worker and not worker.done():
                raise ProviderError("任务已在运行或正在停止，请稍后操作")
            task = self.store.get(task_id)
            if task.status == "completed":
                raise ProviderError("任务已达到目标，请新建任务")
            if self.store.pending(task_id):
                raise UncertainCreation("存在待核对的创建请求，请先核对结果")
            provider = self.providers[task.provider]
            for rule in task.rules:
                await provider.validate(rule)
            task.status, task.last_error, task.next_check_at = "running", "", now()
            self.store.save(task)
            self.store.event(task.id, "info", "开始监控；达到全部规则目标后自动停止")
            stop = self.stops[task_id] = asyncio.Event()
            self.workers[task_id] = asyncio.create_task(self._run(task, provider, stop))

    async def pause(self, task_id):
        task = self.store.get(task_id)
        if task.status == "completed":
            return
        if task_id in self.stops:
            self.stops[task_id].set()
        task.status, task.next_check_at = "paused", None
        self.store.save(task)
        self.store.event(task_id, "info", "已暂停监控；已发送的创建请求会完成结果记录")

    async def reconcile(self, task_id):
        worker = self.workers.get(task_id)
        if worker and not worker.done():
            raise ProviderError("正在等待创建请求返回，请稍后核对")
        task = self.store.get(task_id)
        for attempt in self.store.pending(task_id):
            rule = next(rule for rule in task.rules if rule.id == attempt["rule_id"])
            instance = await self.providers[task.provider].find_instance(attempt["name"], rule)
            if instance:
                self.store.commit_instance(task, instance, attempt["id"])
                await self.notifier.success(
                    task.id, f"核对成功：{rule.gpu_name} × {rule.gpu_count}，实例 {instance.id}"
                )
            else:
                self.store.event(
                    task_id,
                    "warning",
                    f"尚未找到实例 {attempt['name']}；保留待核对状态，请检查 AutoDL 实例和订单列表",
                )
        self._finish_if_complete(task)
        if task.status != "completed":
            task.status = "attention" if self.store.pending(task_id) else "paused"
        self.store.save(task)
        return task

    def confirm_missing(self, task_id, attempt_id):
        worker = self.workers.get(task_id)
        if worker and not worker.done():
            raise ProviderError("创建请求仍在执行，不能标记为未创建")
        pending = self.store.pending(task_id)
        if attempt_id not in {item["id"] for item in pending}:
            raise ProviderError("没有找到该待核对请求")
        self.store.finish_attempt(attempt_id, "confirmed_missing")
        self.store.event(
            task_id, "warning", "用户已在平台核实请求未创建实例且无待支付订单；允许手动恢复任务"
        )
        task = self.store.get(task_id)
        if not self.store.pending(task_id):
            task.status, task.last_error = "paused", ""
            self.store.save(task)

    def _finish_if_complete(self, task):
        if all(task.completed_for(rule) >= rule.instance_count for rule in task.rules):
            if task.status != "completed":
                self.store.event(
                    task.id, "success", "全部目标已完成，监控已自动停止", "task_completed"
                )
            task.status, task.next_check_at, task.last_error = "completed", None, ""
            return True
        return False

    async def _run(self, task: Task, provider: Provider, stop: asyncio.Event):
        errors = 0
        try:
            while not stop.is_set():
                task.checks += 1
                try:
                    for rule in task.rules:
                        if stop.is_set():
                            break
                        if task.completed_for(rule) >= rule.instance_count:
                            continue
                        quote = await provider.quote(rule)
                        if stop.is_set():
                            break
                        if quote is None or not quote.allowed(rule):
                            self.store.event(
                                task.id,
                                "info",
                                f"{rule.gpu_name}：暂无符合库存、地区及价格限制的资源",
                            )
                            continue
                        name = f"cg-{task.id[:8]}-{rule.id[:6]}-{task.completed_for(rule)+1}-{uid()[:4]}"
                        attempt_id = self.store.begin_attempt(task, rule.id, name)
                        self.store.event(
                            task.id, "info", f"{rule.gpu_name}：找到符合限制的资源，正在创建 {name}"
                        )
                        try:
                            instance = await provider.create(rule, quote, name)
                        except UncertainCreation:
                            raise
                        except (OutOfStock, AuthenticationError, UnsupportedError, ProviderError):
                            self.store.finish_attempt(attempt_id, "rejected")
                            raise
                        self.store.commit_instance(task, instance, attempt_id)
                        prefix = "【演示】" if provider.demo else ""
                        await self.notifier.success(
                            task.id,
                            f"{prefix}{rule.gpu_name} × {rule.gpu_count}，实例 {instance.id} 已创建",
                        )
                    errors, task.last_error = 0, ""
                    if self._finish_if_complete(task):
                        self.store.save(task)
                        return
                except (AuthenticationError, UnsupportedError, UncertainCreation) as exc:
                    task.status, task.last_error = "attention", str(exc)
                    task.next_check_at = None
                    self.store.event(task.id, "error", str(exc))
                    self.store.save(task)
                    return
                except (OutOfStock, ProviderError) as exc:
                    errors += 1
                    task.last_error = str(exc)
                    self.store.event(task.id, "warning", str(exc))
                if stop.is_set():
                    break
                delay = min(task.interval * (2 ** min(errors, 4)), 300) if errors else task.interval
                task.next_check_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).isoformat()
                self.store.save(task)
                await self._wait(stop, delay)
            task.status, task.next_check_at = "paused", None
            self.store.save(task)
        except asyncio.CancelledError:
            task.status = "attention" if self.store.pending(task.id) else "paused"
            task.next_check_at = None
            self.store.save(task)
            raise
        except Exception as exc:
            task.status, task.next_check_at = "attention", None
            task.last_error = (
                f"内部异常（{type(exc).__name__}），已暂停任务。若有待核对请求，请先检查实例与订单"
            )
            self.store.event(task.id, "error", task.last_error)
            self.store.save(task)

    async def close(self):
        for stop in self.stops.values():
            stop.set()
        if self.workers:
            await asyncio.gather(*self.workers.values(), return_exceptions=True)

    async def _wait(self, stop: asyncio.Event, delay: int):
        """等待可被暂停信号中断；测试可替换此等待边界。"""
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass
