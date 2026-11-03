"""HTTP 接口与静态界面。启动时创建服务依赖，关闭时等待在途请求落盘。"""

import asyncio
import hmac
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, SecretStr

from catchgpu.config import Settings
from catchgpu.locking import WorkspaceLock
from catchgpu.models import Model, NotificationSettings, Task, TaskInput, FavoriteInput
from catchgpu.notifications import Notifier
from catchgpu.providers.autodl import AutoDLProProvider, AutoDLProvider
from catchgpu.providers.base import ProviderError
from catchgpu.providers.demo import DemoProvider
from catchgpu.providers.transport import AutoDLConnection
from catchgpu.scheduler import Scheduler
from catchgpu.storage import Store, TaskNotFound

WEB = Path(__file__).parent / "web"


class Credentials(Model):
    token: SecretStr = Field(max_length=4096)


class MissingConfirmation(Model):
    confirmed: bool


class BrowserLoginInput(Model):
    browser: Literal["auto", "chrome", "edge", "custom"] = "auto"


def create_app(settings: Settings | None = None, providers_override=None):
    settings = settings or Settings.from_env()
    if settings.host not in ("127.0.0.1", "localhost", "::1") and not settings.access_token:
        raise RuntimeError("服务器部署须设置 CATCHGPU_ACCESS_TOKEN")

    @asynccontextmanager
    async def lifespan(app):
        workspace_lock = WorkspaceLock(settings.data_dir)
        store = Store(settings.data_dir / "catchgpu.db")
        connection = AutoDLConnection(settings.data_dir / "browser")
        providers = providers_override or {
            provider.id: provider
            for provider in [
                DemoProvider(),
                AutoDLProvider(connection),
                AutoDLProProvider(connection),
            ]
        }
        notifier = Notifier(store)
        scheduler = Scheduler(store, providers, notifier)
        scheduler.recover()
        app.state.store, app.state.connection, app.state.providers = store, connection, providers
        app.state.scheduler, app.state.notifier = scheduler, notifier
        app.state.cache, app.state.catalog_locks = {}, {key: asyncio.Lock() for key in providers}
        try:
            yield
        finally:
            await scheduler.close()
            await connection.close()
            for provider in providers.values():
                await provider.close()
            store.close()
            workspace_lock.close()

    app = FastAPI(title="CatchGPU", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            if settings.access_token:
                value = request.headers.get("Authorization", "").removeprefix("Bearer ")
                if not hmac.compare_digest(value.encode(), settings.access_token.encode()):
                    return JSONResponse({"detail": "请输入工具访问密钥"}, status_code=401)
            elif urlparse("//" + request.headers.get("host", "")).hostname not in (
                "127.0.0.1",
                "localhost",
                "::1",
            ):
                return JSONResponse({"detail": "本地模式仅允许通过本机地址访问"}, status_code=403)
            origin = request.headers.get("origin")
            if (
                request.method not in ("GET", "HEAD", "OPTIONS")
                and origin
                and urlparse(origin).netloc != request.headers.get("host")
            ):
                return JSONResponse({"detail": "不允许跨站修改任务"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        request.app.state.store.event(None, "error", str(exc))
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(TaskNotFound)
    async def missing(request, exc):
        return JSONResponse({"detail": "任务或平台不存在"}, status_code=404)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        message = f"服务内部异常（{type(exc).__name__}），请检查配置或联系维护者"
        request.app.state.store.event(None, "error", message)
        return JSONResponse({"detail": message}, status_code=500)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # 不返回 Pydantic 的 input 字段，避免把 Token 回显给前端或日志。
        errors = [
            {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            {"detail": "表单内容无效，请检查必填项、数量与价格范围", "errors": errors},
            status_code=422,
        )

    @app.get("/api/state")
    async def state():
        store = app.state.store
        events = store.events(limit=200)
        return {
            "tasks": store.tasks(),
            "events": events,
            "providers": [
                {"id": provider.id, "name": provider.name, "demo": provider.demo}
                for provider in app.state.providers.values()
            ],
            "connection": {
                "api": bool(app.state.connection.token),
                "browser": app.state.connection.browser.connected,
                "browser_login": app.state.connection.browser.info(),
            },
            "notifications": store.notification_settings(),
            "favorites": store.favorites(),
            "native_notifications": app.state.notifier.native_available,
        }

    @app.get("/api/catalog/{provider_id}")
    async def catalog(
        provider_id: str, refresh: bool = False, region: str = Query("", max_length=200)
    ):
        provider = app.state.providers.get(provider_id)
        if provider is None:
            raise HTTPException(404, "平台不存在")
        async with app.state.catalog_locks[provider_id]:
            key = (provider_id, region, app.state.connection.browser.revision)
            cached = app.state.cache.get(key)
            if not refresh and cached and time.monotonic() - cached[0] < 60:
                return cached[1]
            result = await provider.catalog(region or None)
            app.state.cache[key] = (time.monotonic(), result)
            return result

    @app.get("/api/resources/{provider_id}")
    async def resources(
        provider_id: str,
        gpu_id: str = Query(min_length=1, max_length=200),
        region: str = Query("", max_length=200),
        page: int = Query(1, ge=1, le=10000),
        page_size: int = Query(10, ge=1, le=20),
    ):
        provider = app.state.providers.get(provider_id)
        if provider is None:
            raise HTTPException(404, "平台不存在")
        return await provider.resources(gpu_id, region or None, page, page_size)

    @app.put("/api/favorites")
    async def save_favorite(data: FavoriteInput):
        if data.provider not in app.state.providers:
            raise HTTPException(422, "平台不存在")
        app.state.store.save_favorite(data.provider, data.gpu_id, data.name)
        return app.state.store.favorites()

    @app.delete("/api/favorites/{provider_id}")
    async def remove_favorite(provider_id: str, gpu_id: str = Query(min_length=1, max_length=200)):
        app.state.store.remove_favorite(provider_id, gpu_id)
        return app.state.store.favorites()

    @app.put("/api/connection/token")
    async def set_token(credentials: Credentials):
        if any(task.status == "running" for task in app.state.store.tasks()):
            raise HTTPException(409, "请先暂停所有任务，再修改登录凭据")
        token = credentials.token.get_secret_value().strip()
        if any(char.isspace() for char in token):
            raise HTTPException(422, "Token 不应包含空格或换行")
        app.state.connection.token = token
        app.state.cache.clear()
        app.state.store.event(None, "info", "开发者 Token 已更新，仅保存在当前服务进程内")
        return {"configured": bool(token)}

    @app.post("/api/connection/test")
    async def test_connection():
        if app.state.connection.token:
            await app.state.connection.api("POST", "/api/v1/dev/wallet/balance", {})
            return {"detail": "开发者 Token 校验成功"}
        await app.state.connection.browser.request(
            "POST", "/api/v1/instance", {"page_index": 1, "page_size": 1}
        )
        return {"detail": "网页登录状态有效"}

    @app.post("/api/connection/login")
    async def login(data: BrowserLoginInput | None = None):
        ensure_browser_idle()
        name = await app.state.connection.browser.open_login(data.browser if data else "auto")
        app.state.store.event(
            None, "info", f"已打开 {name} 专用窗口，等待用户手动登录；尚未连接账号"
        )
        return {"detail": f"已打开 {name}。请手动登录并进入控制台，再点击“登录完成，连接工具”"}

    @app.post("/api/connection/browser/connect")
    async def connect_browser():
        ensure_browser_idle()
        name = await app.state.connection.browser.connect_login()
        app.state.cache.clear()
        app.state.store.event(None, "success", f"{name} 网页登录状态已校验，工具连接成功")
        return {"detail": "网页登录状态已校验，工具连接成功；请保留该浏览器窗口"}

    def ensure_browser_idle():
        if any(not worker.done() for worker in app.state.scheduler.workers.values()):
            raise HTTPException(409, "请先暂停所有监控任务，并等待在途请求结束后再连接登录浏览器")

    @app.post("/api/tasks", status_code=201)
    async def create_task(data: TaskInput):
        if data.provider not in app.state.providers:
            raise HTTPException(422, "请选择有效的平台")
        task = Task(**data.model_dump())
        app.state.store.save(task)
        app.state.store.event(task.id, "info", "任务已保存，可点击开始监控")
        return task

    @app.post("/api/tasks/{task_id}/start")
    async def start_task(task_id: str):
        await app.state.scheduler.start(task_id)
        return app.state.store.get(task_id)

    @app.post("/api/tasks/{task_id}/pause")
    async def pause_task(task_id: str):
        await app.state.scheduler.pause(task_id)
        return app.state.store.get(task_id)

    @app.post("/api/tasks/{task_id}/reconcile")
    async def reconcile(task_id: str):
        return await app.state.scheduler.reconcile(task_id)

    @app.get("/api/tasks/{task_id}/pending")
    async def pending(task_id: str):
        app.state.store.get(task_id)
        return app.state.store.pending(task_id)

    @app.post("/api/tasks/{task_id}/attempts/{attempt_id}/missing")
    async def confirm_missing(task_id: str, attempt_id: str, data: MissingConfirmation):
        if not data.confirmed:
            raise HTTPException(422, "必须先在 AutoDL 核实没有实例和未支付订单")
        app.state.scheduler.confirm_missing(task_id, attempt_id)
        return app.state.store.get(task_id)

    @app.get("/api/events")
    async def events(
        after: int = Query(0, ge=0), task_id: str | None = None, level: str | None = None
    ):
        return app.state.store.events(after=after, task_id=task_id, level=level)

    @app.put("/api/notifications")
    async def notifications(data: NotificationSettings):
        app.state.store.set_notifications(data)
        return data

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/")
    async def index():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


app = create_app()
