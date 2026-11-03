"""测试只使用隔离目录和模拟平台，不创建真实实例。"""

import pytest

from catchgpu.models import Rule
from catchgpu.storage import Store


@pytest.fixture
def rule():
    return Rule(gpu_id="demo-4090", gpu_name="RTX 4090", image_id="demo-pytorch", max_price="10")


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "test.db")
    yield result
    result.close()


class SilentNotifier:
    def __init__(self, store):
        self.store = store
        self.messages = []

    async def success(self, task_id, message):
        self.messages.append(message)
        self.store.event(task_id, "success", message, "instance_created")
