"""MCP 工具层：把 ScopeService 暴露为 FastMCP stdio 工具。"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations

from .service import ScopeService

_READONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False)


def create_server(service: ScopeService) -> FastMCP:
    server = FastMCP("oscilloscope")

    @server.tool(annotations=_READONLY)
    def scope_list_devices() -> list[dict[str, Any]]:
        """列出候选示波器 VISA 地址；多候选时需在启动参数指定 --resource。"""
        return service.list_devices()

    @server.tool(annotations=_READONLY)
    def scope_info() -> dict[str, Any]:
        """连接示波器，返回 *IDN? 身份、能力与控制开关状态。"""
        return service.get_info()

    @server.tool(annotations=_READONLY)
    def scope_get_setup() -> dict[str, Any]:
        """读取采集、时基、触发与四个通道的实际回读设置。"""
        return service.get_setup()

    @server.tool(annotations=_READONLY)
    def scope_get_screen() -> Image:
        """抓取当前屏幕 PNG 图像。"""
        return Image(data=service.get_screen(), format="png")

    @server.tool(annotations=_READONLY)
    def scope_read_waveform(
        channels: list[int],
        mode: Literal["normal", "raw"] = "normal",
        points: int | None = None,
        preview_points: int = 32,
        save_csv: bool = True,
    ) -> dict[str, Any]:
        """读取通道波形（不启动/停止/新建采集；如需确定状态请先让用户停止）。preview 为均匀抽样预览，save_csv 时把全量数据写入输出目录。"""
        return service.read_waveform(
            channels,
            mode=mode,
            points=points,
            preview_points=preview_points,
            save_csv=save_csv,
        )

    @server.tool()
    def scope_configure_channel(
        channel: int,
        enabled: bool | None = None,
        scale_per_div: float | None = None,
        offset: float | None = None,
        coupling: Literal["ac", "dc", "AC", "DC"] | None = None,
        probe_attenuation: float | None = None,
        bandwidth_limit: bool | None = None,
        invert: bool | None = None,
    ) -> dict[str, Any]:
        """配置通道（需 --allow-control）；改动会使已配置或新采集的波形读数含义变化。"""
        return service.configure_channel(
            channel,
            enabled=enabled,
            scale_per_div=scale_per_div,
            offset=offset,
            coupling=coupling,
            probe_attenuation=probe_attenuation,
            bandwidth_limit=bandwidth_limit,
            invert=invert,
        )

    @server.tool()
    def scope_configure_timebase(
        mode: Literal["main", "roll", "xy", "MAIN", "ROLL", "XY"] | None = None,
        scale_s_per_div: float | None = None,
        position_s: float | None = None,
    ) -> dict[str, Any]:
        """配置时基（需 --allow-control）；改动会使已配置的波形点距/原点失效。"""
        return service.configure_timebase(
            mode=mode, scale_s_per_div=scale_s_per_div, position_s=position_s
        )

    @server.tool()
    def scope_configure_edge_trigger(
        source: Literal["ch1", "ch2", "ch3", "ch4", "external"] | None = None,
        slope: Literal["rising", "falling", "either"] | None = None,
        level: float | None = None,
        sweep: Literal["auto", "normal"] | None = None,
    ) -> dict[str, Any]:
        """配置边沿触发（需 --allow-control）；空参数也会强制切到 EDGE 模式。"""
        return service.configure_edge_trigger(
            source=source, slope=slope, level=level, sweep=sweep
        )

    @server.tool()
    def scope_configure_acquisition(
        mode: Literal["normal", "average", "high_resolution", "peak"] | None = None,
        average_count: int | None = None,
    ) -> dict[str, Any]:
        """配置采集类型/平均次数（需 --allow-control）；改动会使新采集波形特性变化。"""
        return service.configure_acquisition(mode=mode, average_count=average_count)

    @server.tool()
    def scope_control_acquisition(
        action: Literal["run", "stop", "single", "force_trigger"],
    ) -> dict[str, Any]:
        """运行/停止/单次/强制触发（需 --allow-control）；run/single 会使此前读到的波形过期。"""
        return service.control_acquisition(action)

    return server
