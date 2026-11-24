from array import array
from dataclasses import dataclass
from math import isnan


@dataclass(frozen=True)
class Waveform:
    """与仪器无关的等间隔波形；缺失值在内存中用 NaN 表示。"""

    channel: int
    unit: str
    start_s: float
    step_s: float
    values: array
    raw_codes: array | None = None

    @property
    def sample_count(self) -> int:
        return len(self.values)

    def time_at(self, index: int) -> float:
        return self.start_s + index * self.step_s

    def value_at(self, index: int) -> float | None:
        value = self.values[index]
        return None if isnan(value) else value
