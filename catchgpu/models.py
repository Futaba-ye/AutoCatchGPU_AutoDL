"""平台无关的数据契约，金额使用十进制定点数。"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid4().hex


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GPU(Model):
    id: str
    name: str
    memory_gb: int = 0
    available: int | None = None
    total: int | None = None
    price_hour: Decimal | None = None
    region: str = ""
    region_name: str = ""
    metadata: dict = Field(default_factory=dict)


class Option(Model):
    id: str
    name: str


class RegionGroup(Option):
    region_ids: list[str]
    special: bool = False


class Catalog(Model):
    gpus: list[GPU]
    regions: list[Option] = Field(default_factory=list)
    region_groups: list[RegionGroup] = Field(default_factory=list)
    active_region: str = ""
    authenticated: bool = False
    images: list[Option] = Field(default_factory=list)
    billing: list[Option] = Field(default_factory=lambda: [Option(id="payg", name="按量计费")])
    fetched_at: str = Field(default_factory=now)
    notices: list[str] = Field(default_factory=list)
    demo: bool = False


class ResourceOffer(Model):
    id: str
    gpu_id: str
    gpu_name: str
    region_name: str = ""
    memory_gb: int = 0
    cpu: str = "—"
    ram: str = "—"
    available: int | None = None
    total: int | None = None
    price_hour: Decimal | None = None
    price_source: Literal["account", "public", "demo", "unknown"] = "unknown"


class ResourcePage(Model):
    gpu_id: str
    offers: list[ResourceOffer] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 10
    authenticated: bool = False
    demo: bool = False
    notices: list[str] = Field(default_factory=list)


class FavoriteInput(Model):
    provider: str = Field(min_length=1, max_length=80)
    gpu_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)


class Rule(Model):
    id: str = Field(default_factory=uid)
    gpu_id: str = Field(min_length=1, max_length=200)
    gpu_name: str = Field(min_length=1, max_length=200)
    gpu_count: int = Field(default=1, ge=1, le=12)
    instance_count: int = Field(default=1, ge=1, le=100)
    regions: list[str] = Field(default_factory=list, max_length=30)
    image_id: str = Field(min_length=1, max_length=200)
    disk_gb: int = Field(default=0, ge=0, le=500)
    billing: Literal["payg", "day", "week", "month"] = "payg"
    duration: int = Field(default=1, ge=1, le=12)
    max_price: Decimal = Field(gt=0, le=1000000, max_digits=12, decimal_places=4)
    price_scope: Literal["gpu", "instance"] = "gpu"
    include_disk: bool = False
    cuda: int = Field(default=118, ge=100, le=999)


class TaskInput(Model):
    name: str = Field(default="GPU 资源监控", min_length=1, max_length=80)
    provider: str = Field(min_length=1)
    interval: int = Field(default=30, ge=10, le=3600)
    rules: list[Rule] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_rules(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("规则编号不能重复")
        return self


class Instance(Model):
    id: str
    name: str
    rule_id: str
    gpu_name: str
    gpu_count: int
    created_at: str = Field(default_factory=now)


class Task(TaskInput):
    id: str = Field(default_factory=uid)
    status: Literal["draft", "running", "paused", "attention", "completed"] = "draft"
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    next_check_at: str | None = None
    last_error: str = ""
    checks: int = 0
    instances: list[Instance] = Field(default_factory=list)

    def completed_for(self, rule: Rule) -> int:
        return sum(instance.rule_id == rule.id for instance in self.instances)


class Quote(Model):
    resource_id: str
    gpu_hour: Decimal = Field(ge=0)
    disk_hour: Decimal | None = Field(default=None, ge=0)
    available: int | None = None
    metadata: dict = Field(default_factory=dict)

    def allowed(self, rule: Rule) -> bool:
        if rule.include_disk and self.disk_hour is None:
            return False
        total = self.gpu_hour * rule.gpu_count
        if rule.include_disk:
            total += self.disk_hour or Decimal(0)
        price = total / rule.gpu_count if rule.price_scope == "gpu" else total
        return price <= rule.max_price


class NotificationSettings(Model):
    desktop: bool = True
    speech: bool = False
