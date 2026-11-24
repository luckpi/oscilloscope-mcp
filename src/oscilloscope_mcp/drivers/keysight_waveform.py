import sys
from array import array
from math import isfinite, nan

from ..ieee488 import definite_block
from ..waveform import Waveform

MAX_POINTS = 1_000_000


def decode_word(
    channel: int, unit: str, preamble: str, message: bytes
) -> Waveform:
    """解码 InfiniiVision 2000 X 的无符号小端 WORD 波形。"""
    fields = preamble.strip().split(",")
    if len(fields) != 10:
        raise ValueError("Expected 10 fields in the Keysight waveform preamble")
    fmt, kind, points, count = map(int, fields[:4])
    if fmt != 1:
        raise ValueError("Expected WORD data in the Keysight waveform preamble")
    if kind not in (0, 2, 3):
        raise ValueError(f"Unsupported waveform acquisition type: {kind}")
    if not 1 <= points <= MAX_POINTS or count < 1:
        raise ValueError("Invalid point or acquisition count in waveform preamble")

    dx, x_origin, dy, y_origin = map(float, (fields[4], fields[5], fields[7], fields[8]))
    x_ref, y_ref = int(fields[6]), int(fields[9])
    start = x_origin - x_ref * dx
    end = start + (points - 1) * dx
    lo = y_origin - y_ref * dy
    hi = y_origin + (65535 - y_ref) * dy
    if not (
        dx > 0
        and dy > 0
        and all(map(isfinite, (dx, start, end, dy, lo, hi)))
        and 0 <= y_ref <= 65535
    ):
        raise ValueError("Invalid scaling in Keysight waveform preamble")

    payload = definite_block(message, max_bytes=points * 2)
    if len(payload) != 2 * points:
        raise ValueError("Waveform payload length does not match the preamble")
    codes = array("H")
    codes.frombytes(payload)
    if sys.byteorder != "little":
        codes.byteswap()
    # 手册将二进制 0 定义为空洞标记；仍保留原码，避免丢失传输信息。
    values = array(
        "d", ((code - y_ref) * dy + y_origin if code else nan for code in codes)
    )
    return Waveform(channel, unit, start, dx, values, codes)
