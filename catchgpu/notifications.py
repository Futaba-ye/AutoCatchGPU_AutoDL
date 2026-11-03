"""通知渠道独立于调度；Windows 原生通知不依赖网页保持打开。"""

import asyncio
import base64
import platform
import subprocess
from xml.etree.ElementTree import Element, SubElement, tostring

from catchgpu.storage import Store


class Notifier:
    def __init__(self, store: Store):
        self.store = store
        self.native_available = platform.system() == "Windows"

    async def success(self, task_id: str, message: str):
        self.store.event(task_id, "success", message, "instance_created")
        if self.store.notification_settings().desktop and self.native_available:
            try:
                await asyncio.to_thread(self._windows_toast, message)
            except Exception:
                self.store.event(
                    task_id, "warning", "Windows 通知发送失败，请检查系统通知权限；实例结果已保存"
                )

    def _windows_toast(self, message: str):
        toast = Element("toast")
        binding = SubElement(SubElement(toast, "visual"), "binding", template="ToastGeneric")
        SubElement(binding, "text").text = "CatchGPU · 实例创建成功"
        SubElement(binding, "text").text = message
        xml = base64.b64encode(tostring(toast, encoding="utf-8")).decode("ascii")
        script = (
            """
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$toastXml = New-Object Windows.Data.Xml.Dom.XmlDocument
$toastXml.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"""
            + xml
            + r"""')))
$toast = [Windows.UI.Notifications.ToastNotification]::new($toastXml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.WindowsPowerShell\v1.0\powershell.exe').Show($toast)
"""
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "-"],
            input=script,
            text=True,
            capture_output=True,
            timeout=15,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
