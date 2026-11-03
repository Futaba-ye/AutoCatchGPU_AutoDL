"""运行配置，服务器部署通过环境变量覆盖。"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    access_token: str = ""

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.getenv("CATCHGPU_DATA_DIR", "data")).resolve(),
            host=os.getenv("CATCHGPU_HOST", "127.0.0.1"),
            port=int(os.getenv("CATCHGPU_PORT", "8765")),
            access_token=os.getenv("CATCHGPU_ACCESS_TOKEN", ""),
        )
