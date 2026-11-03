"""验证手动登录与工具连接分开执行，不自动操作安全认证页面。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from catchgpu.providers.base import AuthenticationError, ProviderError
from catchgpu.providers.browser_launcher import NativeBrowser, NativeBrowserLauncher, local_endpoint
from catchgpu.providers.browser_session import BrowserSession


def chrome():
    return NativeBrowser("chrome", "Google Chrome", Path("chrome.exe"))


def request(url="https://www.autodl.com/api/v1/instance", token="test-token"):
    return SimpleNamespace(url=url, headers={"authorization": token, "appversion": "test"})


async def test_open_only_launches_native_browser_without_attaching(tmp_path, monkeypatch):
    session = BrowserSession(tmp_path)
    opened = []

    async def launch(browser_id):
        opened.append(browser_id)
        session.launcher.browser = chrome()
        session.launcher.endpoint = "http://127.0.0.1:12345"
        return "Google Chrome"

    monkeypatch.setattr(session.launcher, "open", launch)
    assert await session.open_login("chrome") == "Google Chrome"
    assert opened == ["chrome"]
    assert session.playwright is None
    assert session.context is None
    assert not session.connected
    assert session.phase == "waiting"
    assert session.info()["can_connect"]


def test_native_launch_uses_isolated_profile_and_loopback(tmp_path):
    launcher = NativeBrowserLauncher(tmp_path)
    command = launcher.command(chrome(), tmp_path / "dedicated", 12345)
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=12345" in command
    assert f"--user-data-dir={(tmp_path / 'dedicated').resolve()}" in command
    assert "--enable-automation" not in command
    assert not any("disable-blink" in argument or "stealth" in argument for argument in command)


async def test_existing_native_window_can_be_reused_after_restart(tmp_path, monkeypatch):
    import catchgpu.providers.browser_launcher as module

    launcher = NativeBrowserLauncher(tmp_path)
    profile = tmp_path / "native/chrome"
    profile.mkdir(parents=True)
    (profile / "connection.json").write_text(json.dumps({"port": 12345}))
    monkeypatch.setattr(module, "installed_browsers", lambda: [chrome()])
    monkeypatch.setattr(launcher, "available", AsyncMock(return_value=True))
    monkeypatch.setattr(
        module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("不应重复启动浏览器")
    )
    assert await launcher.open("chrome") == "Google Chrome"
    assert launcher.endpoint == "http://127.0.0.1:12345"


async def test_missing_browser_has_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr("catchgpu.providers.browser_launcher.installed_browsers", lambda: [])
    with pytest.raises(ProviderError, match="安装 Google Chrome"):
        await NativeBrowserLauncher(tmp_path).open()


@pytest.mark.parametrize("port", [0, -1, 65536, "12345", True])
def test_invalid_remembered_port_rejected(port):
    with pytest.raises(ValueError):
        local_endpoint(port)


def test_captured_header_alone_does_not_mean_authenticated(tmp_path):
    session = BrowserSession(tmp_path)
    session.context = object()
    session.observe(request())
    assert session.authorization == "test-token"
    assert not session.connected


@pytest.mark.parametrize(
    "url",
    [
        "http://www.autodl.com/api/v1/instance",
        "https://unrelated.example/api/v1/instance",
        "https://www.autodl.com.attacker.example/api/v1/instance",
        "https://www.autodl.com/home",
    ],
)
def test_headers_outside_autodl_api_are_ignored(tmp_path, url):
    session = BrowserSession(tmp_path)
    session.observe(request(url))
    assert not session.authorization


class FakePage:
    def __init__(self, context, url):
        self.context = context
        self.url = url
        self.reloads = 0

    def expect_request(self, predicate, **kwargs):
        class Expected:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        return Expected()

    async def reload(self, **kwargs):
        self.reloads += 1
        self.context.listeners["request"](request())


class FakeContext:
    def __init__(self, url, code):
        self.listeners = {}
        self.pages = [FakePage(self, url)]
        response = SimpleNamespace(
            status=200, ok=True, json=AsyncMock(return_value={"code": code, "data": {"list": []}})
        )
        self.request = SimpleNamespace(fetch=AsyncMock(return_value=response))

    def on(self, name, callback):
        self.listeners[name] = callback

    def remove_listener(self, name, callback):
        self.listeners.pop(name, None)


def runtime_for(monkeypatch, context):
    import playwright.async_api

    browser = SimpleNamespace(
        contexts=[context], on=lambda *args: None, remove_listener=lambda *args: None
    )
    runtime = SimpleNamespace(
        stop=AsyncMock(), chromium=SimpleNamespace(connect_over_cdp=AsyncMock(return_value=browser))
    )
    monkeypatch.setattr(
        playwright.async_api,
        "async_playwright",
        lambda: SimpleNamespace(start=AsyncMock(return_value=runtime)),
    )
    return runtime


async def test_confirmation_validates_account_before_connected(tmp_path, monkeypatch):
    session = BrowserSession(tmp_path)
    session.launcher.browser, session.launcher.endpoint = chrome(), "http://127.0.0.1:12345"
    monkeypatch.setattr(session.launcher, "available", AsyncMock(return_value=True))
    context = FakeContext("https://www.autodl.com/console/instance/list", "Success")
    runtime = runtime_for(monkeypatch, context)
    await session.connect_login()
    assert session.connected
    assert session.phase == "connected"
    assert context.pages[0].reloads == 1
    assert context.request.fetch.await_args.args[0] == "https://www.autodl.com/api/v1/instance"
    await session.close()
    runtime.stop.assert_awaited_once()


async def test_login_page_is_never_reloaded_or_operated(tmp_path, monkeypatch):
    session = BrowserSession(tmp_path)
    session.launcher.browser, session.launcher.endpoint = chrome(), "http://127.0.0.1:12345"
    monkeypatch.setattr(session.launcher, "available", AsyncMock(return_value=True))
    context = FakeContext("https://www.autodl.com/login", "Success")
    runtime_for(monkeypatch, context)
    with pytest.raises(AuthenticationError, match="手动完成登录"):
        await session.connect_login()
    assert context.pages[0].reloads == 0
    context.request.fetch.assert_not_awaited()
    assert not session.connected
    assert session.phase == "waiting"


async def test_invalid_captured_token_does_not_become_connected(tmp_path, monkeypatch):
    session = BrowserSession(tmp_path)
    session.launcher.browser, session.launcher.endpoint = chrome(), "http://127.0.0.1:12345"
    monkeypatch.setattr(session.launcher, "available", AsyncMock(return_value=True))
    context = FakeContext("https://www.autodl.com/console/instance/list", "AuthorizeFailed")
    runtime_for(monkeypatch, context)
    with pytest.raises(AuthenticationError):
        await session.connect_login()
    assert not session.connected
    assert not session.authorization
    assert session.phase == "waiting"


async def test_closed_native_window_requires_reopening(tmp_path, monkeypatch):
    session = BrowserSession(tmp_path)
    session.launcher.endpoint = "http://127.0.0.1:12345"
    monkeypatch.setattr(session.launcher, "available", AsyncMock(return_value=False))
    with pytest.raises(AuthenticationError, match="尚未打开或已关闭"):
        await session.connect_login()
    assert session.info()["state"] == "idle"
    assert not session.info()["can_connect"]


async def test_find_open_recovers_own_window_after_service_restart(tmp_path, monkeypatch):
    launcher = NativeBrowserLauncher(tmp_path)
    profile = tmp_path / "native/chrome"
    profile.mkdir(parents=True)
    (profile / "connection.json").write_text(json.dumps({"port": 12345}))
    monkeypatch.setattr(
        "catchgpu.providers.browser_launcher.installed_browsers", lambda: [chrome()]
    )
    monkeypatch.setattr(launcher, "available", AsyncMock(return_value=True))
    assert await launcher.find_open()
    assert launcher.endpoint == "http://127.0.0.1:12345"
    assert launcher.browser.id == "chrome"


async def test_multiple_open_windows_require_explicit_choice(tmp_path, monkeypatch):
    launcher = NativeBrowserLauncher(tmp_path)
    browsers = [chrome(), NativeBrowser("edge", "Microsoft Edge", Path("msedge.exe"))]
    for index, browser in enumerate(browsers):
        profile = tmp_path / "native" / browser.id
        profile.mkdir(parents=True)
        (profile / "connection.json").write_text(json.dumps({"port": 12345 + index}))
    monkeypatch.setattr("catchgpu.providers.browser_launcher.installed_browsers", lambda: browsers)
    monkeypatch.setattr(launcher, "available", AsyncMock(return_value=True))
    with pytest.raises(ProviderError, match="明确选择浏览器"):
        await launcher.find_open()
    assert not launcher.endpoint


async def test_logged_in_market_page_can_connect(tmp_path, monkeypatch):
    session = BrowserSession(tmp_path)
    session.launcher.browser, session.launcher.endpoint = chrome(), "http://127.0.0.1:12345"
    monkeypatch.setattr(session.launcher, "available", AsyncMock(return_value=True))
    context = FakeContext("https://www.autodl.com/market/list", "Success")
    runtime_for(monkeypatch, context)
    await session.connect_login()
    assert session.connected
    await session.close()
