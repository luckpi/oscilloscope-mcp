"""Keysight/Agilent DSO-X 2024A 驱动：集中管理该型号全部 SCPI 命令。"""

from __future__ import annotations

import re
from math import isfinite

from ..ieee488 import definite_block
from ..model import Capabilities, Identity, ScopeError
from ..transport import ScpiSession
from ..waveform import Waveform
from .keysight_waveform import decode_word

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_OPER_RUNNING = 0x08  # :OPER:COND? bit3：采集运行中
_OPER_ARMED = 0x20  # :OPER:COND? bit5：已触发等待（armed）


def _normalize(text: str) -> str:
    """厂商/型号匹配前归一化：仅保留大写字母数字。"""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _num(value: float) -> str:
    return f"{value:.15g}"


def _require_finite(value: float, name: str) -> float:
    if not isfinite(value):
        raise ScopeError(f"{name} 必须是有限数值")
    return value


def _require_positive(value: float, name: str) -> float:
    if not isfinite(value) or value <= 0:
        raise ScopeError(f"{name} 必须是正的有限数值")
    return value


class DsoX2024A:
    """DSO-X 2024A（4 模拟通道，WORD 波形，edge 触发）。"""

    USB_VENDOR = 0x0957
    USB_PRODUCT = 0x1796
    MANUFACTURERS = frozenset(
        {"AGILENT", "AGILENTTECHNOLOGIES", "KEYSIGHT", "KEYSIGHTTECHNOLOGIES"}
    )
    MODEL = "DSOX2024A"

    ANALOG_CHANNELS = (1, 2, 3, 4)
    NORMAL_POINTS = (100, 250, 500, 1000)
    RAW_POINTS = NORMAL_POINTS + (
        2000,
        5000,
        10000,
        20000,
        50000,
        100000,
        200000,
        500000,
        1000000,
    )
    DEFAULT_POINTS = {"normal": 1000, "raw": 1000000}

    CAPABILITIES = Capabilities(
        analog_channels=4,
        screen_format="png",
        waveform_modes=("normal", "raw"),
        raw_max_points=1_000_000,
        trigger_modes=("edge",),
    )

    def __init__(self, identity: Identity, session: ScpiSession) -> None:
        self.identity = identity
        self.capabilities = self.CAPABILITIES
        self._session = session

    # ---- 匹配 ----

    @classmethod
    def matches_identity(cls, identity: Identity) -> bool:
        return _normalize(identity.manufacturer) in cls.MANUFACTURERS and _normalize(
            identity.model
        ) == cls.MODEL

    @classmethod
    def matches_resource(cls, address: str) -> bool:
        """按 VISA USB 地址中的 vid/pid 判断是否可能是本型号，不做 IDN 探测。"""
        tokens = address.split("::")
        if len(tokens) < 3 or not tokens[0].upper().startswith("USB"):
            return False
        try:
            vid = int(tokens[1], 0)
            pid = int(tokens[2], 0)
        except ValueError:
            return False
        return vid == cls.USB_VENDOR and pid == cls.USB_PRODUCT

    # ---- 内部工具 ----

    def _query(self, command: str) -> str:
        return self._session.query(command).strip()

    def _query_float(self, command: str) -> float:
        try:
            value = float(self._query(command))
        except ValueError as exc:
            raise ScopeError(f"无法解析 {command} 应答") from exc
        if not isfinite(value):
            raise ScopeError(f"{command} 应答不是有限数值")
        return value

    def _operation_condition(self) -> int:
        try:
            return int(self._query(":OPER:COND?"))
        except ValueError as exc:
            raise ScopeError("无法解析 :OPER:COND? 应答") from exc

    def _check_error(self, context: str) -> None:
        response = self._query(":SYST:ERR?")
        try:
            code = int(response.split(",", 1)[0])
        except (ValueError, IndexError) as exc:
            raise ScopeError(f"无法解析 :SYST:ERR? 应答: {response!r}") from exc
        if code != 0:
            raise ScopeError(f"{context} 时仪器报错: {response}")

    def _apply(self, commands: list[str]) -> None:
        """逐条写入并在每条前后检查 :SYST:ERR?；失败即中止，可能部分生效。"""
        if not commands:
            raise ScopeError("内部错误：命令列表为空")
        self._check_error("写入前检查")
        for command in commands:
            self._session.write(command)
            self._check_error(f"写入 {command!r}（之前的命令可能已生效）")

    def _channel_unit(self, channel: int) -> str:
        token = self._query(f":CHAN{channel}:UNIT?")
        if token == "VOLT":
            return "V"
        if token in ("AMP", "AMPERE"):
            return "A"
        raise ScopeError(f"通道 {channel} 单位不受支持: {token!r}")

    def _channel_readback(self, channel: int) -> dict:
        p = f":CHAN{channel}"
        return {
            "channel": channel,
            "enabled": self._query(f"{p}:DISP?") == "1",
            "scale_per_div": self._query_float(f"{p}:SCAL?"),
            "offset": self._query_float(f"{p}:OFFS?"),
            "coupling": self._query(f"{p}:COUP?"),
            "probe_attenuation": self._query_float(f"{p}:PROB?"),
            "bandwidth_limit": self._query(f"{p}:BWL?") == "1",
            "invert": self._query(f"{p}:INV?") == "1",
            "unit": self._channel_unit(channel),
        }

    def _acquisition_status(self) -> dict:
        cond = self._operation_condition()
        return {"running": bool(cond & _OPER_RUNNING), "armed": bool(cond & _OPER_ARMED)}

    # ---- 读取 ----

    def get_setup(self) -> dict:
        status = self._acquisition_status()
        acq_type = self._query(":ACQ:TYPE?")
        acquisition = {"type": acq_type, **status}
        if acq_type == "AVER":
            acquisition["average_count"] = int(self._query(":ACQ:COUN?"))

        timebase = {
            "mode": self._query(":TIM:MODE?"),
            "scale_s_per_div": self._query_float(":TIM:SCAL?"),
            "position_s": self._query_float(":TIM:POS?"),
        }

        trig_mode = self._query(":TRIG:MODE?")
        trigger = {"mode": trig_mode, "sweep": self._query(":TRIG:SWE?")}
        if trig_mode == "EDGE":
            source = self._query(":TRIG:EDGE:SOUR?")
            trigger["edge"] = {
                "source": source,
                "slope": self._query(":TRIG:EDGE:SLOP?"),
            }
            if source not in ("LINE", "WGEN"):
                trigger["edge"]["level"] = self._query_float(":TRIG:EDGE:LEV?")

        channels = [self._channel_readback(ch) for ch in self.ANALOG_CHANNELS]
        return {
            "acquisition": acquisition,
            "timebase": timebase,
            "trigger": trigger,
            "channels": channels,
        }

    def get_screen(self) -> bytes:
        try:
            png = definite_block(
                self._session.query_binary(":DISP:DATA? PNG, COL"),
                max_bytes=2_000_000,
            )
        except ValueError as exc:
            raise ScopeError(f"屏幕数据块无效: {exc}") from exc
        if not png.startswith(_PNG_MAGIC):
            raise ScopeError("屏幕数据不是有效 PNG")
        return png

    def read_waveforms(
        self, channels: list[int], mode: str, points: int | None
    ) -> tuple[bool, list[Waveform]]:
        channels = list(channels)
        if (
            not channels
            or len(set(channels)) != len(channels)
            or any(ch not in self.ANALOG_CHANNELS for ch in channels)
        ):
            raise ScopeError(f"通道必须是 1..4 的非空不重复列表: {channels!r}")
        if mode not in self.CAPABILITIES.waveform_modes:
            raise ScopeError(f"不支持的波形模式: {mode!r}")
        allowed = self.NORMAL_POINTS if mode == "normal" else self.RAW_POINTS
        points = self.DEFAULT_POINTS[mode] if points is None else points
        if points not in allowed:
            raise ScopeError(f"{mode} 模式不支持的点数: {points}")

        # 写出任何 :WAV 设置之前先核对采集前置条件。
        running = bool(self._operation_condition() & _OPER_RUNNING)
        acq_type = self._query(":ACQ:TYPE?")
        if acq_type == "PEAK":
            # PEAK 数据布局不同，normal 模式同样拒绝。
            raise ScopeError("PEAK 采集类型下不支持波形传输")
        if mode == "raw":
            if running:
                raise ScopeError("RAW 传输要求先停止采集")
            if self._query(":TIM:MODE?") != "MAIN":
                raise ScopeError("RAW 传输要求时基处于 MAIN")
            if acq_type not in ("NORM", "HRES"):
                raise ScopeError(f"RAW 传输不支持采集类型 {acq_type}")
        if len(channels) > 1 and running:
            raise ScopeError("多通道波形传输要求先停止采集")

        for ch in channels:
            if self._query(f":CHAN{ch}:DISP?") != "1":
                raise ScopeError(f"通道 {ch} 未显示，无法传输波形")

        records: list[Waveform] = []
        for ch in channels:
            unit = self._channel_unit(ch)
            point_mode = "RAW" if mode == "raw" else "NORM"
            commands = [
                f":WAV:SOUR CHAN{ch}",
                ":WAV:FORM WORD",
                ":WAV:BYT LSBF",
                ":WAV:UNS 1",
                f":WAV:POIN:MODE {point_mode}",
                f":WAV:POIN {points}",
            ]
            for command in commands:
                self._session.write(command)
            self._check_error("配置波形传输")
            actual_mode = self._query(":WAV:POIN:MODE?")
            if actual_mode != point_mode:
                raise ScopeError(
                    f"波形点数模式回读 {actual_mode}，与请求 {point_mode} 不一致"
                )
            preamble = self._session.query(":WAV:PRE?")
            blob = self._session.query_binary(":WAV:DATA?")
            try:
                records.append(decode_word(ch, unit, preamble, blob))
            except ValueError as exc:
                raise ScopeError(f"通道 {ch} 波形解码失败: {exc}") from exc

        if (mode == "raw" or len(channels) > 1) and (
            self._operation_condition() & _OPER_RUNNING
        ):
            # 要求停止态的传输结束后发现已恢复运行，波形来源不可信。
            raise ScopeError("读取期间采集已恢复运行，无法保证该波形来自停止时的记录")
        return running, records

    # ---- 控制 ----

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
        if channel not in self.ANALOG_CHANNELS:
            raise ScopeError(f"通道必须是 1..4: {channel}")
        p = f":CHAN{channel}"
        commands: list[str] = []
        if coupling is not None:
            coupling = coupling.upper()
            if coupling not in ("AC", "DC"):
                raise ScopeError(f"coupling 只能是 AC/DC: {coupling!r}")
            commands.append(f"{p}:COUP {coupling}")
        if probe_attenuation is not None:
            commands.append(f"{p}:PROB {_num(_require_positive(probe_attenuation, 'probe_attenuation'))}")
        if scale_per_div is not None:
            commands.append(f"{p}:SCAL {_num(_require_positive(scale_per_div, 'scale_per_div'))}")
        if offset is not None:
            commands.append(f"{p}:OFFS {_num(_require_finite(offset, 'offset'))}")
        if bandwidth_limit is not None:
            commands.append(f"{p}:BWL {int(bandwidth_limit)}")
        if invert is not None:
            commands.append(f"{p}:INV {int(invert)}")
        if enabled is not None:
            commands.append(f"{p}:DISP {int(enabled)}")
        if commands:
            self._apply(commands)
        return self._channel_readback(channel)

    def configure_timebase(
        self,
        *,
        mode: str | None = None,
        scale_s_per_div: float | None = None,
        position_s: float | None = None,
    ) -> dict:
        commands: list[str] = []
        if mode is not None:
            mode = mode.upper()
            if mode not in ("MAIN", "ROLL", "XY"):
                raise ScopeError(f"时基模式只能是 MAIN/ROLL/XY: {mode!r}")
            commands.append(f":TIM:MODE {mode}")
        if scale_s_per_div is not None:
            commands.append(
                f":TIM:SCAL {_num(_require_positive(scale_s_per_div, 'scale_s_per_div'))}"
            )
        if position_s is not None:
            commands.append(f":TIM:POS {_num(_require_finite(position_s, 'position_s'))}")
        if commands:
            self._apply(commands)
        return {
            "mode": self._query(":TIM:MODE?"),
            "scale_s_per_div": self._query_float(":TIM:SCAL?"),
            "position_s": self._query_float(":TIM:POS?"),
        }

    def configure_edge_trigger(
        self,
        *,
        source: str | None = None,
        slope: str | None = None,
        level: float | None = None,
        sweep: str | None = None,
    ) -> dict:
        if self._query(":TIM:MODE?") != "MAIN":
            raise ScopeError("配置边沿触发要求时基处于 MAIN")
        scpi_source: str | None = None
        if source is not None:
            if source.lower() == "external":
                scpi_source = "EXT"
            else:
                m = re.fullmatch(r"ch([1-4])", source.lower())
                if m is None:
                    raise ScopeError(f"source 只能是 ch1..ch4/external: {source!r}")
                scpi_source = f"CHAN{m.group(1)}"
        scpi_slope: str | None = None
        if slope is not None:
            scpi_slope = {"rising": "POS", "falling": "NEG", "either": "EITH"}.get(
                slope.lower()
            )
            if scpi_slope is None:
                raise ScopeError(f"slope 只能是 rising/falling/either: {slope!r}")
        scpi_sweep: str | None = None
        if sweep is not None:
            scpi_sweep = {"auto": "AUTO", "normal": "NORM"}.get(sweep.lower())
            if scpi_sweep is None:
                raise ScopeError(f"sweep 只能是 auto/normal: {sweep!r}")
        if level is not None:
            _require_finite(level, "level")
            if scpi_source is None:
                # 未显式换源时，LINE/WGEN 源没有触发电平概念，写入前拒绝。
                current = self._query(":TRIG:EDGE:SOUR?")
                if current in ("LINE", "WGEN"):
                    raise ScopeError(f"当前触发源 {current} 不支持电平设置")
        commands = [":TRIG:MODE EDGE"]
        if scpi_source is not None:
            commands.append(f":TRIG:EDGE:SOUR {scpi_source}")
        if scpi_slope is not None:
            commands.append(f":TRIG:EDGE:SLOP {scpi_slope}")
        if level is not None:
            commands.append(f":TRIG:EDGE:LEV {_num(level)}")
        if scpi_sweep is not None:
            commands.append(f":TRIG:SWE {scpi_sweep}")
        self._apply(commands)
        readback = {
            "mode": self._query(":TRIG:MODE?"),
            "sweep": self._query(":TRIG:SWE?"),
        }
        actual_source = self._query(":TRIG:EDGE:SOUR?")
        readback["edge"] = {
            "source": actual_source,
            "slope": self._query(":TRIG:EDGE:SLOP?"),
        }
        if actual_source not in ("LINE", "WGEN"):
            readback["edge"]["level"] = self._query_float(":TRIG:EDGE:LEV?")
        return readback

    def configure_acquisition(
        self,
        *,
        mode: str | None = None,
        average_count: int | None = None,
    ) -> dict:
        scpi_type: str | None = None
        if mode is not None:
            scpi_type = {
                "normal": "NORM",
                "average": "AVER",
                "high_resolution": "HRES",
                "peak": "PEAK",
            }.get(mode.lower())
            if scpi_type is None:
                raise ScopeError(
                    f"采集模式只能是 normal/average/high_resolution/peak: {mode!r}"
                )
        if average_count is not None and not 2 <= average_count <= 65536:
            raise ScopeError("average_count 必须在 2..65536")
        current_type = self._query(":ACQ:TYPE?") if scpi_type is None else scpi_type
        if average_count is not None and current_type != "AVER":
            raise ScopeError("average_count 仅适用于 average 采集模式")
        commands: list[str] = []
        if scpi_type is not None:
            commands.append(f":ACQ:TYPE {scpi_type}")
        if average_count is not None:
            commands.append(f":ACQ:COUN {average_count}")
        if commands:
            self._apply(commands)
        readback_type = self._query(":ACQ:TYPE?")
        result: dict = {"type": readback_type}
        if readback_type == "AVER":
            result["average_count"] = int(self._query(":ACQ:COUN?"))
        return result

    def control_acquisition(self, action: str) -> dict:
        command = {
            "run": ":RUN",
            "stop": ":STOP",
            "single": ":SING",
            "force_trigger": ":TRIG:FORC",
        }.get(action)
        if command is None:
            raise ScopeError(f"action 只能是 run/stop/single/force_trigger: {action!r}")
        self._apply([command])
        return {"action": action, **self._acquisition_status()}
