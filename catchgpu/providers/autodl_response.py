"""统一解释 AutoDL 响应，避免在日志中回显平台原始数据。"""

import re

from catchgpu.providers.base import (
    AuthenticationError,
    OutOfStock,
    ProviderError,
    UnsupportedError,
)


def unpack(body):
    if not isinstance(body, dict) or "code" not in body:
        raise UnsupportedError("AutoDL 响应格式发生变化，请检查平台适配器")
    raw_code = str(body["code"])
    code = raw_code if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", raw_code) else "UnknownError"
    if code == "Success":
        return body.get("data")
    lower = code.lower()
    if any(word in lower for word in ("authoriz", "token", "login")):
        raise AuthenticationError("AutoDL 登录已失效，请重新登录或更新开发者 Token")
    if any(
        word in lower
        for word in ("stock", "notenoughgpu", "gpuisnotenough", "gpuisnotidle", "machineoffline")
    ) or ("gpu" in lower and "enough" in lower and "not" in lower):
        raise OutOfStock(f"库存不足或主机已不可用（{code}）")
    raise ProviderError(f"AutoDL 拒绝请求（{code}），请在官网检查余额、实名认证及配置要求")
