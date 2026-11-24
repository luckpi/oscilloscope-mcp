"""命令行入口：解析参数、构造服务并运行 stdio MCP。"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from .server import create_server
from .service import ScopeService
from .transport import VisaProvider


def _timeout_ms(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 120000:
        raise argparse.ArgumentTypeError("--timeout-ms 必须在 1..120000")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oscilloscope-mcp", description="示波器 stdio MCP 服务"
    )
    parser.add_argument("--resource", default=None, help="指定 VISA 资源地址")
    parser.add_argument("--visa-library", default=None, help="VISA 库路径")
    parser.add_argument("--timeout-ms", type=_timeout_ms, default=15000)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "oscilloscope-mcp",
        help="CSV 输出目录",
    )
    parser.add_argument(
        "--allow-control", action="store_true", help="允许写入仪器的控制工具"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    provider = VisaProvider(library=args.visa_library, timeout_ms=args.timeout_ms)
    service = ScopeService(
        provider,
        resource=args.resource,
        allow_control=args.allow_control,
        output_dir=args.output_dir,
    )
    create_server(service).run(transport="stdio")


if __name__ == "__main__":
    main()
