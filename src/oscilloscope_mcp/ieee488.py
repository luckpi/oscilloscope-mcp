def definite_block(message: bytes, *, max_bytes: int) -> bytes:
    """解析 IEEE 488.2 定长块，不把载荷内的换行当作结束符。"""
    if len(message) < 3 or message[:1] != b"#" or not 49 <= message[1] <= 57:
        raise ValueError("Expected an IEEE 488.2 definite-length block")
    start = 2 + (message[1] - 48)
    field = message[2:start]
    if len(message) < start or not field.isdigit():
        raise ValueError("Invalid IEEE 488.2 block length")
    size = int(field)
    if size > max_bytes:
        raise ValueError(f"IEEE 488.2 block exceeds {max_bytes} bytes")
    end = start + size
    if len(message) < end or message[end:] not in (b"", b"\n", b"\r\n"):
        raise ValueError("IEEE 488.2 block does not match its declared length")
    return message[start:end]
