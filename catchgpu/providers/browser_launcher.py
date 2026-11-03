"""启动本机浏览器，让用户独立完成登录后再由连接层接入。"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from catchgpu.providers.base import ProviderError

LOGIN_URL = "https://www.autodl.com/console/instance/list"


@dataclass(frozen=True)
class NativeBrowser:
    id: str
    name: str
    executable: Path


def installed_browsers() -> list[NativeBrowser]:
    """只检查安装位置，不读取个人浏览器的配置或登录资料。"""
    result = []
    custom = os.getenv("CATCHGPU_BROWSER_PATH")
    if custom and Path(custom).is_file():
        result.append(NativeBrowser("custom", "自定义 Chromium 浏览器", Path(custom).resolve()))
    candidates: dict[str, list[Path]] = {"chrome": [], "edge": []}
    if os.name == "nt":
        roots = [os.getenv(key) for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        for root in filter(None, roots):
            candidates["chrome"].append(Path(root) / "Google/Chrome/Application/chrome.exe")
            candidates["edge"].append(Path(root) / "Microsoft/Edge/Application/msedge.exe")
    elif sys.platform == "darwin":
        candidates["chrome"].append(
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        )
        candidates["edge"].append(
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
        )
    else:
        for key, names in {
            "chrome": ("google-chrome", "google-chrome-stable"),
            "edge": ("microsoft-edge", "microsoft-edge-stable"),
        }.items():
            candidates[key] += [Path(found) for name in names if (found := shutil.which(name))]
    for key, paths in candidates.items():
        if executable := next((path for path in paths if path.is_file()), None):
            result.append(
                NativeBrowser(
                    key,
                    {"chrome": "Google Chrome", "edge": "Microsoft Edge"}[key],
                    executable.resolve(),
                )
            )
    return result


def local_endpoint(port: int) -> str:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("无效的本地连接端口")
    return f"http://127.0.0.1:{port}"


class NativeBrowserLauncher:
    def __init__(self, profile: Path):
        self.root = profile / "native"
        self.endpoint = ""
        self.browser: NativeBrowser | None = None
        self.process = None

    def options(self):
        return [{"id": browser.id, "name": browser.name} for browser in installed_browsers()]

    async def find_open(self) -> bool:
        """只发现工具自己记录的专用窗口；多个窗口时由用户明确选择。"""
        found = []
        for browser in installed_browsers():
            state_file = self.root / browser.id / "connection.json"
            try:
                endpoint = local_endpoint(
                    json.loads(state_file.read_text(encoding="utf-8"))["port"]
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if await self.available(endpoint):
                found.append((browser, endpoint))
        if len(found) > 1:
            raise ProviderError(
                "发现多个已打开的专用浏览器，请在“连接与通知”中明确选择浏览器后再连接"
            )
        if not found:
            return False
        self.browser, self.endpoint = found[0]
        return True

    async def available(self, endpoint: str | None = None) -> bool:
        endpoint = endpoint or self.endpoint
        if not endpoint:
            return False
        try:
            async with httpx.AsyncClient(
                timeout=1, trust_env=False, follow_redirects=False
            ) as client:
                response = await client.get(endpoint + "/json/version")
                response.raise_for_status()
                body = response.json()
                websocket = urlparse(body.get("webSocketDebuggerUrl", ""))
                expected = urlparse(endpoint)
                return (
                    websocket.scheme == "ws"
                    and websocket.hostname == "127.0.0.1"
                    and websocket.port == expected.port
                    and websocket.path.startswith("/devtools/browser/")
                    and isinstance(body.get("Browser"), str)
                )
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    def command(self, browser: NativeBrowser, profile: Path, port: int) -> list[str]:
        return [
            str(browser.executable),
            f"--user-data-dir={profile.resolve()}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            LOGIN_URL,
        ]

    async def open(self, browser_id: str = "auto") -> str:
        choices = installed_browsers()
        browser = next(
            (item for item in choices if browser_id == "auto" or item.id == browser_id), None
        )
        if browser is None:
            raise ProviderError(
                "未找到所选浏览器。请安装 Google Chrome 或 Microsoft Edge，或设置 CATCHGPU_BROWSER_PATH 后重启工具"
            )
        profile = self.root / browser.id
        profile.mkdir(parents=True, exist_ok=True)
        state_file = profile / "connection.json"
        try:
            remembered = local_endpoint(json.loads(state_file.read_text(encoding="utf-8"))["port"])
        except (OSError, ValueError, KeyError, TypeError):
            remembered = ""
        if remembered and await self.available(remembered):
            self.endpoint, self.browser = remembered, browser
            return browser.name
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        endpoint = local_endpoint(port)
        try:
            # 用户需要看见并操作该登录窗口；不使用 Playwright 启动浏览器。
            self.process = subprocess.Popen(
                self.command(browser, profile, port),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError:
            raise ProviderError("本机浏览器启动失败，请确认安装完整并且可以正常打开") from None
        for _ in range(40):
            if await self.available(endpoint):
                state_file.write_text(json.dumps({"port": port}), encoding="utf-8")
                self.endpoint, self.browser = endpoint, browser
                return browser.name
            if self.process.poll() is not None:
                break
            await asyncio.sleep(0.2)
        raise ProviderError(
            "浏览器连接未就绪。请关闭之前由工具打开的专用窗口后重试；不要关闭你的日常浏览器"
        )
