"""python -m catchgpu：固定单个调度进程启动服务。"""

import multiprocessing

import uvicorn

from catchgpu.config import Settings


if __name__ == "__main__":
    multiprocessing.freeze_support()
    settings = Settings.from_env()
    uvicorn.run("catchgpu.app:app", host=settings.host, port=settings.port, workers=1, reload=False)
