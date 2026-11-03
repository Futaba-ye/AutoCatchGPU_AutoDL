"""两阶段网页登录：用户手动登录，明确确认后再连接并校验会话。"""

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from catchgpu.providers.autodl_response import unpack
from catchgpu.providers.base import (
    AuthenticationError,
    ProviderError,
    UncertainCreation,
    UnsupportedError,
)
from catchgpu.providers.browser_launcher import NativeBrowserLauncher


class BrowserSession:
    def __init__(self, profile: Path):
        self.launcher = NativeBrowserLauncher(profile)
        self.context = None
        self.browser = None
        self.playwright = None
        self.authorization = ""
        self.app_version = ""
        self.verified = False
        self.phase = "idle"
        self.last_error = ""
        self.revision = 0
        self.lock = asyncio.Lock()

    @property
    def connected(self):
        return self.context is not None and self.verified and bool(self.authorization)

    def info(self):
        return {
            "state": self.phase,
            "revision": self.revision,
            "name": self.launcher.browser.name if self.launcher.browser else "",
            "can_connect": bool(self.launcher.endpoint) and self.phase != "connecting",
            "error": self.last_error,
            "options": self.launcher.options(),
        }

    def observe(self, request):
        parsed = urlparse(request.url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.autodl.com"
            or not parsed.path.startswith("/api/")
        ):
            return
        headers = request.headers
        if headers.get("authorization"):
            self.authorization = headers["authorization"]
        if headers.get("appversion"):
            self.app_version = headers["appversion"]

    async def open_login(self, browser_id="auto"):
        async with self.lock:
            if self.connected and browser_id in ("auto", self.launcher.browser.id):
                return self.launcher.browser.name
            await self._detach()
            if self.launcher.browser and browser_id not in ("auto", self.launcher.browser.id):
                self.launcher.endpoint, self.launcher.browser = "", None
            try:
                name = await self.launcher.open(browser_id)
                self.phase, self.last_error = "waiting", ""
                return name
            except ProviderError as exc:
                self.phase, self.last_error = "error", str(exc)
                raise

    async def connect_login(self):
        async with self.lock:
            if not self.launcher.endpoint:
                await self.launcher.find_open()
            if not await self.launcher.available():
                self.phase = "idle"
                self.launcher.endpoint = ""
                raise AuthenticationError("专用浏览器尚未打开或已关闭，请先点击“打开浏览器登录”")
            self.phase, self.last_error = "connecting", ""
            await self._detach()
            try:
                from playwright.async_api import async_playwright

                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.connect_over_cdp(
                    self.launcher.endpoint, timeout=15000
                )
                if not self.browser.contexts:
                    raise ProviderError("未找到浏览器会话，请重新打开登录窗口")
                self.context = self.browser.contexts[0]
                self.context.on("request", self.observe)
                self.context.on("close", self.disconnected)
                self.browser.on("disconnected", self.disconnected)
                pages = [page for page in self.context.pages if self.is_account_page(page.url)]
                if not pages:
                    raise AuthenticationError(
                        "尚未进入 AutoDL 控制台，请先在浏览器中手动完成登录和安全认证，再点击连接"
                    )
                page = pages[-1]
                try:
                    async with page.expect_request(self.is_authorized_request, timeout=15000):
                        await page.reload(wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    raise AuthenticationError(
                        "未能读取有效登录状态，请确认已进入控制台，再点击“登录完成，连接工具”"
                    ) from None
                await self._fetch("POST", "/api/v1/instance", {"page_index": 1, "page_size": 1})
                self.verified, self.phase = True, "connected"
                self.revision += 1
                return self.launcher.browser.name
            except Exception as exc:
                await self._detach()
                self.phase = "waiting"
                self.last_error = (
                    str(exc)
                    if isinstance(exc, ProviderError)
                    else "连接浏览器失败，请确认窗口仍然打开并已登录"
                )
                if isinstance(exc, ProviderError):
                    raise
                raise ProviderError(self.last_error) from None

    @staticmethod
    def is_account_page(url):
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "www.autodl.com"
            and (parsed.path.startswith("/console/") or parsed.path.startswith("/market/"))
        )

    def is_authorized_request(self, request):
        parsed = urlparse(request.url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "www.autodl.com"
            and parsed.path.startswith("/api/")
            and bool(request.headers.get("authorization"))
        )

    def disconnected(self, *_):
        self.revision += 1
        self.context = None
        self.verified = False
        self.authorization = ""
        self.phase = "waiting" if self.launcher.endpoint else "idle"

    async def request(self, method, path, payload=None, *, mutation=False):
        if not self.connected:
            raise AuthenticationError(
                "请在本机浏览器中完成 AutoDL 登录，再点击“登录完成，连接工具”"
            )
        try:
            return await self._fetch(method, path, payload, mutation=mutation)
        except AuthenticationError as exc:
            await self._detach()
            self.phase, self.last_error = "waiting", str(exc)
            raise

    async def _fetch(self, method, path, payload=None, *, mutation=False):
        if not self.context or not self.authorization:
            raise AuthenticationError("尚未取得 AutoDL 登录状态，请手动完成登录后重新连接")
        try:
            response = await self.context.request.fetch(
                "https://www.autodl.com" + path,
                method=method,
                data=payload,
                headers={"Authorization": self.authorization, "AppVersion": self.app_version},
                timeout=30000,
                max_redirects=0,
            )
            if response.status >= 500 and mutation:
                raise UncertainCreation("AutoDL 实例操作返回服务器错误，结果待核对")
            if response.status in (401, 403):
                raise AuthenticationError("AutoDL 网页登录已失效，请重新登录")
            if not response.ok:
                raise UnsupportedError(f"AutoDL 网页接口返回 HTTP {response.status}")
            return unpack(await response.json())
        except ProviderError:
            raise
        except Exception:
            if mutation:
                raise UncertainCreation("实例操作请求中断，可能已被平台接收，请核对结果") from None
            raise ProviderError("AutoDL 网页请求失败，请检查网络或登录窗口") from None

    async def _detach(self):
        if self.verified:
            self.revision += 1
        context, browser, runtime = self.context, self.browser, self.playwright
        self.context = self.browser = self.playwright = None
        self.authorization, self.app_version, self.verified = "", "", False
        if context:
            context.remove_listener("request", self.observe)
            context.remove_listener("close", self.disconnected)
        if browser:
            browser.remove_listener("disconnected", self.disconnected)
        if runtime:
            # 只断开连接，不关闭用户正在操作的本机浏览器窗口。
            await runtime.stop()

    async def close(self):
        await self._detach()
        self.phase = "idle"
