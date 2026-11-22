"""Картинки раздела «Малыш»: неделя сна и кривые роста на фоне норм ВОЗ.

Неделя сна — классическая «лента»: строка на день, по горизонтали сутки,
закрашено время сна. Ночь и дневные сны — две ступени одного синего, чтобы
это читалось как «одно и то же, но разное время суток», а не как две
разные величины. Без эмодзи: в шрифтах matplotlib их нет.
"""

from __future__ import annotations

import datetime as dt
import io
from typing import Sequence

from ..pressure.charts import COLOR_GRID, COLOR_INK, COLOR_MUTED, _pyplot, available
from . import growth
from .norms import duration_text
from .sleep import Sleep

COLOR_NIGHT = "#184f95"
COLOR_NAP = "#6da7ec"
COLOR_TRACK = "#f3f2ee"
COLOR_BAND = "#dbe9fb"
COLOR_LINE = "#2a78d6"

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

__all__ = ["available", "week_png", "growth_png", "day_pieces"]


def day_pieces(
    sleeps: Sequence[Sleep], day: dt.date, now: dt.datetime
) -> list[tuple[float, float, bool]]:
    """Куски сна, попавшие в сутки: (час начала, час конца, ночной ли)."""
    start_of_day = dt.datetime.combine(day, dt.time())
    end_of_day = start_of_day + dt.timedelta(days=1)
    pieces = []
    for sleep in sleeps:
        left = max(sleep.start, start_of_day)
        right = min(sleep.end or now, end_of_day)
        if right <= left:
            continue
        pieces.append(
            (
                (left - start_of_day).total_seconds() / 3600,
                (right - start_of_day).total_seconds() / 3600,
                sleep.is_night(now),
            )
        )
    return pieces


def week_png(sleeps: Sequence[Sleep], now: dt.datetime, name: str, days: int = 7) -> bytes:
    plt = _pyplot()
    today = now.date()
    dates = [today - dt.timedelta(days=offset) for offset in range(days - 1, -1, -1)]

    figure = plt.figure(figsize=(9, 1.6 + 0.55 * days), dpi=170)
    height = 1.6 + 0.55 * days
    figure.text(0.03, 1 - 0.3 / height, f"Сон: {name}", fontsize=14, fontweight="bold", color=COLOR_INK)
    figure.text(
        0.03, 1 - 0.55 / height, "последние 7 дней · по горизонтали — часы суток",
        fontsize=9, color=COLOR_MUTED,
    )
    axes = figure.add_axes((0.11, 0.55 / height, 0.74, 1 - 1.45 / height))
    axes.set_xlim(0, 24)
    axes.set_ylim(days - 0.5, -0.5)

    for row, day in enumerate(dates):
        axes.barh(row, 24, left=0, height=0.62, color=COLOR_TRACK, zorder=1)
        total = 0.0
        for start, end, night in day_pieces(sleeps, day, now):
            axes.barh(
                row, end - start, left=start, height=0.62,
                color=COLOR_NIGHT if night else COLOR_NAP, zorder=2,
            )
            total += end - start
        label = "сегодня" if day == today else f"{WEEKDAYS[day.weekday()]} {day:%d.%m}"
        axes.text(-0.4, row, label, ha="right", va="center", fontsize=8.5, color=COLOR_INK)
        axes.text(
            24.4, row, duration_text(dt.timedelta(hours=total)) if total else "—",
            ha="left", va="center", fontsize=8.5, color=COLOR_MUTED,
        )

    now_hour = now.hour + now.minute / 60
    axes.plot([now_hour, now_hour], [days - 1 - 0.4, days - 1 + 0.4], color=COLOR_INK, linewidth=1.2, zorder=3)

    axes.set_xticks(range(0, 25, 3))
    axes.set_xticklabels([f"{hour:02d}:00" for hour in range(0, 25, 3)], fontsize=7.5, color=COLOR_MUTED)
    axes.set_yticks([])
    for side in ("top", "right", "left", "bottom"):
        axes.spines[side].set_visible(False)
    axes.tick_params(axis="x", length=0)
    for hour in range(0, 25, 3):
        axes.axvline(hour, color=COLOR_GRID, linewidth=0.6, zorder=0)

    # легенда — словами у цветных квадратиков, а не цветом в одиночку
    for index, (text, color) in enumerate((("ночной сон", COLOR_NIGHT), ("дневной", COLOR_NAP))):
        x = 0.62 + index * 0.16
        figure.patches.append(
            plt.Rectangle((x, 1 - 0.5 / height), 0.014, 0.12 / height,
                          transform=figure.transFigure, color=color)
        )
        figure.text(x + 0.02, 1 - 0.5 / height, text, fontsize=8.5, color=COLOR_MUTED,
                    va="bottom")

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor="white")
    plt.close(figure)
    return buffer.getvalue()


def growth_png(
    points: Sequence[tuple[dt.date, str, float]], birth: dt.date, sex: str, name: str, today: dt.date
) -> bytes:
    """Три панели — вес, рост, голова — у каждой своя ось и коридор нормы ВОЗ."""
    plt = _pyplot()
    kinds = [kind for kind in (growth.WEIGHT, growth.LENGTH, growth.HEAD)
             if any(item[1] == kind for item in points)]
    if not kinds:
        raise ValueError("замеров нет")

    figure = plt.figure(figsize=(9, 1.0 + 2.6 * len(kinds)), dpi=170)
    height = 1.0 + 2.6 * len(kinds)
    figure.text(0.03, 1 - 0.32 / height, f"Рост и вес: {name}", fontsize=14,
                fontweight="bold", color=COLOR_INK)
    figure.text(0.03, 1 - 0.56 / height,
                "голубая полоса — середина нормы ВОЗ (от −2 до +2), пунктир — медиана",
                fontsize=9, color=COLOR_MUTED)

    latest = max(item[0] for item in points)
    age_max = max((today - birth).days, (latest - birth).days) + 45
    age_max = min(max(age_max, 120), 1092)
    days = list(range(0, age_max + 1, 7))
    grid = figure.add_gridspec(len(kinds), 1, left=0.1, right=0.86,
                               bottom=0.5 / height, top=1 - 0.85 / height, hspace=0.45)

    for row, kind in enumerate(kinds):
        axes = figure.add_subplot(grid[row])
        months = [day / 30.4375 for day in days]
        low = [growth.at_z(kind, sex, day, -2) for day in days]
        high = [growth.at_z(kind, sex, day, 2) for day in days]
        mid = [growth.median(kind, sex, day) for day in days]
        axes.fill_between(months, low, high, color=COLOR_BAND, linewidth=0, zorder=1)
        axes.plot(months, mid, color=COLOR_MUTED, linewidth=0.9, linestyle=(0, (4, 3)), zorder=2)

        own = sorted((item[0], item[2]) for item in points if item[1] == kind)
        xs = [(on - birth).days / 30.4375 for on, _ in own]
        ys = [value for _, value in own]
        axes.plot(xs, ys, color=COLOR_LINE, linewidth=2, marker="o", markersize=5, zorder=3)
        last_x, last_y = xs[-1], ys[-1]
        axes.annotate(
            f"{last_y:g} {growth.UNITS[kind]}".replace(".", ","),
            xy=(last_x, last_y), xytext=(6, 0), textcoords="offset points",
            va="center", fontsize=8.5, color=COLOR_INK, zorder=4,
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": "none"},
        )

        axes.set_title(growth.TITLES[kind], loc="left", fontsize=10, color=COLOR_INK)
        axes.set_xlim(0, age_max / 30.4375)
        axes.set_xlabel("возраст, мес." if row == len(kinds) - 1 else "", fontsize=8, color=COLOR_MUTED)
        for side in ("top", "right"):
            axes.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            axes.spines[side].set_color(COLOR_GRID)
        axes.grid(axis="y", color=COLOR_GRID, linewidth=0.6)
        axes.tick_params(colors=COLOR_MUTED, labelsize=8)

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor="white")
    plt.close(figure)
    return buffer.getvalue()
