"""平台无关的数据契约，金额使用十进制定点数。"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


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


STARTABLE_STATES = frozenset(
    {
        "shutdown",
        "shutdown_by_starting_error",
        "shutdown_by_running_error",
        "created",
        "oss_merged",
        "created_clone_success",
    }
)


class AccountInstance(Model):
    """账号实例的展示字段；密码、连接口令和原始响应不进入界面。"""

    id: str
    name: str
    gpu_name: str = "未知型号"
    gpu_count: int = 0
    region_name: str = ""
    status: str = "unknown"
    start_mode: str = "unknown"
    billing: str = ""
    created_at: str = ""

    @computed_field
    @property
    def can_start(self) -> bool:
        return self.status in STARTABLE_STATES and self.gpu_count > 0

    @computed_field
    @property
    def can_stop(self) -> bool:
        return self.status in {"running", "starting"}


class AccountInstancePage(Model):
    items: list[AccountInstance] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    demo: bool = False


class StartTarget(Model):
    instance_id: str = Field(min_length=1, max_length=200)
    name: str = Field(max_length=200)
    gpu_name: str = Field(max_length=200)
    gpu_count: int = Field(ge=1, le=12)
    region_name: str = Field(default="", max_length=200)


class InstanceSelection(Model):
    instance_ids: list[str] = Field(min_length=1, max_length=100)
    interval: int = Field(default=30, ge=10, le=3600)

    @model_validator(mode="after")
    def unique_instances(self):
        if len(set(self.instance_ids)) != len(self.instance_ids):
            raise ValueError("实例编号不能重复")
        if any(not value or len(value) > 200 for value in self.instance_ids):
            raise ValueError("实例编号无效")
        return self


class TaskInput(Model):
    name: str = Field(default="GPU 资源监控", min_length=1, max_length=80)
    provider: str = Field(min_length=1)
    interval: int = Field(default=30, ge=10, le=3600)
    action: Literal["create", "start"] = "create"
    rules: list[Rule] = Field(default_factory=list, max_length=100)
    start_targets: list[StartTarget] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_rules(self):
        if self.action == "create" and (not self.rules or self.start_targets):
            raise ValueError("创建任务必须填写 GPU 规则，且不能包含已有实例")
        if self.action == "start" and (not self.start_targets or self.rules):
            raise ValueError("开机任务必须选择已有实例，且不能包含创建规则")
        if len({target.instance_id for target in self.start_targets}) != len(self.start_targets):
            raise ValueError("开机目标不能重复")
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
