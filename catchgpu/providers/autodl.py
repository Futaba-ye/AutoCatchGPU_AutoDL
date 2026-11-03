"""AutoDL 普通/Pro 实例适配。网页接口集中在此，便于随平台更新。"""

import asyncio
import re
from decimal import Decimal

from catchgpu.models import (
    Catalog,
    GPU,
    Instance,
    Option,
    Quote,
    Rule,
    RegionGroup,
    ResourceOffer,
    ResourcePage,
)
from catchgpu.providers.market_data import choose_region, group_regions, machine_query
from catchgpu.providers.base import (
    AuthenticationError,
    Provider,
    ProviderError,
    UncertainCreation,
    UnsupportedError,
)
from catchgpu.providers.transport import AutoDLConnection
from catchgpu.providers.account_instances import AutoDLInstances

GIB = 1024**3
BILLING = {"payg": "payg", "day": "daily", "week": "weekly", "month": "monthly"}
HOURS = {"payg": 1, "day": 24, "week": 168, "month": 720}
BASE_IMAGES = [
    Option(id="base-image-l2t43iu6uk", name="PyTorch 2.0 / CUDA 11.8 / Ubuntu 20.04"),
    Option(id="base-image-mbr2n4urrc", name="Miniconda / CUDA 11.6 / Ubuntu 20.04"),
    Option(id="base-image-l2843iu23k", name="TensorRT 8.5.1 / CUDA 11.8 / Ubuntu 20.04"),
]
BILLING_OPTIONS = [
    Option(id=key, name=name)
    for key, name in [("payg", "按量计费"), ("day", "包日"), ("week", "包周"), ("month", "包月")]
]


def items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("list"), list):
        return data["list"]
    raise UnsupportedError("AutoDL 列表字段发生变化，请更新 providers/autodl.py")


def money(value) -> Decimal:
    result = Decimal(str(value)) / 1000
    if not result.is_finite() or result < 0:
        raise UnsupportedError("AutoDL 返回无效价格")
    return result


def preview_quote(
    data: dict, rule: Rule, resource_id: str, metadata: dict, disk_expected=False
) -> Quote:
    """平台按千分之一元计价；预付费统一折算到每小时。"""
    if not isinstance(data, dict) or not isinstance(data.get("discount_list"), list):
        raise UnsupportedError("价格明细缺失，已阻止创建")
    instance_price, disk_price = None, None
    for line in data["discount_list"]:
        product = line.get("product_type")
        if product not in ("instance", "instance_pro", "data_disk", "system_disk"):
            continue
        if "origin_price" not in line or "total_reduction" not in line:
            raise UnsupportedError("价格明细字段缺失，已阻止创建")
        value = money(Decimal(str(line["origin_price"])) - Decimal(str(line["total_reduction"])))
        if product in ("instance", "instance_pro"):
            instance_price = value
        else:
            disk_price = value
    if instance_price is None:
        raise UnsupportedError("缺少实例报价，已阻止创建")
    hours = Decimal(HOURS[rule.billing] * (rule.duration if rule.billing != "payg" else 1))
    return Quote(
        resource_id=resource_id,
        gpu_hour=instance_price / hours / rule.gpu_count,
        disk_hour=(
            disk_price / hours
            if disk_price is not None
            else (Decimal(0) if rule.disk_gb == 0 and not disk_expected else None)
        ),
        metadata=metadata,
    )


class AutoDLProvider(Provider):
    id = "autodl"
    name = "AutoDL 普通实例"

    def __init__(self, connection: AutoDLConnection):
        self.connection = connection
        self.account = AutoDLInstances(connection, pro=self.id == "autodl-pro")

    async def list_instances(self, page=1, page_size=20):
        return await self.account.list_instances(page, page_size)

    async def power_on(self, instance_id):
        await self.account.power_on(instance_id)

    async def power_off(self, instance_id):
        await self.account.power_off(instance_id)

    async def catalog(self, region=None):
        gpus, regions = await asyncio.gather(
            self.connection.public("GET", "/api/v1/machine/gpu_type"),
            self.connection.public("GET", "/api/v1/region/list"),
        )
        inventory = {}
        region_options, groups = group_regions(items(regions))
        active = choose_region(groups, region)
        authenticated = self.connection.browser.connected
        notices = [
            (
                "已连接网页登录；选择型号后显示当前地区的主机、库存与账号报价。"
                if authenticated
                else "当前展示型号目录；连接网页登录后可读取地区库存和账号报价。"
            )
        ]
        if self.connection.browser.connected:
            try:
                stock = await self.connection.browser.request(
                    "POST",
                    "/api/v1/machine/region/gpu_type",
                    {"region_sign_list": active.region_ids},
                )
                for group in items(stock):
                    for name, count in group.items():
                        inventory[name] = count
            except AuthenticationError:
                raise
            except ProviderError:
                notices.append("当前地区库存暂未同步，选择型号后仍会独立查询主机详情。")
        return Catalog(
            gpus=[
                GPU(
                    id=entry["gpu_name"],
                    name=entry["gpu_name"],
                    memory_gb=int(entry.get("gpu_memory", 0) / GIB),
                    available=inventory.get(entry["gpu_name"], {}).get("idle_gpu_num"),
                    total=inventory.get(entry["gpu_name"], {}).get("total_gpu_num"),
                )
                for entry in items(gpus)
                if not inventory or entry["gpu_name"] in inventory
            ],
            regions=region_options,
            region_groups=groups,
            active_region=active.id,
            authenticated=authenticated,
            images=BASE_IMAGES,
            billing=BILLING_OPTIONS,
            notices=notices,
        )

    async def resources(self, gpu_id, region=None, page=1, page_size=10):
        if not self.connection.browser.connected:
            raise AuthenticationError(
                "工具尚未连接网页登录；请在“连接与通知”中完成连接后查看账号资源"
            )
        regions = items(await self.connection.public("GET", "/api/v1/region/list"))
        _, groups = group_regions(regions)
        active = choose_region(groups, region)
        data = await self.connection.browser.request(
            "POST",
            "/api/v1/user/machine/list",
            machine_query(gpu_id, active.region_ids, page, page_size),
        )
        rows = items(data)
        semaphore = asyncio.Semaphore(3)

        async def load(machine):
            async with semaphore:
                machine_id = str(machine["machine_id"])
                rule = Rule(
                    gpu_id=gpu_id, gpu_name=gpu_id, image_id=BASE_IMAGES[0].id, max_price="1"
                )
                price, source = None, "unknown"
                try:
                    preview = await self.connection.browser.request(
                        "POST", "/api/v1/order/price/preview", self.price_payload(rule, machine_id)
                    )
                    price = preview_quote(preview, rule, machine_id, {}).gpu_hour
                    source = "account"
                except AuthenticationError:
                    raise
                except ProviderError:
                    sku = next(
                        (
                            sku
                            for sku in machine.get("machine_sku_info", [])
                            if sku.get("type") == "payg"
                        ),
                        None,
                    )
                    if sku and sku.get("current_price") is not None:
                        price, source = money(sku["current_price"]), "public"
                available = machine.get("gpu_idle_num")
                if machine.get("on_line") in (3, 4, 5, 6) or machine.get(
                    "max_instance_num", 1
                ) <= machine.get("binding_instance_num", 0):
                    available = 0
                return ResourceOffer(
                    id=machine_id,
                    gpu_id=gpu_id,
                    gpu_name=gpu_id,
                    region_name=machine.get("region_name", active.name),
                    memory_gb=int(machine.get("gpu_memory", 0) / GIB),
                    cpu=(
                        f"{machine['cpu_per_gpu']} 核 / 卡"
                        if machine.get("cpu_per_gpu") is not None
                        else "—"
                    ),
                    ram=(
                        f"{machine['mem_per_gpu'] / GIB:g} GB / 卡"
                        if machine.get("mem_per_gpu") is not None
                        else "—"
                    ),
                    available=available,
                    total=machine.get("gpu_number"),
                    price_hour=price,
                    price_source=source,
                )

        offers = await asyncio.gather(*(load(machine) for machine in rows))
        return ResourcePage(
            gpu_id=gpu_id,
            offers=offers,
            total=int(data.get("result_total", len(rows))) if isinstance(data, dict) else len(rows),
            page=page,
            page_size=page_size,
            authenticated=True,
            notices=["显示每卡按量时价；公开参考价会单独标注，创建前会按限制表单再次校验。"],
        )

    async def validate(self, rule):
        if not self.connection.browser.connected:
            raise AuthenticationError("普通实例需要手动登录 AutoDL")

    def price_payload(self, rule, resource_id):
        return {
            "charge_type": BILLING[rule.billing],
            "coupon_id_list": [],
            "duration": rule.duration if rule.billing != "payg" else 1,
            "expand_data_disk": rule.disk_gb * GIB,
            "machine_id": resource_id,
            "num": rule.gpu_count,
        }

    async def quote(self, rule):
        payload = {
            "charge_type": BILLING[rule.billing],
            "region_sign": "",
            "region_sign_list": rule.regions,
            "gpu_type_name": [rule.gpu_id],
            "machine_tag_name": [],
            "gpu_idle_num": rule.gpu_count,
            "mount_net_disk": False,
            "instance_disk_size_order": "",
            "date_range": "",
            "date_from": "",
            "date_to": "",
            "page_index": 1,
            "page_size": 90,
            "pay_price_order": "asc",
            "gpu_idle_type": "",
            "default_order": False,
        }
        data = await self.connection.browser.request("POST", "/api/v1/user/machine/list", payload)
        for machine in items(data):
            if machine.get("gpu_type_name", machine.get("gpu_name", rule.gpu_id)) != rule.gpu_id:
                continue
            if rule.regions and machine.get("region_sign") not in rule.regions:
                continue
            if (
                machine.get("on_line") in (3, 4, 5, 6)
                or machine.get("gpu_idle_num", 0) < rule.gpu_count
            ):
                continue
            if machine.get("max_instance_num", 1) <= machine.get("binding_instance_num", 0):
                continue
            cuda = machine.get("highest_cuda_version")
            if cuda and Decimal(str(cuda)) * 10 < rule.cuda:
                continue
            resource_id = str(machine["machine_id"])
            price_payload = self.price_payload(rule, resource_id)
            price = await self.connection.browser.request(
                "POST", "/api/v1/order/price/preview", price_payload
            )
            quote = preview_quote(price, rule, resource_id, {"price_info": price_payload})
            if quote.allowed(rule):
                return quote
        return None

    def web_payload(self, rule, quote, name):
        image_field = (
            "private_image_uuid" if rule.image_id.startswith("image-") else "base_image_uuid"
        )
        return {
            "instance_info": {
                "machine_id": quote.resource_id,
                "charge_type": BILLING[rule.billing],
                "req_gpu_amount": rule.gpu_count,
                "instance_name": name,
                "base_image_uuid": "",
                "private_image_uuid": "",
                image_field: rule.image_id,
                "reproduction_uuid": "",
                "reproduction_id": 0,
                "cg_application_uuid": "",
                "cg_application_info": {
                    "image_name": "",
                    "current_version_id": 0,
                    "current_version": "",
                    "image_id": 0,
                },
                "expand_data_disk": rule.disk_gb * GIB,
            },
            "price_info": quote.metadata["price_info"],
        }

    async def create(self, rule, quote, name):
        payload = self.web_payload(rule, quote, name)
        suffix = "payg" if rule.billing == "payg" else "prepay"
        prefix = (
            "/api/v1/order/instance/pro/create/"
            if self.id == "autodl-pro"
            else "/api/v1/order/instance/create/"
        )
        result = await self.connection.browser.request(
            "POST", prefix + suffix, payload, mutation=True
        )
        if rule.billing != "payg":
            if not isinstance(result, dict) or not result.get("uuid"):
                raise UncertainCreation("预付费订单已提交，但没有返回订单编号，请到 AutoDL 核对")
            pay_method = "POST" if self.id == "autodl-pro" else "PUT"
            try:
                result = await self.connection.browser.request(
                    pay_method, "/api/v1/order/pay", {"order_uuid": result["uuid"]}, mutation=True
                )
            except ProviderError:
                raise UncertainCreation(
                    "预付费订单已生成，但支付结果未确认；请到 AutoDL 检查订单与实例后核对"
                ) from None
        instance_id = result if isinstance(result, str) else (result or {}).get("instance_uuid")
        if not instance_id:
            raise UncertainCreation("平台已接收创建请求，但未返回实例编号，请核对结果")
        return Instance(
            id=instance_id,
            name=name,
            rule_id=rule.id,
            gpu_name=rule.gpu_name,
            gpu_count=rule.gpu_count,
        )

    async def find_instance(self, name, rule):
        path = "/api/v1/instance/pro" if self.id == "autodl-pro" else "/api/v1/instance"
        page = 1
        while True:
            data = await self.connection.browser.request(
                "POST", path, {"page_index": page, "page_size": 100}
            )
            for instance in items(data):
                if instance.get("name") == name:
                    return Instance(
                        id=instance["uuid"],
                        name=name,
                        rule_id=rule.id,
                        gpu_name=rule.gpu_name,
                        gpu_count=rule.gpu_count,
                    )
            if not isinstance(data, dict) or page >= data.get("max_page", 1):
                return None
            page += 1


class AutoDLProProvider(AutoDLProvider):
    id = "autodl-pro"
    name = "AutoDL Pro 实例"

    async def gpu_types(self, regions):
        request = (
            self.connection.browser.request
            if self.connection.browser.connected
            else self.connection.public
        )
        return items(
            await request(
                "POST",
                "/api/v1/machine_pro/gpu_type",
                {"data_center_list": regions or ["westDC3", "beijingDC2"]},
            )
        )

    async def specs(self, gpu_name, billing="payg"):
        return items(
            await self.connection.public(
                "POST",
                "/api/v1/machine_pro/gpu_spec_config",
                {"gpu_name": gpu_name, "charge_type": billing},
            )
        )

    async def catalog(self, region=None):
        groups = [
            RegionGroup(id="westDC3", name="西北B区", region_ids=["westDC3"]),
            RegionGroup(id="beijingDC2", name="北京B区", region_ids=["beijingDC2"]),
        ]
        active = choose_region(groups, region)
        types = await self.gpu_types(active.region_ids)
        semaphore = asyncio.Semaphore(3)

        async def load(entry):
            async with semaphore:
                specs = await self.specs(entry["gpu_type"])
            result = []
            for spec in specs:
                sku = next(
                    (
                        sku
                        for sku in spec.get("gpu_spec_config_sku_info", [])
                        if sku["type"] == "payg"
                    ),
                    None,
                )
                memory = re.search(r"(\d+)G", spec["name"])
                result.append(
                    GPU(
                        id=spec["uuid"],
                        name=spec["name"] + " · " + spec["gpu_spec_name"],
                        memory_gb=int(memory[1]) if memory else 0,
                        available=entry.get("gpu_idle_num"),
                        total=entry.get("gpu_total_number"),
                        price_hour=money(sku["current_price"]) if sku else None,
                        metadata={
                            "gpu_type": entry["gpu_type"],
                            "spec_id": spec["id"],
                            "cpu": f"{spec.get('cpu_num_from', '—')}～{spec.get('cpu_num_to', '—')} 核 / 卡",
                            "ram": f"{spec.get('memory_size_from', 0) / GIB:g}～{spec.get('memory_size_to', 0) / GIB:g} GB / 卡",
                        },
                    )
                )
            return result

        gpu_groups = await asyncio.gather(*(load(entry) for entry in types))
        images = list(BASE_IMAGES)
        notices = [
            "库存与规格实时读取；显示价格为公开每卡时价，创建前再次校验。",
            "Pro 磁盘填写额外系统盘容量；基础系统盘 30 GB 的费用在包含磁盘时计入。",
        ]
        if not self.connection.browser.connected:
            notices.append(
                "当前读取公开库存；若官网有库存但这里为零，请手动登录后刷新，读取账号可见库存。"
            )
        if self.connection.token:
            try:
                page = 1
                while True:
                    data = await self.connection.api(
                        "POST",
                        "/api/v1/dev/instance/pro/image/private/list",
                        {"page_index": page, "page_size": 100},
                    )
                    images += [
                        Option(id=entry["image_uuid"], name=entry["name"])
                        for entry in items(data)
                        if entry.get("status") == "finished"
                    ]
                    if page >= data.get("max_page", 1):
                        break
                    page += 1
            except ProviderError:
                notices.append("私有镜像未能同步，请测试连接或手动填写镜像 UUID。")
        return Catalog(
            gpus=[gpu for group in gpu_groups for gpu in group],
            regions=[Option(id="westDC3", name="西北区"), Option(id="beijingDC2", name="北京区")],
            region_groups=groups,
            active_region=active.id,
            authenticated=self.connection.browser.connected,
            images=images,
            billing=BILLING_OPTIONS,
            notices=notices,
        )

    async def resources(self, gpu_id, region=None, page=1, page_size=10):
        catalog = await self.catalog(region)
        gpu = next((gpu for gpu in catalog.gpus if gpu.id == gpu_id), None)
        if gpu is None:
            raise UnsupportedError("当前地区没有该 Pro 规格，请重新选择型号")
        price, source = gpu.price_hour, "public"
        if self.connection.browser.connected:
            rule = Rule(gpu_id=gpu_id, gpu_name=gpu.name, image_id=BASE_IMAGES[0].id, max_price="1")
            price_info = {
                "charge_type": "payg",
                "duration": 1,
                "num": 1,
                "expand_system_disk": 30 * GIB,
                "gpu_spec_info": {"id": gpu.metadata["spec_id"]},
                "coupon_id_list": [],
            }
            preview = await self.connection.browser.request(
                "POST", "/api/v1/order/price/preview/pro", price_info
            )
            price, source = (
                preview_quote(preview, rule, gpu_id, {}, disk_expected=True).gpu_hour,
                "account",
            )
        offer = ResourceOffer(
            id=gpu.id,
            gpu_id=gpu.id,
            gpu_name=gpu.name,
            region_name=choose_region(catalog.region_groups, catalog.active_region).name,
            memory_gb=gpu.memory_gb,
            cpu=gpu.metadata.get("cpu", "—"),
            ram=gpu.metadata.get("ram", "—"),
            available=gpu.available,
            total=gpu.total,
            price_hour=price,
            price_source=source,
        )
        return ResourcePage(
            gpu_id=gpu_id,
            offers=[offer] if page == 1 else [],
            total=1,
            page=page,
            page_size=page_size,
            authenticated=catalog.authenticated,
            notices=["Pro 为规格池库存，由平台在创建时调度具体主机。"],
        )

    async def validate(self, rule):
        if rule.gpu_count > 4:
            raise UnsupportedError("AutoDL Pro 每实例支持 1～4 张 GPU")
        if rule.billing == "payg" and self.connection.token:
            return
        if not self.connection.browser.connected:
            raise AuthenticationError(
                "Pro 按量计费需开发者 Token 或手动登录；包日/包周/包月需手动登录"
            )

    async def quote(self, rule):
        # 使用实时目录查找规格，不固定 GPU 型号；新型号随官网目录出现。
        gpu_types = await self.gpu_types(rule.regions)
        for entry in gpu_types:
            if entry.get("gpu_idle_num", 0) < rule.gpu_count:
                continue
            specs = await self.specs(entry["gpu_type"], BILLING[rule.billing])
            spec = next((spec for spec in specs if spec["uuid"] == rule.gpu_id), None)
            if not spec:
                continue
            if not spec.get(
                {"payg": "payg", "day": "pay_daily", "week": "pay_weekly", "month": "pay_monthly"}[
                    rule.billing
                ]
            ):
                raise UnsupportedError("该 Pro 规格不支持所选计费方式")
            price_info = {
                "charge_type": BILLING[rule.billing],
                "duration": rule.duration if rule.billing != "payg" else 1,
                "num": rule.gpu_count,
                "expand_system_disk": (30 + rule.disk_gb) * GIB,
                "gpu_spec_info": {"id": spec["id"]},
                "coupon_id_list": [],
            }
            if rule.billing == "payg" and self.connection.token:
                sku = next(
                    (sku for sku in spec["gpu_spec_config_sku_info"] if sku["type"] == "payg"), None
                )
                if not sku:
                    raise UnsupportedError("Pro 公开报价缺失，已阻止创建")
                # 官方开发者 API 无价格预览，使用未打折的公开价格作为保守上界。
                disk_price = self.disk_hour(spec, rule)
                quote = Quote(
                    resource_id=spec["uuid"],
                    gpu_hour=money(sku["current_price"]),
                    disk_hour=disk_price,
                    available=entry["gpu_idle_num"],
                    metadata={"api": True},
                )
            else:
                price = await self.connection.browser.request(
                    "POST", "/api/v1/order/price/preview/pro", price_info
                )
                quote = preview_quote(
                    price, rule, spec["uuid"], {"price_info": price_info}, disk_expected=True
                )
            return quote if quote.allowed(rule) else None
        return None

    def disk_hour(self, spec, rule):
        stages = spec.get("system_disk_price_info", {}).get("stage_price", [])
        daily = next((stage for stage in stages if stage["charge_type"] == "daily"), None)
        if not daily:
            return None
        total, size = Decimal(0), (30 + rule.disk_gb) * GIB
        for stage in daily["stage_list"]:
            start, end = stage["stage_from"], stage["stage_to"]
            if size <= start:
                continue
            if stage["staged_charge_type"] == "fixed":
                total += money(stage["price"])
            elif stage["staged_charge_type"] == "payg":
                total += (
                    money(stage["price"])
                    * Decimal(min(size, end) - start if end != -1 else size - start)
                    / GIB
                )
            else:
                return None
        return total / 24

    def web_payload(self, rule, quote, name):
        result = super().web_payload(rule, quote, name)
        info = result["instance_info"]
        info.pop("machine_id")
        info.pop("expand_data_disk")
        info.update(
            {
                "machine_cuda_v": rule.cuda,
                "data_center_list": rule.regions or ["westDC3", "beijingDC2"],
                "expand_system_disk": (30 + rule.disk_gb) * GIB,
                "gpu_spec_info": quote.metadata["price_info"]["gpu_spec_info"],
            }
        )
        return result

    async def create(self, rule, quote, name):
        if quote.metadata.get("api"):
            result = await self.connection.api(
                "POST",
                "/api/v1/dev/instance/pro/create",
                {
                    "data_center_list": rule.regions or ["westDC3", "beijingDC2"],
                    "req_gpu_amount": rule.gpu_count,
                    "expand_system_disk_by_gb": rule.disk_gb,
                    "gpu_spec_uuid": rule.gpu_id,
                    "image_uuid": rule.image_id,
                    "cuda_v_from": rule.cuda,
                    "instance_name": name,
                },
                mutation=True,
            )
            if not isinstance(result, str) or not result:
                raise UncertainCreation("官方 API 未返回实例编号，请核对创建结果")
            return Instance(
                id=result,
                name=name,
                rule_id=rule.id,
                gpu_name=rule.gpu_name,
                gpu_count=rule.gpu_count,
            )
        return await super().create(rule, quote, name)

    async def find_instance(self, name, rule):
        if not self.connection.token or rule.billing != "payg":
            return await super().find_instance(name, rule)
        page = 1
        while True:
            data = await self.connection.api(
                "POST", "/api/v1/dev/instance/pro/list", {"page_index": page, "page_size": 100}
            )
            for instance in items(data):
                if instance.get("name") == name:
                    return Instance(
                        id=instance["uuid"],
                        name=name,
                        rule_id=rule.id,
                        gpu_name=rule.gpu_name,
                        gpu_count=rule.gpu_count,
                    )
            if page >= data.get("max_page", 1):
                return None
            page += 1
