"""单进程异步调度器：串行创建、可中止等待、先记意图再发送请求。"""

import asyncio
from datetime import datetime, timedelta, timezone

from catchgpu.models import StartTarget, Task, TaskInput, now, uid
from catchgpu.instance_monitor import InstanceMonitor
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
        self.instance_monitor = InstanceMonitor(store, notifier)

    def ensure_editable(self, task_id):
        worker = self.workers.get(task_id)
        if worker and not worker.done():
            raise ProviderError("请先暂停任务，并等待在途请求结束后再编辑或删除")
        if self.store.pending(task_id):
            raise ProviderError("存在待核对请求，请先核对结果后再编辑或删除")

    async def update(self, task_id, data: TaskInput):
        async with self.control:
            self.ensure_editable(task_id)
            task = self.store.get(task_id)
            if task.provider != data.provider or task.action != data.action:
                raise ProviderError("不能修改任务的平台或类型，请另建任务")
            if task.action == "start" and task.start_targets != data.start_targets:
                raise ProviderError("开机任务只能修改名称和间隔；更换实例请另建任务")
            new_rules = {rule.id: rule for rule in data.rules}
            for rule in task.rules:
                count = task.completed_for(rule)
                updated = new_rules.get(rule.id)
                if count and (updated is None or updated.instance_count < count):
                    raise ProviderError("不能移除已有结果的规则，目标数量不能少于已完成数量")
                if count and (updated.gpu_id != rule.gpu_id or updated.gpu_count != rule.gpu_count):
                    raise ProviderError("已有结果的规则不能更换 GPU 型号或每实例卡数，请另建规则")
            # 重新校验嵌套对象，保留任务编号、规则编号与历史结果。
            task = Task.model_validate({**task.model_dump(), **data.model_dump()})
            task.status, task.next_check_at, task.last_error = "paused", None, ""
            self._finish_if_complete(task)
            self.store.save(task)
            self.store.event(task.id, "info", "任务配置已修改；已有结果保留，可手动恢复监控")
            return task

    async def delete(self, task_id):
        async with self.control:
            self.ensure_editable(task_id)
            task = self.store.get(task_id)
            self.store.delete(task_id)
            self.workers.pop(task_id, None)
            self.stops.pop(task_id, None)
            self.store.event(task_id, "info", f"监控任务“{task.name}”已删除，平台实例未做修改")

    async def add_start_task(self, provider_id, selection):
        async with self.control:
            provider = self.providers[provider_id]
            selected = set(selection.instance_ids)
            for old in self.store.tasks():
                if (
                    old.provider == provider_id
                    and old.action == "start"
                    and old.status != "completed"
                    and selected.intersection(target.instance_id for target in old.start_targets)
                ):
                    raise ProviderError("选中实例已有未完成的开机任务，请恢复或删除原任务")
            rows = await provider.selected_instances(selection.instance_ids)
            if any(not row.can_start for row in rows.values()):
                raise ProviderError("请只选择可开机的已关机实例，刷新列表后重试")
            task = Task(
                name=f"有卡开机 · {len(rows)} 个实例",
                provider=provider_id,
                action="start",
                interval=selection.interval,
                start_targets=[
                    StartTarget(
                        instance_id=row.id,
                        name=row.name,
                        gpu_name=row.gpu_name,
                        gpu_count=row.gpu_count,
                        region_name=row.region_name,
                    )
                    for row in rows.values()
                ],
            )
            self.store.save(task)
            self.store.event(task.id, "info", "已加入已有实例的有卡开机监控；成功后自动停止")
            self._launch(task, provider)
            return task

    async def power_off(self, provider_id, instance_ids):
        async with self.control:
            provider = self.providers[provider_id]
            rows = await provider.selected_instances(instance_ids)
            # 关机优先于开机监控：先停止相关任务并等待在途请求，避免关机后又被任务启动。
            for task in self.store.tasks():
                if (
                    task.provider == provider_id
                    and task.action == "start"
                    and set(instance_ids).intersection(
                        target.instance_id for target in task.start_targets
                    )
                ):
                    stop = self.stops.get(task.id)
                    if stop:
                        stop.set()
                    worker = self.workers.get(task.id)
                    if worker and not worker.done():
                        await worker
                    task = self.store.get(task.id)
                    if task.status != "completed":
                        task.status, task.next_check_at = "paused", None
                        self.store.save(task)
            results = []
            for instance_id in instance_ids:
                row = rows[instance_id]
                try:
                    current = (await provider.selected_instances([instance_id]))[instance_id]
                    if current.can_stop:
                        await provider.power_off(instance_id)
                        message = "关机请求已提交，请刷新实例状态确认"
                    elif current.can_start:
                        message = "实例已经关机"
                    else:
                        raise ProviderError(f"当前状态不能关机（{current.status}）")
                    self.store.event(
                        None,
                        "info",
                        f"{row.gpu_name}：实例 {row.id}，{message}",
                        subject=row.gpu_name,
                    )
                    results.append(
                        {"instance_id": instance_id, "success": True, "message": message}
                    )
                except ProviderError as exc:
                    self.store.event(
                        None,
                        "warning",
                        f"{row.gpu_name}：实例 {row.id} 关机未确认：{exc}",
                        subject=row.gpu_name,
                    )
                    results.append(
                        {"instance_id": instance_id, "success": False, "message": str(exc)}
                    )
            return results

    def recover(self):
        for task in self.store.tasks():
            if task.status == "running" or self.store.pending(task.id):
                task.status = "attention" if self.store.pending(task.id) else "paused"
                task.next_check_at = None
                task.last_error = (
                    "服务已重启，请核对未完成的实例操作后继续"
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
            if task.action == "create" and self.store.pending(task_id):
                raise UncertainCreation("存在待核对的创建请求，请先核对结果")
            provider = self.providers[task.provider]
            for rule in task.rules:
                await provider.validate(rule)
            self._launch(task, provider)

    def _launch(self, task, provider):
        task.status, task.last_error, task.next_check_at = "running", "", now()
        self.store.save(task)
        self.store.event(task.id, "info", "开始监控；达到全部目标后自动停止")
        stop = self.stops[task.id] = asyncio.Event()
        self.workers[task.id] = asyncio.create_task(self._run(task, provider, stop))

    async def pause(self, task_id):
        async with self.control:
            self._pause(task_id)

    def _pause(self, task_id):
        task = self.store.get(task_id)
        if task.status == "completed":
            return
        if task_id in self.stops:
            self.stops[task_id].set()
        task.status, task.next_check_at = "paused", None
        self.store.save(task)
        self.store.event(task_id, "info", "已暂停监控；已发送的实例操作会完成结果记录")

    async def reconcile(self, task_id):
        async with self.control:
            return await self._reconcile(task_id)

    async def _reconcile(self, task_id):
        worker = self.workers.get(task_id)
        if worker and not worker.done():
            raise ProviderError("正在等待实例操作返回，请稍后核对")
        task = self.store.get(task_id)
        if task.action == "start":
            await self.instance_monitor.round(task, self.providers[task.provider], allow_send=False)
            self._finish_if_complete(task)
            if task.status != "completed":
                task.status = "attention" if self.store.pending(task.id) else "paused"
            self.store.save(task)
            return task
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

    async def confirm_missing(self, task_id, attempt_id):
        async with self.control:
            await self._confirm_missing(task_id, attempt_id)

    async def _confirm_missing(self, task_id, attempt_id):
        worker = self.workers.get(task_id)
        if worker and not worker.done():
            raise ProviderError("实例操作仍在执行，不能标记为未生效")
        pending = self.store.pending(task_id)
        if attempt_id not in {item["id"] for item in pending}:
            raise ProviderError("没有找到该待核对请求")
        task = self.store.get(task_id)
        if task.action == "start":
            attempt = next(item for item in pending if item["id"] == attempt_id)
            row = (await self.providers[task.provider].selected_instances([attempt["rule_id"]]))[
                attempt["rule_id"]
            ]
            if not row.can_start:
                raise ProviderError("实例未确认处于关机状态，不能清除开机请求，请先核对结果")
        self.store.finish_attempt(attempt_id, "confirmed_missing")
        self.store.event(task_id, "warning", "用户已核实请求未生效；允许手动恢复任务")
        task = self.store.get(task_id)
        if not self.store.pending(task_id):
            task.status, task.last_error = "paused", ""
            self.store.save(task)

    def _finish_if_complete(self, task):
        complete = (
            all(
                target.instance_id in {row.id for row in task.instances}
                for target in task.start_targets
            )
            if task.action == "start"
            else all(task.completed_for(rule) >= rule.instance_count for rule in task.rules)
        )
        if complete:
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
                    if task.action == "start":
                        await self.instance_monitor.round(task, provider, stop)
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
                                subject=rule.gpu_name,
                            )
                            continue
                        name = f"cg-{task.id[:8]}-{rule.id[:6]}-{task.completed_for(rule)+1}-{uid()[:4]}"
                        attempt_id = self.store.begin_attempt(task, rule.id, name)
                        self.store.event(
                            task.id,
                            "info",
                            f"{rule.gpu_name}：找到符合限制的资源，正在创建 {name}",
                            subject=rule.gpu_name,
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
