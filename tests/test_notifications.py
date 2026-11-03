"""通知失败不得触发重新创建；关闭通知后不调用系统渠道。"""

import base64
import re
from xml.etree.ElementTree import fromstring


from catchgpu.models import NotificationSettings
from catchgpu.notifications import Notifier


async def test_disabled_desktop_keeps_success_event(store, monkeypatch):
    notifier = Notifier(store)
    notifier.native_available = True
    store.set_notifications(NotificationSettings(desktop=False))
    calls = []
    monkeypatch.setattr(notifier, "_windows_toast", lambda message: calls.append(message))
    await notifier.success("task", "创建结果")
    assert not calls
    assert store.events()[0]["kind"] == "instance_created"


async def test_channel_failure_only_logs_warning(store, monkeypatch):
    notifier = Notifier(store)
    notifier.native_available = True

    def fail(message):
        raise RuntimeError("模拟系统通知失败")

    monkeypatch.setattr(notifier, "_windows_toast", fail)
    await notifier.success("task", "已创建实例")
    events = store.events()
    assert len(events) == 2
    assert events[0]["kind"] == "instance_created"
    assert events[1]["level"] == "warning"


def test_toast_xml_preserves_chinese_and_special_characters(store, monkeypatch):
    import subprocess

    captured = {}

    def capture(args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(subprocess, "run", capture)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    message = '中文实例 <GPU> & "参数"'
    Notifier(store)._windows_toast(message)
    script = captured["input"]
    assert "\v" not in script
    encoded = re.search(r"FromBase64String\('([^']+)'\)", script)[1]
    root = fromstring(base64.b64decode(encoded))
    assert root.findall("./visual/binding/text")[1].text == message
