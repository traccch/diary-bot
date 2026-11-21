"""График трат: по дням и по категориям. Требует matplotlib, как и давление.

Две панели на одной картинке, а не две оси на одном поле: сверху — когда
уходили деньги, снизу — на что. Долги и накопления сюда не попадают: это
перекладывание денег, а не траты, и в сводке их считают так же.
"""

from __future__ import annotations

import datetime as dt
import io
from typing import Sequence

from ..pressure.charts import (
    COLOR_INK,
    COLOR_MUTED,
    COLOR_TARGET,
    _pyplot,
    _style_axes,
    available,
)
from .db import CategoryTotal

COLOR_SPENT = "#eb6834"

#: Сколько категорий рисовать отдельно; остальные сливаются в «Остальное».
TOP_CATEGORIES = 7
#: Дольше этого периода дневные столбики превращаются в частокол — считаем по месяцам.
DAILY_LIMIT_DAYS = 92

MONTHS_SHORT = (
    "янв", "фев", "мар", "апр", "май", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)

__all__ = ["available", "money_png", "fill_days", "by_month", "top_categories"]


def fill_days(
    daily: Sequence[tuple[dt.date, int]], start: dt.date, end: dt.date
) -> list[tuple[dt.date, int]]:
    """Дни без трат — нулевые столбики, иначе календарь на графике врёт."""
    known = dict(daily)
    days = (end - start).days + 1
    return [
        (start + dt.timedelta(days=offset), known.get(start + dt.timedelta(days=offset), 0))
        for offset in range(days)
    ]


def by_month(daily: Sequence[tuple[dt.date, int]]) -> list[tuple[dt.date, int]]:
    """Сворачивает дни в месяцы: ключ — первое число месяца."""
    months: dict[dt.date, int] = {}
    for day, amount in daily:
        key = day.replace(day=1)
        months[key] = months.get(key, 0) + amount
    return sorted(months.items())


def top_categories(
    totals: Sequence[CategoryTotal], limit: int = TOP_CATEGORIES
) -> list[tuple[str, int]]:
    """Крупнейшие категории плюс «Остальное», чтобы длинный хвост не съел картинку."""
    ordered = sorted(totals, key=lambda item: item.total, reverse=True)
    head = [(item.name, item.total) for item in ordered[:limit]]
    rest = sum(item.total for item in ordered[limit:])
    if rest:
        head.append(("Остальное", rest))
    return head


def _money(minor: float, currency: str) -> str:
    whole = int(round(minor / 100))
    return f"{whole:,}".replace(",", " ") + f" {currency}"


def _axis_money(value: float, _position) -> str:
    whole = int(round(value / 100))
    if abs(whole) >= 10_000:
        return f"{whole / 1000:.0f} тыс"
    return f"{whole:,}".replace(",", " ")


def money_png(
    daily: Sequence[tuple[dt.date, int]],
    categories: Sequence[CategoryTotal],
    start: dt.date,
    end: dt.date,
    title: str,
    currency: str = "₽",
) -> bytes:
    """Картинка для чата. Суммы — в копейках, как в базе."""
    if not categories:
        raise ValueError("за период нет трат")

    plt = _pyplot()
    from matplotlib.ticker import FuncFormatter

    span = (end - start).days + 1
    monthly = span > DAILY_LIMIT_DAYS
    series = by_month(daily) if monthly else fill_days(daily, start, end)
    show_timeline = len(series) > 1
    bars = top_categories(categories)

    height = 3.0 + 0.32 * len(bars) + (2.6 if show_timeline else 0)
    figure = plt.figure(figsize=(9, height), dpi=170)
    total = sum(item.total for item in categories)
    figure.suptitle(title, x=0.02, ha="left", fontsize=13, fontweight="bold")
    figure.text(
        0.02,
        1 - 0.55 / height,
        f"Всего {_money(total, currency)}",
        ha="left",
        fontsize=9,
        color=COLOR_MUTED,
    )

    top_edge = 1 - 0.95 / height
    if show_timeline:
        ratios = [2.6, 0.32 * len(bars) + 0.6]
        grid = figure.add_gridspec(
            2, 1, height_ratios=ratios, hspace=0.38,
            left=0.2, right=0.86, bottom=0.6 / height, top=top_edge,
        )
        timeline = figure.add_subplot(grid[0])
        breakdown = figure.add_subplot(grid[1])
        partial = monthly and _month_unfinished(end)
        _draw_timeline(timeline, series, monthly, partial, currency, FuncFormatter)
    else:
        grid = figure.add_gridspec(
            1, 1, left=0.2, right=0.86, bottom=0.6 / height, top=top_edge
        )
        breakdown = figure.add_subplot(grid[0])

    _draw_breakdown(breakdown, bars, currency)

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor="white")
    plt.close(figure)
    return buffer.getvalue()


def _month_unfinished(end: dt.date) -> bool:
    """Идёт ли ещё месяц, которым кончается период."""
    return (end + dt.timedelta(days=1)).month == end.month


def _draw_timeline(
    axes, series, monthly: bool, partial: bool, currency: str, formatter
) -> None:
    import matplotlib.dates as mdates

    days = [dt.datetime.combine(day, dt.time(12)) for day, _ in series]
    values = [amount for _, amount in series]
    width = 24 if monthly else 0.72
    bars = axes.bar(days, values, width=width, color=COLOR_SPENT, zorder=2)

    # Незаконченный месяц рисуем бледнее и не учитываем в среднем: его
    # неполная сумма иначе выглядит как резкая экономия, которой не было.
    complete = values
    if partial and len(values) > 1:
        bars[-1].set_alpha(0.4)
        axes.annotate(
            "идёт", xy=(days[-1], values[-1]), xytext=(0, 3),
            textcoords="offset points", ha="center", fontsize=7.5, color=COLOR_MUTED,
        )
        complete = values[:-1]

    average = sum(complete) / len(complete)
    axes.axhline(average, color=COLOR_TARGET, linewidth=1, linestyle=(0, (4, 4)), zorder=1)
    axes.annotate(
        f"в среднем\n{_money(average, currency)}",
        xy=(1, average),
        xycoords=("axes fraction", "data"),
        xytext=(5, 0),
        textcoords="offset points",
        ha="left",
        va="center",
        fontsize=7.5,
        color=COLOR_MUTED,
        annotation_clip=False,
    )

    axes.set_title(
        "По месяцам" if monthly else "По дням", loc="left", fontsize=9, color=COLOR_INK
    )
    axes.yaxis.set_major_formatter(formatter(_axis_money))
    _style_axes(axes)

    if monthly:
        axes.xaxis.set_major_locator(mdates.MonthLocator())
        axes.xaxis.set_major_formatter(
            formatter(lambda value, _: MONTHS_SHORT[mdates.num2date(value).month - 1])
        )
    else:
        span = (series[-1][0] - series[0][0]).days
        axes.xaxis.set_major_locator(
            mdates.DayLocator(interval=1)
            if span <= 10
            else mdates.WeekdayLocator(byweekday=mdates.MO)
        )
        axes.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))


def _draw_breakdown(axes, bars: Sequence[tuple[str, int]], currency: str) -> None:
    names = [name for name, _ in bars][::-1]
    values = [amount for _, amount in bars][::-1]
    positions = range(len(bars))
    axes.barh(list(positions), values, height=0.62, color=COLOR_SPENT, zorder=2)

    top = max(values)
    for position, value in zip(positions, values):
        axes.text(
            value + top * 0.015,
            position,
            _money(value, currency),
            va="center",
            ha="left",
            fontsize=8,
            color=COLOR_INK,
        )

    axes.set_yticks(list(positions))
    axes.set_yticklabels(names, fontsize=8.5, color=COLOR_INK)
    axes.set_xlim(0, top * 1.28)
    axes.set_xticks([])
    axes.set_title("По категориям", loc="left", fontsize=9, color=COLOR_INK)
    for side in ("top", "right", "bottom"):
        axes.spines[side].set_visible(False)
    axes.spines["left"].set_color("#e6e5e1")
    axes.tick_params(axis="y", length=0)
