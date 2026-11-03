"""账号实例接口映射，与新建实例的库存和报价逻辑分开维护。"""

from catchgpu.models import AccountInstance, AccountInstancePage
from catchgpu.providers.base import UnsupportedError


class AutoDLInstances:
    def __init__(self, connection, pro=False):
        self.connection, self.pro = connection, pro

    async def request(self, operation, payload, *, mutation=False):
        if self.pro and self.connection.token:
            path = "/api/v1/dev/instance/pro/" + operation
            return await self.connection.api("POST", path, payload, mutation=mutation)
        path = "/api/v1/instance" + ("/pro" if self.pro else "")
        if operation != "list":
            path += "/" + operation
        return await self.connection.browser.request("POST", path, payload, mutation=mutation)

    async def list_instances(self, page=1, page_size=20):
        data = await self.request("list", {"page_index": page, "page_size": page_size})
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise UnsupportedError("AutoDL 实例列表结构发生变化，请更新实例适配器")
        rows = []
        for entry in data["list"]:
            if not entry.get("uuid"):
                raise UnsupportedError("AutoDL 实例编号缺失，已停止读取")
            rows.append(
                AccountInstance(
                    id=entry["uuid"],
                    name=entry.get("name") or entry["uuid"],
                    gpu_name=entry.get("snapshot_gpu_alias_name")
                    or entry.get("gpu_type_name")
                    or entry.get("gpu_spec_uuid")
                    or "未知型号",
                    gpu_count=entry.get("req_gpu_amount", 0),
                    region_name=entry.get("region_name") or entry.get("region_sign", ""),
                    status=entry.get("status", "unknown"),
                    start_mode=entry.get("start_mode") or "unknown",
                    billing=entry.get("charge_type", ""),
                    created_at=entry.get("created_at", ""),
                )
            )
        total = data.get("result_total")
        if total is None:
            total = max(len(rows), int(data.get("max_page", 1)) * page_size)
        return AccountInstancePage(items=rows, total=total, page=page, page_size=page_size)

    async def power_on(self, instance_id):
        payload = {"instance_uuid": instance_id}
        # 普通官网的默认启动接口即为有卡模式；Pro 明确传 gpu。
        if self.pro:
            payload["payload"] = "gpu"
        await self.request("power_on", payload, mutation=True)

    async def power_off(self, instance_id):
        await self.request("power_off", {"instance_uuid": instance_id}, mutation=True)
