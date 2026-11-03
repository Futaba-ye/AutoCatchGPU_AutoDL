"""平台边界：库存、报价、创建、结果核对。"""

from abc import ABC, abstractmethod

from catchgpu.models import Catalog, Instance, Quote, Rule, ResourcePage


class ProviderError(Exception):
    """已知的平台错误，可在界面中显示。"""


class AuthenticationError(ProviderError):
    """需要用户重新登录。"""


class UnsupportedError(ProviderError):
    """平台尚不支持或返回结构已变化。"""


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
