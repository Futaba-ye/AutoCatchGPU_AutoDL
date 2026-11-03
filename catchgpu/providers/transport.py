"""AutoDL 连接层。网页会话与开发者 Token 分离，凭据不会写入任务或日志。"""

import os
from pathlib import Path

import httpx

from catchgpu.providers.autodl_response import unpack
from catchgpu.providers.base import AuthenticationError, ProviderError, UncertainCreation
from catchgpu.providers.browser_session import BrowserSession


class AutoDLConnection:
    def __init__(self, profile: Path, client=None):
        self.http = client or httpx.AsyncClient(timeout=30, follow_redirects=False)
        self.browser = BrowserSession(profile)
        self.token = os.getenv("AUTODL_TOKEN", "")

    async def public(self, method, path, payload=None):
        return await self._http(method, "https://www.autodl.com" + path, payload)

    async def api(self, method, path, payload=None, *, mutation=False):
        if not self.token:
            raise AuthenticationError("请在连接设置中填写 AutoDL 开发者 Token，或使用手动登录")
        return await self._http(
            method,
            "https://api.autodl.com" + path,
            payload,
            headers={"Authorization": self.token},
            mutation=mutation,
        )

    async def _http(self, method, url, payload=None, headers=None, mutation=False):
        try:
            response = await self.http.request(method, url, json=payload, headers=headers)
            if response.status_code in (401, 403):
                raise AuthenticationError("AutoDL 凭据无效或已过期")
            if response.status_code >= 500 and mutation:
                raise UncertainCreation("AutoDL 服务端错误，创建结果待核对")
            response.raise_for_status()
            return unpack(response.json())
        except ProviderError:
            raise
        except (httpx.HTTPError, ValueError):
            if mutation:
                raise UncertainCreation("AutoDL 创建请求超时或响应异常，请核对结果") from None
            raise ProviderError("AutoDL 请求失败，请检查网络连接及平台服务状态") from None

    async def close(self):
        await self.browser.close()
        await self.http.aclose()
