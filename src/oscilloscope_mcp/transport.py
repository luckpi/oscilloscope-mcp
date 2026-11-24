"""传输层：SCPI 会话协议与基于 PyVISA 的资源提供者。"""

from __future__ import annotations

import os
import sys
from contextlib import AbstractContextManager, contextmanager
from typing import Iterator, Protocol

import pyvisa

from .model import ScopeError

_CHUNK_SIZE = 262_144


class ScpiSession(Protocol):
    """一次打开的仪器会话：查询、写入、二进制查询。"""

    def query(self, command: str) -> str: ...

    def write(self, command: str) -> None: ...

    def query_binary(self, command: str) -> bytes: ...


class ResourceProvider(Protocol):
    """枚举并打开仪器资源的提供者（便于测试替换）。"""

    def list_resources(self) -> tuple[str, ...]: ...

    def open(self, resource: str) -> AbstractContextManager[ScpiSession]: ...


def _default_library() -> str | None:
    """64 位 Windows 上优先使用系统 visa64.dll，否则交给 PyVISA 默认。"""
    if os.name == "nt" and sys.maxsize > 2**32:
        windir = os.environ.get("WINDIR", r"C:\Windows")
        candidate = os.path.join(windir, "System32", "visa64.dll")
        if os.path.exists(candidate):
            return candidate
    return None


class _VisaSession:
    """包装 pyvisa.Resource 为 ScpiSession，VISA 错误统一转成 ScopeError。"""

    def __init__(self, instrument: "pyvisa.resources.MessageBasedResource") -> None:
        self._instrument = instrument

    def query(self, command: str) -> str:
        try:
            return self._instrument.query(command).strip()
        except pyvisa.Error as exc:
            raise ScopeError(f"VISA 查询失败 ({command!r}): {exc}") from exc

    def write(self, command: str) -> None:
        try:
            self._instrument.write(command)
        except pyvisa.Error as exc:
            raise ScopeError(f"VISA 写入失败 ({command!r}): {exc}") from exc

    def query_binary(self, command: str) -> bytes:
        # 先写命令再 read_raw，不做 clear/reset，避免打断二进制块。
        try:
            self._instrument.write(command)
            return self._instrument.read_raw()
        except pyvisa.Error as exc:
            raise ScopeError(f"VISA 二进制查询失败 ({command!r}): {exc}") from exc


class VisaProvider:
    """基于 PyVISA 的 ResourceProvider；每次调用独立打开并显式关闭资源管理器。"""

    def __init__(self, library: str | None, timeout_ms: int) -> None:
        self._library = library if library is not None else _default_library()
        self._timeout_ms = timeout_ms

    def _new_manager(self) -> "pyvisa.ResourceManager":
        try:
            if self._library is None:
                return pyvisa.ResourceManager()
            return pyvisa.ResourceManager(self._library)
        except (pyvisa.Error, OSError, ValueError) as exc:
            raise ScopeError(f"无法打开 VISA 资源管理器: {exc}") from exc

    def list_resources(self) -> tuple[str, ...]:
        manager = self._new_manager()
        try:
            return tuple(manager.list_resources("?*INSTR"))
        except pyvisa.Error as exc:
            raise ScopeError(f"枚举 VISA 资源失败: {exc}") from exc
        finally:
            manager.close()

    @contextmanager
    def open(self, resource: str) -> Iterator[ScpiSession]:
        manager = self._new_manager()
        instrument = None
        try:
            try:
                instrument = manager.open_resource(
                    resource, open_timeout=self._timeout_ms
                )
                instrument.timeout = self._timeout_ms
                instrument.write_termination = "\n"
                instrument.read_termination = None
                instrument.chunk_size = _CHUNK_SIZE
            except pyvisa.Error as exc:
                raise ScopeError(f"无法打开资源 {resource!r}: {exc}") from exc
            yield _VisaSession(instrument)
        finally:
            try:
                if instrument is not None:
                    instrument.close()
            finally:
                manager.close()
