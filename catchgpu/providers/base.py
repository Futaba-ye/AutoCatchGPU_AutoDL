"""平台边界：库存、报价、创建、结果核对。"""

from abc import ABC, abstractmethod

from catchgpu.models import AccountInstancePage, Catalog, Instance, Quote, Rule, ResourcePage


class ProviderError(Exception):
    """已知的平台错误，可在界面中显示。"""


class AuthenticationError(ProviderError):
    """需要用户重新登录。"""


class UnsupportedError(ProviderError):
    """平台尚不支持或返回结构已变化。"""


class RequestRejected(UnsupportedError):
    """平台明确拒绝请求，保留错误类别供业务层只读核对。"""

    def __init__(self, message, *, code, reason="unknown"):
        super().__init__(message)
        self.code, self.reason = code, reason


class OutOfStock(ProviderError):
    """库存已被其他用户占用，可下轮重试。"""


class UncertainCreation(ProviderError):
    """请求可能已成功，必须核对实例后才能继续。"""


class Provider(ABC):
    id: str
    name: str
    demo: bool = False

    @abstractmethod
    async def catalog(self, region: str | None = None) -> Catalog: ...

    async def resources(
        self, gpu_id: str, region: str | None = None, page: int = 1, page_size: int = 10
    ) -> ResourcePage:
        raise UnsupportedError("该平台尚未接入资源详情")

    async def list_instances(self, page=1, page_size=20) -> AccountInstancePage:
        raise UnsupportedError("该平台尚未接入账号实例管理")

    async def selected_instances(self, instance_ids):
        """从账号列表查找目标，避免信任客户端提供的实例配置。"""
        wanted, found, page = set(instance_ids), {}, 1
        while wanted - found.keys():
            result = await self.list_instances(page, 100)
            found.update({row.id: row for row in result.items if row.id in wanted})
            if not result.items or page * result.page_size >= result.total:
                break
            page += 1
            if page > 1000:
                raise UnsupportedError("账号实例过多，请缩小管理范围后重试")
        if wanted - found.keys():
            raise ProviderError("选中的实例已不存在或不属于当前账号，请刷新实例列表")
        return found

    async def power_on(self, instance_id):
        raise UnsupportedError("该平台尚未接入有卡开机")

    async def power_off(self, instance_id):
        raise UnsupportedError("该平台尚未接入关机")

    @abstractmethod
    async def quote(self, rule: Rule) -> Quote | None: ...

    @abstractmethod
    async def create(self, rule: Rule, quote: Quote, name: str) -> Instance: ...

    @abstractmethod
    async def find_instance(self, name: str, rule: Rule) -> Instance | None: ...

    async def validate(self, rule: Rule) -> None:
        """在启动前检查平台支持的配置。"""

    async def close(self) -> None:
        """释放平台连接。"""
