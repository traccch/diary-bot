"""Картинка прогресса по английскому: календарь занятий, уровень, темы.

Цифры в тексте честные, но не цепляют. Календарь, где каждый день занятий —
закрашенная клетка, работает сам: пустую клетку в серии хочется не допускать.

Цвета — одна синяя шкала от светлого к тёмному: здесь одна величина (сколько
ответов за день), и разные оттенки означали бы разные вещи. Без эмодзи: в
шрифтах matplotlib их нет, вместо иконки был бы квадрат.
"""

from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from ..pressure.charts import COLOR_INK, COLOR_MUTED, _pyplot, available

#: Пустой день и четыре ступени по числу ответов (сессия — около десяти).
COLOR_EMPTY = "#efeeea"
STEPS: tuple[tuple[int, str], ...] = (
    (1, "#b7d3f6"),
    (10, "#6da7ec"),
    (20, "#2a78d6"),
    (40, "#184f95"),
)
COLOR_BAR = "#2a78d6"
COLOR_TRACK = "#efeeea"

#: Сколько недель показывать в календаре.
WEEKS = 26

WEEKDAYS = ("пн", "", "ср", "", "пт", "", "")
MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")

__all__ = ["available", "Snapshot", "progress_png", "color_for", "calendar_start"]


@dataclass(frozen=True)
class Snapshot:
    """Всё, что нужно картинке: собирается в обработчике из базы."""

    today: dt.date
    days: Mapping[dt.date, int]
    learned: int
    in_work: int
    streak: int
    level: str
    next_level: Optional[tuple[int, str]]  # порог и название следующего уровня
    level_floor: int  # порог текущего уровня
    packs: Sequence[tuple[str, int, int]]  # тема, выучено, всего


def color_for(answers: int) -> str:
    color = COLOR_EMPTY
    for threshold, step in STEPS:
        if answers >= threshold:
            color = step
    return color


def calendar_start(today: dt.date, weeks: int = WEEKS) -> dt.date:
    """Понедельник, с которого начинается календарь: последняя колонка — эта неделя."""
    monday = today - dt.timedelta(days=today.weekday())
    return monday - dt.timedelta(weeks=weeks - 1)


def _words_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "слово"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "слова"
    return "слов"


def _days_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "день"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


def progress_png(snapshot: Snapshot) -> bytes:
    plt = _pyplot()
    from matplotlib.patches import FancyBboxPatch

    figure = plt.figure(figsize=(9, 8.2), dpi=170)
    figure.text(0.04, 0.955, "Английский", fontsize=16, fontweight="bold", color=COLOR_INK)
    figure.text(0.04, 0.927, f"Уровень: {snapshot.level}", fontsize=10, color=COLOR_MUTED)

    _stats_row(figure, snapshot)
    _level_bar(figure, snapshot)
    _calendar(figure, snapshot, FancyBboxPatch)
    _packs(figure, snapshot)

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor="white")
    plt.close(figure)
    return buffer.getvalue()


def _stats_row(figure, snapshot: Snapshot) -> None:
    """Три крупных числа — то, ради чего открывают экран."""
    tiles = (
        (f"{snapshot.learned}", "выучено слов"),
        (f"{snapshot.in_work}", "в работе"),
        (f"{snapshot.streak}", f"{_days_word(snapshot.streak)} подряд"),
    )
    for index, (value, label) in enumerate(tiles):
        x = 0.04 + index * 0.22
        figure.text(x, 0.845, value, fontsize=26, fontweight="bold", color=COLOR_INK)
        figure.text(x, 0.825, label, fontsize=9, color=COLOR_MUTED)


def _rounded(axes, patch_type, x, y, width, height, color, radius=0.25):
    axes.add_patch(
        patch_type(
            (x, y), width, height,
            boxstyle=f"round,pad=0,rounding_size={radius}",
            linewidth=0, facecolor=color,
        )
    )


def _pill(axes, y: float, share: float, width: float) -> None:
    """Полоса с круглыми концами: дорожка и заливка поверх неё."""
    axes.plot([0, 1], [y, y], color=COLOR_TRACK, linewidth=width,
              solid_capstyle="round", zorder=1)
    if share > 0:
        axes.plot([0, max(share, 0.004)], [y, y], color=COLOR_BAR, linewidth=width,
                  solid_capstyle="round", zorder=2)


def _level_bar(figure, snapshot: Snapshot) -> None:
    axes = figure.add_axes((0.71, 0.825, 0.25, 0.06))
    axes.set_axis_off()
    axes.set_xlim(-0.02, 1.02)
    axes.set_ylim(0, 1)

    if snapshot.next_level is None:
        share, caption = 1.0, "Все уровни набора пройдены"
    else:
        threshold, name = snapshot.next_level
        span = max(1, threshold - snapshot.level_floor)
        share = min(1.0, max(0.0, (snapshot.learned - snapshot.level_floor) / span))
        left = max(0, threshold - snapshot.learned)
        caption = f"До {name.split(' — ')[0]} — ещё {left} {_words_word(left)}"

    axes.text(0, 0.95, caption, fontsize=9.5, fontweight="bold", color=COLOR_INK, va="top")
    _pill(axes, 0.25, share, 7)


def _calendar(figure, snapshot: Snapshot, patch_type) -> None:
    start = calendar_start(snapshot.today)
    axes = figure.add_axes((0.04, 0.395, 0.92, 0.33))
    axes.set_axis_off()
    axes.set_xlim(-1.3, WEEKS)
    axes.set_ylim(7.9, -1.4)  # понедельник сверху, место под месяцы
    axes.set_aspect("equal", anchor="W")  # клетки квадратные

    figure.text(0.04, 0.745, "Занятия по дням", fontsize=10.5, fontweight="bold", color=COLOR_INK)
    total_days = sum(1 for day, count in snapshot.days.items() if count and day >= start)
    figure.text(
        0.96, 0.745,
        f"{total_days} {_days_word(total_days)} с занятиями за полгода",
        fontsize=8.5, color=COLOR_MUTED, ha="right",
    )

    cell = 0.82
    last_month = None
    for week in range(WEEKS):
        for weekday in range(7):
            day = start + dt.timedelta(weeks=week, days=weekday)
            if day > snapshot.today:
                continue
            color = color_for(snapshot.days.get(day, 0))
            _rounded(axes, patch_type, week, weekday, cell, cell, color, 0.16)
            if day == snapshot.today:
                axes.add_patch(
                    patch_type(
                        (week - 0.06, weekday - 0.06), cell + 0.12, cell + 0.12,
                        boxstyle="round,pad=0,rounding_size=0.2",
                        linewidth=1.2, edgecolor=COLOR_INK, facecolor="none",
                    )
                )
            # подпись месяца — над его первым понедельником; обрезанный
            # месяц в начале календаря не подписываем, чтобы не налезал
            if weekday == 0 and day.month != last_month and day.day <= 7:
                axes.text(week, -0.45, MONTHS[day.month - 1], fontsize=8, color=COLOR_MUTED)
                last_month = day.month

    for weekday, name in enumerate(WEEKDAYS):
        if name:
            axes.text(-0.35, weekday + cell / 2, name, fontsize=7.5, color=COLOR_MUTED,
                      ha="right", va="center")

    # легенда: светлее — меньше ответов, темнее — больше
    legend = figure.add_axes((0.70, 0.37, 0.26, 0.03))
    legend.set_axis_off()
    legend.set_xlim(0, 10)
    legend.set_ylim(0, 1)
    legend.text(0, 0.5, "меньше", fontsize=7.5, color=COLOR_MUTED, va="center")
    for index, color in enumerate([COLOR_EMPTY, *[step for _, step in STEPS]]):
        _rounded(legend, patch_type, 2.6 + index * 1.05, 0.12, 0.8, 0.76, color, 0.12)
    legend.text(10, 0.5, "больше", fontsize=7.5, color=COLOR_MUTED, va="center", ha="right")


def _packs(figure, snapshot: Snapshot) -> None:
    packs = list(snapshot.packs)
    if not packs:
        return
    figure.text(0.04, 0.325, "Темы", fontsize=10.5, fontweight="bold", color=COLOR_INK)

    axes = figure.add_axes((0.20, 0.03, 0.64, 0.27))
    axes.set_axis_off()
    axes.set_xlim(-0.01, 1.01)
    axes.set_ylim(len(packs) - 0.4, -0.6)

    for row, (title, known, total) in enumerate(packs):
        _pill(axes, row, known / total if total else 0, 6)
        axes.text(-0.04, row, title, fontsize=9, color=COLOR_INK,
                  ha="right", va="center", clip_on=False)
        axes.text(1.04, row, f"{known} из {total}", fontsize=8.5, color=COLOR_MUTED,
                  va="center", ha="left", clip_on=False)
