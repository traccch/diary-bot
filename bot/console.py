"""Консоль как приборная панель: что с ботом, видно с одного взгляда.

Окно с ботом открыто часами, и смотрят в него не за тем, чтобы читать
подряд, а чтобы одним взглядом понять: работает ли, что сейчас происходит,
не пора ли вмешаться. Поэтому здесь три вещи:

* шапка при запуске — версия, база, записи, ближайшее напоминание;
* короткая строка на каждое событие вместо служебных сообщений библиотеки;
* цвет как разметка: спокойное приглушено, важное выделено.

Цвета отключаются сами, если вывод идёт не в терминал (перенаправлен в файл
или в pipe) или если задана переменная NO_COLOR — тогда остаётся чистый текст.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Optional, Sequence

#: Ширина рамки. 72 влезает в любое окно, включая cmd по умолчанию (80).
WIDTH = 72


def supports_color(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


def enable_windows_ansi() -> None:
    """Просит cmd понимать цвета. На всём, кроме Windows, — ничего не делает."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        # 7 = STD_OUTPUT_HANDLE, 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # noqa: BLE001 - цвет не стоит того, чтобы падать
        pass


class Palette:
    """Коды цветов — или пустые строки, если цвет не поддерживается."""

    NAMES = {
        "reset": "\033[0m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "magenta": "\033[35m",
        "cyan": "\033[36m",
        "grey": "\033[90m",
    }

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        for name, code in self.NAMES.items():
            setattr(self, name, code if enabled else "")

    def paint(self, text: str, *colors: str) -> str:
        if not self.enabled or not colors:
            return text
        prefix = "".join(getattr(self, color, "") for color in colors)
        return f"{prefix}{text}{self.reset}"


def cell_width(text: str) -> int:
    """Сколько знакомест займёт строка: у широких символов их два."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def visible(text: str) -> str:
    """Текст без управляющих последовательностей — по нему считается ширина."""
    result = []
    escaping = False
    for char in text:
        if escaping:
            escaping = char not in "mK"
            continue
        if char == "\033":
            escaping = True
            continue
        result.append(char)
    return "".join(result)


def clip(text: str, limit: int) -> str:
    """Обрезает по видимой ширине, не разрубая цветовую последовательность."""
    if cell_width(visible(text)) <= limit:
        return text

    kept: list[str] = []
    width = 0
    escaping = False
    for char in text:
        if escaping:
            kept.append(char)
            escaping = char not in "mK"
            continue
        if char == "\033":
            kept.append(char)
            escaping = True
            continue
        step = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if width + step > limit - 1:
            break
        kept.append(char)
        width += step
    return "".join(kept) + "…" + ("\033[0m" if "\033" in text else "")


def box(title: str, rows: Sequence[tuple[str, str]], palette: Palette) -> str:
    """Рамка с парами «поле — значение». Ширина считается по видимым знакам."""
    label_width = max((cell_width(label) for label, _ in rows), default=0)
    lines = [
        palette.paint("┌─ ", "grey")
        + palette.paint(title, "bold")
        + palette.paint(" " + "─" * max(0, WIDTH - 5 - cell_width(title)) + "┐", "grey")
    ]
    for label, value in rows:
        padded = label + " " * (label_width - cell_width(label))
        # длинное значение обрезаем: перекошенная рамка читается хуже, чем «…»
        value = clip(value, WIDTH - 7 - label_width)
        body = f"  {padded}   {value}"
        filler = " " * max(0, WIDTH - 2 - cell_width(visible(body)))
        lines.append(
            palette.paint("│", "grey")
            + f"  {palette.paint(padded, 'grey')}   {value}"
            + filler
            + palette.paint("│", "grey")
        )
    lines.append(palette.paint("└" + "─" * (WIDTH - 2) + "┘", "grey"))
    return "\n".join(lines)


def short_path(path: str, keep: int = 2) -> str:
    """Оставляет хвост пути: имя файла важнее, чем C:\\Users\\…\\AppData."""
    parts = path.replace("\\", "/").rstrip("/").split("/")
    if len(parts) <= keep:
        return path
    return "…/" + "/".join(parts[-keep:])


@dataclass
class Startup:
    """Всё, что стоит знать про запущенного бота."""

    username: str
    branch: str = ""
    commit: str = ""
    tz: str = "Europe/Moscow"
    now: Optional[dt.datetime] = None
    machine: str = ""
    db_path: str = ""
    db_bytes: int = 0
    counts: list[str] = field(default_factory=list)
    reminders: int = 0
    next_reminder: str = ""
    features: list[tuple[str, bool, str]] = field(default_factory=list)


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} Б"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} КБ"
    return f"{size / 1024 / 1024:.1f} МБ".replace(".", ",")


def banner(info: Startup, palette: Optional[Palette] = None) -> str:
    """Шапка при запуске: версия, база, записи, ближайшее напоминание."""
    palette = palette or Palette(False)
    rows: list[tuple[str, str]] = [
        ("Открыть", palette.paint(f"https://t.me/{info.username}", "cyan")),
    ]

    version = " · ".join(part for part in (info.branch, info.commit) if part)
    if version:
        rows.append(("Версия", version))

    when = info.now.strftime("%H:%M") if info.now else ""
    rows.append(("Часовой пояс", f"{info.tz}{f' · сейчас {when}' if when else ''}"))

    if info.machine:
        rows.append(("Машина", info.machine))
    if info.db_path:
        rows.append(("База", f"{info.db_path} · {human_size(info.db_bytes)}"))
    if info.counts:
        rows.append(("Записи", " · ".join(info.counts)))

    if info.reminders:
        text = f"{info.reminders}"
        if info.next_reminder:
            text += f" · ближайшее {info.next_reminder}"
        rows.append(("Напоминания", text))
    else:
        rows.append(("Напоминания", palette.paint("выключены", "yellow")))

    for name, ready, note in info.features:
        mark = palette.paint("✓", "green") if ready else palette.paint("—", "yellow")
        rows.append((name, f"{mark} {note}"))

    hint = palette.paint(
        "  Остановить — Ctrl+C · обновиться — /update в Telegram", "grey"
    )
    return "\n" + box(info.username, rows, palette) + "\n" + hint + "\n"


#: Как выглядит уровень важности в строке лога.
LEVEL_MARKS: dict[int, tuple[str, tuple[str, ...]]] = {
    logging.DEBUG: ("·", ("grey",)),
    logging.INFO: ("·", ("grey",)),
    logging.WARNING: ("!", ("yellow",)),
    logging.ERROR: ("✗", ("red",)),
    logging.CRITICAL: ("✗", ("red", "bold")),
}

#: Логгеры бота — их имя в строке не показываем, оно и так понятно.
OWN_PREFIX = "bot."


class PrettyFormatter(logging.Formatter):
    """Строка лога: время, значок важности, сообщение. Ничего лишнего."""

    def __init__(self, palette: Optional[Palette] = None) -> None:
        super().__init__()
        self.palette = palette or Palette(False)

    def format(self, record: logging.LogRecord) -> str:
        palette = self.palette
        mark, colors = LEVEL_MARKS.get(record.levelno, ("·", ("grey",)))
        stamp = dt.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        message = record.getMessage()
        if record.levelno >= logging.WARNING:
            message = palette.paint(message, *colors)
        elif record.name.startswith("aiogram"):
            message = palette.paint(message, "grey")

        source = ""
        if not (record.name.startswith(OWN_PREFIX) or record.name == "__main__"):
            short = record.name.split(".")[0]
            source = palette.paint(f"[{short}] ", "grey")

        line = (
            f"{palette.paint(stamp, 'grey')} {palette.paint(mark, *colors)} "
            f"{source}{message}"
        )
        if record.exc_info:
            line += "\n" + palette.paint(self.formatException(record.exc_info), "grey")
        return line


def setup(level: str = "INFO") -> Palette:
    """Ставит форматтер на корневой логгер и приглушает служебный шум aiogram."""
    enable_windows_ansi()
    palette = Palette(supports_color())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(PrettyFormatter(palette))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # «Update id=… is handled. Duration 2546 ms» — то же самое, но по-человечески
    # пишет наш собственный журнал событий.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    return palette
