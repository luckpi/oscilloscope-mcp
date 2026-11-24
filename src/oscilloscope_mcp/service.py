"""服务层：设备选择、会话生命周期、波形导出与控制权限。"""

from __future__ import annotations

import csv
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .drivers import match_identity, match_resource
from .model import Identity, ScopeDriver, ScopeError
from .transport import ResourceProvider

_MAX_PREVIEW = 256


class ScopeService:
    """每个工具调用独立打开会话、用完即关；RLock 保证同一时刻只有一个硬件操作。"""

    def __init__(
        self,
        provider: ResourceProvider,
        *,
        resource: str | None = None,
        allow_control: bool = False,
        output_dir: Path,
    ) -> None:
        self._provider = provider
        self._resource = resource
        self._allow_control = allow_control
        self._output_dir = Path(output_dir).resolve()
        self._lock = threading.RLock()

    # ---- 连接 ----

    def _candidates(self) -> list[str]:
        return [a for a in self._provider.list_resources() if match_resource(a)]

    def _select(self) -> str:
        if self._resource is not None:
            return self._resource
        candidates = self._candidates()
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise ScopeError(
                "未找到受支持的 USB 示波器；如需连接 LAN，请用 --resource 指定"
            )
        raise ScopeError(
            "找到多个候选设备，请用 --resource 指定: " + ", ".join(candidates)
        )

    @contextmanager
    def _connect(self) -> Iterator[tuple[ScopeDriver, str]]:
        """打开会话、核对 *IDN?、挑驱动；退出即关闭会话。"""
        address = self._select()
        with self._provider.open(address) as session:
            identity = Identity.parse(session.query("*IDN?"))
            driver_cls = match_identity(identity)
            if driver_cls is None:
                raise ScopeError(
                    f"不支持的仪器: {identity.manufacturer} {identity.model}"
                )
            yield driver_cls(identity, session), address

    def _require_control(self) -> None:
        """控制类工具在发起任何 I/O 前先检查授权。"""
        if not self._allow_control:
            raise ScopeError("控制功能未启用；请加 --allow-control 重启服务")

    # ---- 读取工具 ----

    def list_devices(self) -> list[dict]:
        with self._lock:
            devices = [
                {"resource": a, "configured": a == self._resource}
                for a in self._candidates()
            ]
            if self._resource is not None and all(
                d["resource"] != self._resource for d in devices
            ):
                devices.append({"resource": self._resource, "configured": True})
            return devices

    def get_info(self) -> dict:
        with self._lock, self._connect() as (driver, address):
            cap = driver.capabilities
            return {
                "resource": address,
                "identity": {
                    "manufacturer": driver.identity.manufacturer,
                    "model": driver.identity.model,
                    "serial_number": driver.identity.serial_number,
                    "firmware": driver.identity.firmware,
                },
                "capabilities": {
                    "analog_channels": cap.analog_channels,
                    "screen_format": cap.screen_format,
                    "waveform_modes": list(cap.waveform_modes),
                    "raw_max_points": cap.raw_max_points,
                    "trigger_modes": list(cap.trigger_modes),
                },
                "control_enabled": self._allow_control,
            }

    def get_setup(self) -> dict:
        with self._lock, self._connect() as (driver, _):
            return driver.get_setup()

    def get_screen(self) -> bytes:
        with self._lock, self._connect() as (driver, _):
            return driver.get_screen()

    # ---- 波形 ----

    @staticmethod
    def _preview(record, preview_points: int) -> list[dict]:
        count = record.sample_count
        n = min(preview_points, count)
        if n == 0:
            return []
        indices = [0] if n == 1 else [i * (count - 1) // (n - 1) for i in range(n)]
        return [
            {"index": i, "time_s": record.time_at(i), "value": record.value_at(i)}
            for i in indices
        ]

    def _write_csv(self, record) -> str:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        name = f"waveform-ch{record.channel}-{uuid.uuid4().hex}.csv"
        path = self._output_dir / name
        # 先独占打开再进 try：若因重名 open 失败，不会误删别人的已有文件。
        fh = path.open("x", newline="", encoding="utf-8")
        try:
            with fh:
                writer = csv.writer(fh)
                writer.writerow(["index", "time_s", "value", "unit", "raw_code"])
                for index in range(record.sample_count):
                    value = record.value_at(index)
                    raw = (
                        ""
                        if record.raw_codes is None
                        else record.raw_codes[index]
                    )
                    writer.writerow(
                        [
                            index,
                            record.time_at(index),
                            "" if value is None else value,
                            record.unit,
                            raw,
                        ]
                    )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return str(path)

    def read_waveform(
        self,
        channels: list[int],
        mode: str = "normal",
        points: int | None = None,
        preview_points: int = 32,
        save_csv: bool = True,
    ) -> dict:
        if not 0 <= preview_points <= _MAX_PREVIEW:
            raise ScopeError(f"preview_points 必须在 0..{_MAX_PREVIEW}")
        with self._lock, self._connect() as (driver, _):
            running, records = driver.read_waveforms(channels, mode, points)
            effective_points = (
                driver.DEFAULT_POINTS[mode] if points is None else points
            )
            out = {
                "mode": mode,
                "points_requested": effective_points,
                "acquisition_running": running,
                "channels": [],
            }
            for record in records:
                entry = {
                    "channel": record.channel,
                    "unit": record.unit,
                    "sample_count": record.sample_count,
                    "start_s": record.start_s,
                    "step_s": record.step_s,
                    "preview": self._preview(record, preview_points),
                    "csv_path": self._write_csv(record) if save_csv else None,
                }
                out["channels"].append(entry)
            return out

    # ---- 控制工具 ----

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
    ) -> dict:
        self._require_control()
        with self._lock, self._connect() as (driver, _):
            return driver.configure_channel(
                channel,
                enabled=enabled,
                scale_per_div=scale_per_div,
                offset=offset,
                coupling=coupling,
                probe_attenuation=probe_attenuation,
                bandwidth_limit=bandwidth_limit,
                invert=invert,
            )

    def configure_timebase(
        self,
        *,
        mode: str | None = None,
        scale_s_per_div: float | None = None,
        position_s: float | None = None,
    ) -> dict:
        self._require_control()
        with self._lock, self._connect() as (driver, _):
            return driver.configure_timebase(
                mode=mode, scale_s_per_div=scale_s_per_div, position_s=position_s
            )

    def configure_edge_trigger(
        self,
        *,
        source: str | None = None,
        slope: str | None = None,
        level: float | None = None,
        sweep: str | None = None,
    ) -> dict:
        self._require_control()
        with self._lock, self._connect() as (driver, _):
            return driver.configure_edge_trigger(
                source=source, slope=slope, level=level, sweep=sweep
            )

    def configure_acquisition(
        self, *, mode: str | None = None, average_count: int | None = None
    ) -> dict:
        self._require_control()
        with self._lock, self._connect() as (driver, _):
            return driver.configure_acquisition(mode=mode, average_count=average_count)

    def control_acquisition(self, action: str) -> dict:
        self._require_control()
        with self._lock, self._connect() as (driver, _):
            return driver.control_acquisition(action)
