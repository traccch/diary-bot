"""Графики и PDF-выгрузка. Требуют matplotlib; без него бот работает без картинок.

Пульс рисуется отдельной панелью под давлением, а не второй осью Y: две шкалы
на одном поле создают ложные пересечения и читаются неверно.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
from typing import Optional, Sequence

from . import metrics
from ..db import UserSettings
from .db import Measurement
from .export import table_rows
from ..formatting import format_period
from .stats import Summary

#: Цвета серий (проверены на различимость при дальтонизме).
COLOR_SYS = "#eb6834"
COLOR_DIA = "#2a78d6"
COLOR_PULSE = "#1baf7a"
COLOR_INK = "#1a1a19"
COLOR_MUTED = "#6b6a65"
COLOR_GRID = "#e6e5e1"
COLOR_TARGET = "#b3b2ac"

#: С этого количества точек рисуем суточные средние, а не каждое измерение.
DAILY_THRESHOLD = 60

TABLE_ROWS_PER_PAGE = 40


class ChartsUnavailable(RuntimeError):
    """matplotlib не установлен."""


def available() -> bool:
    return importlib.util.find_spec("matplotlib") is not None


def _pyplot():
    try:
        import matplotlib
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise ChartsUnavailable("matplotlib не установлен") from exc

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": COLOR_GRID,
            "axes.labelcolor": COLOR_MUTED,
            "text.color": COLOR_INK,
            "xtick.color": COLOR_MUTED,
            "ytick.color": COLOR_MUTED,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
        }
    )
    return plt


def _daily(measurements: Sequence[Measurement]):
    """Суточные средние плюс разброс каждого ряда за день."""
    grouped: dict[dt.date, list[Measurement]] = {}
    for measurement in measurements:
        grouped.setdefault(measurement.measured_at.date(), []).append(measurement)

    rows = []
    for date in sorted(grouped):
        chunk = grouped[date]
        pulses = [m.pulse for m in chunk if m.pulse]
        rows.append(
            {
                "at": dt.datetime.combine(date, dt.time(12, 0)),
                "sys": sum(m.systolic for m in chunk) / len(chunk),
                "dia": sum(m.diastolic for m in chunk) / len(chunk),
                "pulse": sum(pulses) / len(pulses) if pulses else None,
                "sys_low": min(m.systolic for m in chunk),
                "sys_high": max(m.systolic for m in chunk),
                "dia_low": min(m.diastolic for m in chunk),
                "dia_high": max(m.diastolic for m in chunk),
            }
        )
    return rows


def _style_axes(axes) -> None:
    axes.spines["top"].set_visible(False)
    axes.spines["right"].set_visible(False)
    axes.spines["left"].set_color(COLOR_GRID)
    axes.spines["bottom"].set_color(COLOR_GRID)
    axes.grid(axis="y", color=COLOR_GRID, linewidth=0.8)
    axes.set_axisbelow(True)


def _draw(
    figure,
    measurements: Sequence[Measurement],
    user: UserSettings,
    rect: tuple[float, float, float, float],
) -> None:
    """Рисует на фигуре две панели: давление сверху, пульс снизу.

    `rect` — доля фигуры (left, right, bottom, top), которую занимает график.
    Через SubFigure это не сделать: поля gridspec для них игнорируются.
    """
    import matplotlib.dates as mdates

    left, right, low, high = rect

    aggregated = len(measurements) > DAILY_THRESHOLD
    if aggregated:
        rows = _daily(measurements)
        times = [row["at"] for row in rows]
        systolic = [row["sys"] for row in rows]
        diastolic = [row["dia"] for row in rows]
        pulses = [(row["at"], row["pulse"]) for row in rows if row["pulse"] is not None]
        spread = rows
    else:
        times = [m.measured_at for m in measurements]
        systolic = [m.systolic for m in measurements]
        diastolic = [m.diastolic for m in measurements]
        pulses = [(m.measured_at, m.pulse) for m in measurements if m.pulse]
        spread = None

    has_pulse = len(pulses) >= 2
    if has_pulse:
        grid = figure.add_gridspec(
            2, 1, height_ratios=[3, 1], hspace=0.12,
            left=left, right=right, bottom=low, top=high,
        )
        top = figure.add_subplot(grid[0])
        bottom = figure.add_subplot(grid[1], sharex=top)
        top.tick_params(labelbottom=False)
    else:
        grid = figure.add_gridspec(1, 1, left=left, right=right, bottom=low, top=high)
        top = figure.add_subplot(grid[0])
        bottom = None

    if spread is not None:
        # каждый ряд получает свою полосу «мин–макс за день»: одна общая полоса
        # растянулась бы от верхнего до нижнего и не значила бы ничего
        top.fill_between(
            times, [row["sys_low"] for row in spread], [row["sys_high"] for row in spread],
            color=COLOR_SYS, alpha=0.15, linewidth=0, zorder=1,
            label="разброс за день",
        )
        top.fill_between(
            times, [row["dia_low"] for row in spread], [row["dia_high"] for row in spread],
            color=COLOR_DIA, alpha=0.15, linewidth=0, zorder=1,
        )

    marker = "o" if len(times) <= 120 else None
    top.plot(
        times, systolic, color=COLOR_SYS, linewidth=1.8, marker=marker, markersize=3.5,
        label="верхнее (САД)", zorder=3,
    )
    top.plot(
        times, diastolic, color=COLOR_DIA, linewidth=1.8, marker=marker, markersize=3.5,
        label="нижнее (ДАД)", zorder=3,
    )

    for level in (user.target_sys, user.target_dia):
        top.axhline(level, color=COLOR_TARGET, linewidth=1, linestyle=(0, (4, 4)), zorder=1)
    # подпись цели живёт в поле справа от графика, чтобы не наезжать на данные
    top.annotate(
        f"цель {user.target_sys}/{user.target_dia}",
        xy=(1, user.target_sys),
        xycoords=("axes fraction", "data"),
        xytext=(5, -2),
        textcoords="offset points",
        ha="left",
        va="center",
        fontsize=7.5,
        color=COLOR_MUTED,
        annotation_clip=False,
    )

    top.set_ylabel("мм рт. ст.")
    top.legend(loc="upper left", frameon=False, fontsize=8, ncol=3, borderaxespad=0.2)
    _style_axes(top)

    if bottom is not None:
        bottom.plot(
            [item[0] for item in pulses],
            [item[1] for item in pulses],
            color=COLOR_PULSE,
            linewidth=1.6,
            marker=marker,
            markersize=3,
        )
        bottom.set_ylabel("пульс")
        _style_axes(bottom)
        axis_owner = bottom
    else:
        axis_owner = top

    span_days = max(1, (times[-1] - times[0]).days)
    if span_days <= 14:
        locator = mdates.DayLocator(interval=max(1, span_days // 7))
    elif span_days <= 90:
        locator = mdates.WeekdayLocator(byweekday=mdates.MO)
    else:
        locator = mdates.MonthLocator()
    axis_owner.xaxis.set_major_locator(locator)
    axis_owner.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    for label in axis_owner.get_xticklabels():
        label.set_rotation(0)


def pressure_png(
    measurements: Sequence[Measurement], user: UserSettings, title: str
) -> bytes:
    """График давления и пульса за период — PNG для отправки в чат."""
    if len(measurements) < 2:
        raise ValueError("для графика нужно хотя бы два измерения")

    plt = _pyplot()
    figure = plt.figure(figsize=(9, 5.2), dpi=170)
    figure.suptitle(title, x=0.02, ha="left", fontsize=13, fontweight="bold")
    figure.text(
        0.02,
        0.925,
        f"{format_period(measurements[0].measured_at.date(), measurements[-1].measured_at.date())}"
        f" · {len(measurements)} измерений",
        ha="left",
        fontsize=9,
        color=COLOR_MUTED,
    )

    _draw(figure, measurements, user, rect=(0.08, 0.90, 0.09, 0.86))

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor="white")
    plt.close(figure)
    return buffer.getvalue()


# ------------------------------------------------ графики показателей здоровья

#: У каждого показателя свой график, поэтому серия всегда одна и цвета
#: между собой не соревнуются — берём разные, чтобы не путать вкладки.
METRIC_COLORS = {
    "sleep": "#4a3aa7",
    "steps": "#1baf7a",
    "resting_pulse": "#2a78d6",
    "weight": "#eb6834",
}


def _tick_formatter(kind: metrics.MetricKind):
    """Подписи оси по-русски: 7 400 вместо 7400 и 78,5 вместо 78.5."""
    if kind.key == metrics.STEPS.key:
        return lambda value, _: f"{value:,.0f}".replace(",", " ")
    return lambda value, _: f"{value:g}".replace(".", ",")


def metric_png(
    kind: metrics.MetricKind,
    values: Sequence[tuple[dt.date, float]],
    subtitle: str = "",
) -> bytes:
    """График одного показателя: столбики для сна и шагов, линия для веса и пульса."""
    if len(values) < 2:
        raise ValueError("для графика нужно хотя бы два значения")

    plt = _pyplot()
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter

    dates = [date for date, _ in values]
    heights = [metrics.chart_value(kind.key, value) for _, value in values]
    color = METRIC_COLORS.get(kind.key, COLOR_DIA)
    average = sum(heights) / len(heights)

    figure = plt.figure(figsize=(9, 4.4), dpi=170)
    # без эмодзи: в шрифтах matplotlib их глифов нет, вместо иконки будет квадрат
    figure.suptitle(kind.title, x=0.02, ha="left", fontsize=13, fontweight="bold")
    if subtitle:
        figure.text(0.02, 0.905, subtitle, ha="left", fontsize=9, color=COLOR_MUTED)

    grid = figure.add_gridspec(1, 1, left=0.08, right=0.84, bottom=0.14, top=0.82)
    axes = figure.add_subplot(grid[0])

    if kind.chart == "bars":
        axes.bar(dates, heights, width=0.72, color=color, zorder=2)
        axes.set_ylim(0, max(heights) * 1.15)
    else:
        axes.plot(
            dates, heights, color=color, linewidth=1.8, marker="o", markersize=4, zorder=3
        )

    axes.axhline(average, color=COLOR_TARGET, linewidth=1, linestyle=(0, (4, 4)), zorder=1)
    axes.annotate(
        f"среднее {metrics.format_value(kind.key, sum(v for _, v in values) / len(values))}",
        xy=(1, average),
        xycoords=("axes fraction", "data"),
        xytext=(5, -2),
        textcoords="offset points",
        ha="left",
        va="center",
        fontsize=7.5,
        color=COLOR_MUTED,
        annotation_clip=False,
    )

    axes.set_ylabel(kind.axis)
    axes.yaxis.set_major_formatter(FuncFormatter(_tick_formatter(kind)))
    _style_axes(axes)

    span = max(1, (dates[-1] - dates[0]).days)
    if span <= 14:
        locator = mdates.DayLocator(interval=max(1, span // 7))
    elif span <= 90:
        locator = mdates.WeekdayLocator(byweekday=mdates.MO)
    else:
        locator = mdates.MonthLocator()
    axes.xaxis.set_major_locator(locator)
    axes.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor="white")
    plt.close(figure)
    return buffer.getvalue()


# --------------------------------------------------------------------- PDF


def _summary_lines(summary: Summary, user: UserSettings) -> list[str]:
    lines = [
        f"Период:            {format_period(summary.start, summary.end)}",
        f"Измерений:         {summary.count} за {summary.days_covered} дн.",
        "",
        f"Среднее:           {summary.avg_sys}/{summary.avg_dia} мм рт. ст."
        + (f", пульс {summary.avg_pulse}" if summary.avg_pulse else ""),
        f"Разброс:           {summary.min_sys}/{summary.min_dia} — "
        f"{summary.max_sys}/{summary.max_dia}",
        f"Целевые значения:  < {user.target_sys}/{user.target_dia} — "
        f"достигнуты в {summary.in_target} из {summary.count} ({summary.target_share}%)",
    ]
    if summary.crisis:
        lines.append(f"Значений >= 180/120: {summary.crisis}")

    lines.append("")
    lines.append("Распределение по категориям (ESC/ESH):")
    for grade, count in summary.grades:
        share = round(count / summary.count * 100)
        lines.append(f"    {grade.title:<34} {count:>4}  ({share}%)")

    if summary.parts:
        lines.append("")
        lines.append("По времени суток:")
        for part in summary.parts:
            pulse = f", пульс {part.avg_pulse}" if part.avg_pulse else ""
            lines.append(
                f"    {part.title:<10} n={part.count:<4} "
                f"{part.avg_sys}/{part.avg_dia}{pulse}"
            )
    return lines


def summary_page(
    plt,
    measurements: Sequence[Measurement],
    summary: Summary,
    user: UserSettings,
    now: dt.datetime,
    owner: Optional[str] = None,
    health: Sequence[str] = (),
):
    """Титульная страница A4: шапка, цифры, график, примечание."""
    page = plt.figure(figsize=(8.27, 11.69), dpi=150)
    page.text(
        0.07, 0.955, "Дневник артериального давления",
        fontsize=16, fontweight="bold", va="top",
    )
    subtitle = f"Самоконтроль дома · сформировано {now:%d.%m.%Y}"
    if owner:
        subtitle = f"{owner} · {subtitle}"
    page.text(0.07, 0.928, subtitle, fontsize=9, color=COLOR_MUTED, va="top")

    lines = _summary_lines(summary, user) + list(health)
    page.text(
        0.07, 0.90, "\n".join(lines),
        fontsize=9, family="DejaVu Sans Mono", va="top", linespacing=1.5,
    )

    if len(measurements) >= 2:
        # график начинается там, где закончился текст: строк бывает от 12 до 30
        top = min(0.66, max(0.36, 0.86 - len(lines) * 0.0165))
        _draw(page, measurements, user, rect=(0.10, 0.87, 0.11, top))

    page.text(
        0.07, 0.045,
        "Домашние измерения: артериальной гипертензией считают средние значения\n"
        "от 135/85 мм рт. ст. (на приёме у врача — от 140/90). Дневник заполнен\n"
        "самостоятельно и не является медицинским заключением.",
        fontsize=7.5, color=COLOR_MUTED, va="top", linespacing=1.6,
    )
    return page


def doctor_pdf(
    measurements: Sequence[Measurement],
    summary: Summary,
    user: UserSettings,
    now: dt.datetime,
    owner: Optional[str] = None,
    health: Sequence[str] = (),
) -> bytes:
    """Сводка для врача: титульная страница с графиком плюс таблица измерений."""
    plt = _pyplot()
    from matplotlib.backends.backend_pdf import PdfPages

    buffer = io.BytesIO()
    with PdfPages(buffer) as pdf:
        page = summary_page(plt, measurements, summary, user, now, owner, health)
        pdf.savefig(page)
        plt.close(page)

        rows = table_rows(measurements)
        header, rest = rows[:2], rows[2:]
        for start in range(0, len(rest), TABLE_ROWS_PER_PAGE):
            chunk = rest[start : start + TABLE_ROWS_PER_PAGE]
            page = plt.figure(figsize=(8.27, 11.69), dpi=150)
            page.text(
                0.06, 0.955,
                f"Измерения ({start + 1}–{start + len(chunk)} из {len(rest)})",
                fontsize=12, fontweight="bold", va="top",
            )
            page.text(
                0.06, 0.915, "\n".join(header + chunk),
                fontsize=7.5, family="DejaVu Sans Mono", va="top", linespacing=1.6,
            )
            pdf.savefig(page)
            plt.close(page)

    return buffer.getvalue()
