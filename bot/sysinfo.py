"""Сколько занято процессора, памяти и диска — без сторонних библиотек.

Библиотека psutil была бы уместнее, но она собирается под каждую версию
питона отдельно: на свежем интерпретаторе установка может не найти готовое
колесо и полезть компилировать, а это ломает обновление целиком. Здесь то же
самое взято из стандартной библиотеки: /proc в линуксе, kernel32 в винде.
Чего узнать не вышло — молчим, а не выдумываем.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Optional

from .formatting import plural

MB = 1024 * 1024


@dataclass(frozen=True)
class Memory:
    total_mb: int
    available_mb: int

    @property
    def used_percent(self) -> int:
        if not self.total_mb:
            return 0
        return round((self.total_mb - self.available_mb) / self.total_mb * 100)


def cores() -> int:
    return os.cpu_count() or 1


def memory() -> Optional[Memory]:
    """Сколько всего памяти и сколько ещё можно занять."""
    if sys.platform == "win32":
        return _windows_memory()
    return _proc_memory()


def _proc_memory() -> Optional[Memory]:
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            found = {}
            for line in handle:
                name, _, rest = line.partition(":")
                if name in {"MemTotal", "MemAvailable"}:
                    found[name] = int(rest.split()[0]) // 1024
                if len(found) == 2:
                    break
    except (OSError, ValueError, IndexError):
        return None
    if "MemTotal" not in found or "MemAvailable" not in found:
        return None
    return Memory(found["MemTotal"], found["MemAvailable"])


def _windows_memory() -> Optional[Memory]:
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return Memory(status.ullTotalPhys // MB, status.ullAvailPhys // MB)
    except Exception:  # noqa: BLE001 - справка о памяти не стоит падения
        return None


def disk_free_mb(path: str = ".") -> Optional[int]:
    try:
        return shutil.disk_usage(path).free // MB
    except OSError:
        return None


class CpuMeter:
    """Загрузка процессора между двумя замерами.

    Мгновенной загрузки не существует: она считается по тому, сколько времени
    система провела в простое между двумя вопросами. Поэтому первый ответ —
    None: сравнивать ещё не с чем.
    """

    def __init__(self) -> None:
        self._previous: Optional[tuple[float, float]] = None

    def sample(self) -> Optional[int]:
        current = _cpu_times()
        if current is None:
            return None

        previous, self._previous = self._previous, current
        if previous is None:
            return None

        busy = current[0] - previous[0]
        total = current[1] - previous[1]
        if total <= 0:
            return None
        return max(0, min(100, round(busy / total * 100)))


def _cpu_times() -> Optional[tuple[float, float]]:
    """(занято, всего) в каких-нибудь единицах — важна только разница."""
    if sys.platform == "win32":
        return _windows_cpu_times()
    try:
        with open("/proc/stat", encoding="ascii") as handle:
            parts = [float(value) for value in handle.readline().split()[1:]]
    except (OSError, ValueError, IndexError):
        return None
    if len(parts) < 4:
        return None
    total = sum(parts)
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    return total - idle, total


def _windows_cpu_times() -> Optional[tuple[float, float]]:
    try:
        import ctypes

        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

            @property
            def value(self) -> float:
                return (self.high << 32) + self.low

        idle, kernel, user = FileTime(), FileTime(), FileTime()
        ok = ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        )
        if not ok:
            return None
        total = kernel.value + user.value  # kernel уже включает простой
        return total - idle.value, total
    except Exception:  # noqa: BLE001
        return None


def format_memory(state: Optional[Memory]) -> str:
    if state is None:
        return ""
    total = state.total_mb / 1024
    free = state.available_mb / 1024
    return f"{total:.0f} ГБ · свободно {free:.1f}".replace(".", ",")


def describe() -> str:
    """Строка для шапки: ядра, память, свободное место."""
    count = cores()
    parts = [f"{count} {plural(count, 'ядро', 'ядра', 'ядер')}"]
    state = memory()
    if state is not None:
        parts.append(format_memory(state))
    free = disk_free_mb()
    if free is not None:
        parts.append(f"диск {free / 1024:.0f} ГБ".replace(".", ","))
    return " · ".join(parts)


def load_line(meter: CpuMeter) -> str:
    """«ЦП 92% · память 78%» — или пусто, если узнать не вышло."""
    parts = []
    busy = meter.sample()
    if busy is not None:
        parts.append(f"ЦП {busy}%")
    state = memory()
    if state is not None:
        parts.append(f"память {state.used_percent}%")
    return " · ".join(parts)
