"""AutoDL 市场的地区分组和只读资源筛选。"""

from catchgpu.models import Option, RegionGroup
from catchgpu.providers.base import UnsupportedError


def group_regions(entries: list[dict]):
    options, grouped = [], {}
    for entry in entries:
        if entry.get("visible", "all") != "all" or entry.get("used_for") == "deployment":
            continue
        sign, name = entry["region_sign"], entry["region_name"]
        options.append(Option(id=sign, name=f"{name} · {sign}"))
        key = entry.get("data_center", sign)
        if key not in grouped:
            grouped[key] = RegionGroup(id=key, name=name, region_ids=[], special="专区" in name)
        grouped[key].region_ids.append(sign)
    return options, list(grouped.values())


def choose_region(groups: list[RegionGroup], region: str | None) -> RegionGroup:
    if not groups:
        raise UnsupportedError("平台没有返回可选地区")
    if region == "all":
        return RegionGroup(
            id="all",
            name="全部地区",
            region_ids=[value for group in groups for value in group.region_ids],
        )
    if region is None:
        return groups[0]
    for group in groups:
        if region == group.id:
            return group
        if region in group.region_ids:
            return RegionGroup(
                id=region, name=group.name, region_ids=[region], special=group.special
            )
    raise UnsupportedError("所选地区已失效，请重新选择地区")


def machine_query(gpu_id: str, region_ids: list[str], page: int, page_size: int):
    return {
        "charge_type": "payg",
        "region_sign": "",
        "region_sign_list": region_ids,
        "gpu_type_name": [gpu_id],
        "machine_tag_name": [],
        "gpu_idle_num": 0,
        "mount_net_disk": False,
        "instance_disk_size_order": "",
        "date_range": "",
        "date_from": "",
        "date_to": "",
        "page_index": page,
        "page_size": page_size,
        "pay_price_order": "asc",
        "gpu_idle_type": "",
        "default_order": False,
        "cpu_arch": [],
        "chip_corp": [],
    }
