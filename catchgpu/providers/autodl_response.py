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
    # 平台有时统一返回 BadRequest，具体业务原因放在 msg；只分类，不回显原始内容。
    raw_message = body.get("msg")
    message = raw_message[:2000].lower() if isinstance(raw_message, str) else ""
    if any(word in lower for word in ("authoriz", "token", "login")):
        raise AuthenticationError("AutoDL 登录已失效，请重新登录或更新开发者 Token")
    if lower in {"torecharge", "insufficientbalance", "balancenotenough"}:
        raise ProviderError(f"AutoDL 要求核对账户可用余额或该实例的开机最低余额条件（{code}）")
    if lower == "torealname":
        raise ProviderError(f"AutoDL 要求当前账号完成实名认证（{code}）")
    stock_reason = re.search(
        r"(?:gpu|显卡|库存|资源|卡数|空闲卡).{0,24}(?:不足|不够|不充足|已满|不可用|不空闲)"
        r"|(?:没有|暂无|无可用|无空闲).{0,24}(?:gpu|显卡|库存|资源|空闲卡)"
        r"|(?:no|not enough|insufficient).{0,20}(?:gpu|stock|resource)"
        r"|(?:gpu|stock|resource).{0,20}(?:not enough|insufficient|not available|unavailable)",
        message,
    )
    if (
        any(
            word in lower
            for word in (
                "stock",
                "notenoughgpu",
                "gpuisnotenough",
                "gpuisnotidle",
                "machineoffline",
            )
        )
        or ("gpu" in lower and "enough" in lower and "not" in lower)
        or stock_reason
    ):
        raise OutOfStock(f"库存不足或主机已不可用（{code}）")
    if lower == "badrequest":
        if re.search(r"参数|字段|param|payload|uuid|invalid.argument|请求数据", message):
            raise UnsupportedError(
                "AutoDL 拒绝请求（BadRequest）：请求参数不符合平台接口要求，请检查适配器或实例状态"
            )
        if re.search(r"到期|租期|expired", message):
            raise UnsupportedError(
                "AutoDL 拒绝请求（BadRequest）：实例计费周期已到期，请在官网核对状态"
            )
        raise UnsupportedError(
            "AutoDL 拒绝请求（BadRequest）：当前实例状态或请求条件不符合要求，尚未识别具体原因，请在官网核对；不能据此判断为余额或实名认证问题"
        )
    raise ProviderError(f"AutoDL 拒绝请求（{code}），请在官网核对该操作的具体原因")
