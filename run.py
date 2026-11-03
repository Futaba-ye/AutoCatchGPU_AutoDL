"""Windows 本地入口：启动服务后自动打开网页。服务器使用 python -m catchgpu。"""

import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from catchgpu.config import Settings


def open_when_ready(url):
    for _ in range(50):
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)


if __name__ == "__main__":
    settings = Settings.from_env()
    if settings.host in ("127.0.0.1", "localhost"):
        threading.Thread(
            target=open_when_ready, args=(f"http://127.0.0.1:{settings.port}",), daemon=True
        ).start()
    uvicorn.run("catchgpu.app:app", host=settings.host, port=settings.port, workers=1, reload=False)
