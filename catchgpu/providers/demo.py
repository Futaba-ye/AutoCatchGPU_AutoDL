"""明确隔离的演示平台，不访问 AutoDL，也不产生费用。"""

from decimal import Decimal

from catchgpu.models import (
    Catalog,
    GPU,
    Instance,
    Option,
    Quote,
    Rule,
    uid,
    RegionGroup,
    ResourceOffer,
    ResourcePage,
)
from catchgpu.providers.market_data import choose_region
from catchgpu.providers.base import Provider


class DemoProvider(Provider):
    id = "demo"
    name = "演示环境"
    demo = True

    def __init__(self):
        self.instances: dict[str, Instance] = {}

    async def catalog(self, region=None):
        groups = [
            RegionGroup(id="demo-east", name="华东 · 演示", region_ids=["demo-east"]),
            RegionGroup(id="demo-north", name="华北 · 演示", region_ids=["demo-north"]),
        ]
        active = choose_region(groups, region)
        return Catalog(
            demo=True,
            authenticated=True,
            region_groups=groups,
            active_region=active.id,
            gpus=[
                GPU(
                    id=key,
                    name=name,
                    memory_gb=memory,
                    available=8,
                    total=16,
                    price_hour=Decimal(price),
                )
                for key, name, memory, price in [
                    ("demo-4090", "RTX 4090", 24, "2.38"),
                    ("demo-5090", "RTX 5090", 32, "3.80"),
                    ("demo-a100", "A100", 80, "6.80"),
                    ("demo-h800", "H800", 80, "9.80"),
                    ("demo-pro6000", "RTX PRO 6000", 96, "5.20"),
                    ("demo-3090", "RTX 3090", 24, "1.28"),
                ]
            ],
            regions=[
                Option(id="demo-east", name="华东 · 演示"),
                Option(id="demo-north", name="华北 · 演示"),
            ],
            images=[
                Option(id="demo-pytorch", name="PyTorch 2.6 / CUDA 12.4 · 演示"),
                Option(id="demo-ubuntu", name="Ubuntu 22.04 · 演示"),
            ],
            billing=[
                Option(id="payg", name="按量计费"),
                Option(id="day", name="包日"),
                Option(id="week", name="包周"),
                Option(id="month", name="包月"),
            ],
            notices=["以下为模拟库存。演示任务不会连接 AutoDL 或创建真实实例。"],
        )

    async def resources(self, gpu_id, region=None, page=1, page_size=10):
        catalog = await self.catalog(region)
        gpu = next((gpu for gpu in catalog.gpus if gpu.id == gpu_id), None)
        if gpu is None:
            return ResourcePage(gpu_id=gpu_id, demo=True)
        offers = [
            ResourceOffer(
                id=f"demo-machine-{index}",
                gpu_id=gpu.id,
                gpu_name=gpu.name,
                region_name=choose_region(catalog.region_groups, catalog.active_region).name,
                memory_gb=gpu.memory_gb,
                cpu="12 核 / 卡",
                ram="64 GB / 卡",
                available=4,
                total=8,
                price_hour=gpu.price_hour + Decimal(index) / 10,
                price_source="demo",
            )
            for index in range(1, 4)
        ]
        return ResourcePage(
            gpu_id=gpu_id,
            offers=offers[(page - 1) * page_size : page * page_size],
            total=len(offers),
            page=page,
            page_size=page_size,
            authenticated=True,
            demo=True,
        )

    async def quote(self, rule: Rule):
        gpu = next((gpu for gpu in (await self.catalog()).gpus if gpu.id == rule.gpu_id), None)
        if not gpu:
            return None
        quote = Quote(
            resource_id=gpu.id,
            gpu_hour=gpu.price_hour,
            disk_hour=Decimal(rule.disk_gb) * Decimal("0.0002"),
            available=8,
        )
        return quote if quote.allowed(rule) else None

    async def create(self, rule, quote, name):
        instance = Instance(
            id="demo-" + uid()[:12],
            name=name,
            rule_id=rule.id,
            gpu_name=rule.gpu_name,
            gpu_count=rule.gpu_count,
        )
        self.instances[name] = instance
        return instance

    async def find_instance(self, name, rule):
        return self.instances.get(name)
