"""已有实例的有卡开机监控；不会查询新建库存、创建实例或支付订单。"""

from datetime import datetime, timezone

from catchgpu.models import Instance, uid
from catchgpu.providers.base import (
    AuthenticationError,
    OutOfStock,
    ProviderError,
    UncertainCreation,
    UnsupportedError,
)


class InstanceMonitor:
    def __init__(self, store, notifier):
        self.store, self.notifier = store, notifier

    async def round(self, task, provider, stop=None, *, allow_send=True):
        completed = {instance.id for instance in task.instances}
        targets = [target for target in task.start_targets if target.instance_id not in completed]
        if not targets:
            return
        rows = await provider.selected_instances([target.instance_id for target in targets])
        for target in targets:
            if stop and stop.is_set():
                return
            row = rows[target.instance_id]
            if (
                row.gpu_count != target.gpu_count
                or row.gpu_name != target.gpu_name
                or row.region_name != target.region_name
            ):
                raise UnsupportedError(
                    f"{target.gpu_name}：实例配置已变化，请核对并重新建立开机任务"
                )
            pending = next(
                (item for item in self.store.pending(task.id) if item["rule_id"] == row.id), None
            )
            if row.status == "running" and row.start_mode == "gpu":
                await self.complete(task, target, row, pending)
                continue
            if row.status == "running" and row.start_mode == "non_gpu":
                raise UnsupportedError(
                    f"{target.gpu_name}：实例正在无卡模式运行，请先关机，再恢复有卡开机监控"
                )
            if pending:
                elapsed = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(pending["created_at"])
                ).total_seconds()
                if row.can_start and elapsed > 180:
                    raise UncertainCreation(
                        f"{target.gpu_name}：开机请求已发送，但仍未确认实例运行，请核对结果"
                    )
                self.store.event(
                    task.id,
                    "info",
                    f"{target.gpu_name}：等待实例 {row.id} 进入有卡运行状态（{row.status}）",
                    subject=target.gpu_name,
                )
                continue
            if not allow_send:
                continue
            if row.status == "starting" and row.start_mode == "gpu":
                # 实例可能已由用户在官网启动，后续只观察，不重复发送。
                self.store.begin_attempt(task, row.id, f"power-{uid()}")
                continue
            if not row.can_start:
                raise UnsupportedError(
                    f"{target.gpu_name}：实例 {row.id} 当前不能有卡开机（{row.status} / {row.start_mode}），请先在实例列表核对"
                )
            # 普通按量实例固定在原主机。已知卡数不足时等待，不发送必然失败的开机请求。
            # 已接受的开机请求先在上方核对；此时 GPU 可能已分配，不能再次按空闲量判断。
            if (
                row.billing == "payg"
                and row.available_gpus is not None
                and row.available_gpus < target.gpu_count
            ):
                self.store.event(
                    task.id,
                    "info",
                    f"{target.gpu_name}：原主机空闲 GPU {row.available_gpus} 张，需要 {target.gpu_count} 张，等待库存，下轮继续检测",
                    subject=target.gpu_name,
                )
                continue
            attempt = self.store.begin_attempt(task, row.id, f"power-{uid()}")
            self.store.event(
                task.id,
                "info",
                f"{target.gpu_name}：尝试为已有实例 {row.id} 有卡开机",
                subject=target.gpu_name,
            )
            try:
                await provider.power_on(row.id)
            except UncertainCreation:
                raise
            except OutOfStock:
                self.store.finish_attempt(attempt, "rejected")
                self.store.event(
                    task.id,
                    "warning",
                    f"{target.gpu_name}：暂无可用于该实例的 GPU，下轮继续尝试有卡开机",
                    subject=target.gpu_name,
                )
                continue
            except (AuthenticationError, UnsupportedError, ProviderError):
                self.store.finish_attempt(attempt, "rejected")
                raise
            self.store.event(
                task.id,
                "info",
                f"{target.gpu_name}：平台已接受开机请求，正在确认实例状态",
                subject=target.gpu_name,
            )
            latest = (await provider.selected_instances([row.id]))[row.id]
            if latest.status == "running" and latest.start_mode == "gpu":
                pending = next(
                    item for item in self.store.pending(task.id) if item["id"] == attempt
                )
                await self.complete(task, target, latest, pending)

    async def complete(self, task, target, row, pending):
        attempt = (
            pending["id"] if pending else self.store.begin_attempt(task, row.id, f"power-{uid()}")
        )
        instance = Instance(
            id=row.id,
            name=row.name,
            rule_id=row.id,
            gpu_name=target.gpu_name,
            gpu_count=target.gpu_count,
        )
        self.store.commit_instance(task, instance, attempt)
        await self.notifier.success(
            task.id,
            f"{target.gpu_name} × {target.gpu_count}，实例 {row.id} 已有卡开机",
            kind="instance_started",
            subject=target.gpu_name,
        )
