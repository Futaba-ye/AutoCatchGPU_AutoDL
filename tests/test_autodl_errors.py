"""错误按平台实际原因分类，不把 BadRequest 一律归因于余额或实名。"""

import pytest

from catchgpu.providers.autodl_response import unpack
from catchgpu.providers.base import OutOfStock, ProviderError, UnsupportedError


@pytest.mark.parametrize(
    "message",
    [
        "主机 GPU 不足，无法开机",
        "当前主机空闲卡数不够",
        "暂无空闲 GPU",
        "insufficient GPU resources",
        "GPU is not available",
    ],
)
def test_bad_request_inventory_message_is_retryable(message):
    with pytest.raises(OutOfStock):
        unpack({"code": "BadRequest", "msg": message})


def test_unknown_bad_request_does_not_blame_balance_or_retry_bad_parameters():
    with pytest.raises(UnsupportedError, match="尚未识别具体原因") as caught:
        unpack({"code": "BadRequest", "msg": "未识别的业务限制"})
    assert "请在官网检查余额" not in str(caught.value)


def test_sensitive_platform_message_is_not_returned():
    with pytest.raises(UnsupportedError, match="参数") as caught:
        unpack(
            {
                "code": "BadRequest",
                "msg": "参数 payload 无效；root_password=secret-password-value；token=secret-token-value",
            }
        )
    assert "secret-" not in str(caught.value)


@pytest.mark.parametrize("code,reason", [("TORecharge", "可用余额"), ("TORealName", "实名认证")])
def test_account_hints_only_appear_for_specific_platform_codes(code, reason):
    with pytest.raises(ProviderError, match=reason):
        unpack({"code": code, "msg": "具体错误码优先于文本中的 GPU 不足关键词"})
