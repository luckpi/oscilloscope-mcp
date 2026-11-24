"""共享模型：错误类型、仪器身份、能力描述与驱动协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ScopeError(RuntimeError):
    """与示波器交互（VISA/SCPI/解码/前置条件）失败时抛出的统一错误。"""


@dataclass(frozen=True)
class Identity:
    """*IDN? 应答解析出的仪器身份。"""

    manufacturer: str
    model: str
    serial_number: str
    firmware: str

    @classmethod
    def parse(cls, response: str) -> "Identity":
        fields = [field.strip() for field in response.strip().split(",")]
        if len(fields) != 4:
            raise ScopeError(f"无法解析 *IDN? 应答: {response!r}")
        return cls(*fields)


@dataclass(frozen=True)
class Capabilities:
    """驱动对外声明的只读能力集。"""

    analog_channels: int
    screen_format: str
    waveform_modes: tuple[str, ...]
    raw_max_points: int
    trigger_modes: tuple[str, ...]


class ScopeDriver(Protocol):
    """示波器驱动协议：所有驱动必须实现下列接口。"""

    identity: Identity
    capabilities: Capabilities
    DEFAULT_POINTS: dict

    def get_setup(self) -> dict: ...

    def get_screen(self) -> bytes: ...

    def read_waveforms(
        self, channels: list[int], mode: str, points: int | None
    ) -> tuple[bool, list]: ...

    def configure_channel(
        self,
        channel: int,
        *,
        enabled: bool | None = None,
        scale_per_div: float | None = None,
        offset: float | None = None,
        coupling: str | None = None,
        probe_attenuation: float | None = None,
        bandwidth_limit: bool | None = None,
        invert: bool | None = None,
    ) -> dict: ...

    def configure_timebase(
        self,
        *,
        mode: str | None = None,
        scale_s_per_div: float | None = None,
        position_s: float | None = None,
    ) -> dict: ...

    def configure_edge_trigger(
        self,
        *,
        source: str | None = None,
        slope: str | None = None,
        level: float | None = None,
        sweep: str | None = None,
    ) -> dict: ...

    def configure_acquisition(
        self,
        *,
        mode: str | None = None,
        average_count: int | None = None,
    ) -> dict: ...

    def control_acquisition(self, action: str) -> dict: ...
