"""驱动注册表：新增型号时在 DRIVERS 中追加即可。"""

from __future__ import annotations

from ..model import Identity
from .keysight_dsox2024a import DsoX2024A

DRIVERS = (DsoX2024A,)


def match_resource(address: str) -> bool:
    """VISA 地址是否可能属于某个已知驱动（不发起 IDN 探测）。"""
    return any(driver.matches_resource(address) for driver in DRIVERS)


def match_identity(identity: Identity) -> type | None:
    """按 *IDN? 身份挑选驱动类；无匹配返回 None。"""
    for driver in DRIVERS:
        if driver.matches_identity(identity):
            return driver
    return None
