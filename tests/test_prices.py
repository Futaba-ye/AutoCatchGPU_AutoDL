"""验证价格口径、十进制边界和平台计费单位换算。"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from catchgpu.models import Quote, Rule, TaskInput
from catchgpu.providers.autodl import preview_quote
from catchgpu.providers.base import UnsupportedError


def test_per_gpu_and_instance_caps(rule):
    rule.gpu_count, rule.max_price = 2, Decimal("3")
    quote = Quote(resource_id="r", gpu_hour=Decimal("2.5"), disk_hour=Decimal("1"))
    assert quote.allowed(rule)
    rule.price_scope = "instance"
    assert not quote.allowed(rule)
    rule.max_price = Decimal("5")
    assert quote.allowed(rule)
    rule.include_disk = True
    assert not quote.allowed(rule)
    rule.max_price = Decimal("6")
    assert quote.allowed(rule)


def test_disk_cost_is_shared_across_cards(rule):
    rule.gpu_count, rule.max_price, rule.include_disk = 2, Decimal("3"), True
    assert Quote(resource_id="r", gpu_hour=Decimal("2.5"), disk_hour=Decimal("1")).allowed(rule)


def test_missing_disk_price_never_accepted_when_required(rule):
    rule.include_disk = True
    assert not Quote(resource_id="r", gpu_hour=Decimal("1")).allowed(rule)


def test_decimal_boundary_is_exact(rule):
    rule.max_price = Decimal("0.3")
    quote = Quote(resource_id="r", gpu_hour=Decimal("0.1") + Decimal("0.2"))
    assert quote.allowed(rule)


def test_prepaid_quote_converts_to_hourly_per_card(rule):
    rule.billing, rule.duration, rule.gpu_count = "week", 2, 2
    result = preview_quote(
        {
            "discount_list": [
                {"product_type": "instance", "origin_price": 6720000, "total_reduction": 0},
                {"product_type": "data_disk", "origin_price": 336000, "total_reduction": 0},
            ]
        },
        rule,
        "machine",
        {},
    )
    assert result.gpu_hour == Decimal("10")
    assert result.disk_hour == Decimal("1")


def test_pro_base_disk_missing_blocks_inclusive_cap(rule):
    result = preview_quote(
        {
            "discount_list": [
                {"product_type": "instance_pro", "origin_price": 1000, "total_reduction": 0}
            ]
        },
        rule,
        "p",
        {},
        disk_expected=True,
    )
    rule.include_disk = True
    assert not result.allowed(rule)


def test_missing_price_fails_closed(rule):
    with pytest.raises(UnsupportedError):
        preview_quote({"pay_price": 100}, rule, "machine", {})


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "1000001"])
def test_invalid_price_rejected(rule, value):
    data = rule.model_dump()
    data["max_price"] = value
    with pytest.raises(ValidationError):
        Rule(**data)


def test_duplicate_rule_ids_rejected(rule):
    with pytest.raises(ValidationError):
        TaskInput(provider="demo", rules=[rule, rule])
