"""Форматирование раздела «Давление»: карточки измерений и показателей."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from ..formatting import esc, format_date, format_moment, plural, short_moment
from . import metrics
from .classify import classify
from .db import Measurement, Metric


def measurements_word(count: int) -> str:
    return plural(count, "измерение", "измерения", "измерений")


def pulse_text(pulse: Optional[int]) -> str:
    return f"♥ {pulse}" if pulse else ""


def render_measurement(
    measurement: Measurement, now: Optional[dt.datetime] = None, with_id: bool = True
) -> str:
    """Карточка измерения для ответа бота."""
    grade = classify(measurement.systolic, measurement.diastolic)
    head = f"{grade.icon} <b>{measurement.bp}</b>"
    if measurement.pulse:
        head += f" · {pulse_text(measurement.pulse)}"
    lines = [head, f"<i>{grade.title}</i>"]
    if measurement.note:
        lines.append(f"💬 {esc(measurement.note)}")
    tail = format_moment(measurement.measured_at, now)
    if with_id:
        tail += f" · #{measurement.id}"
    lines.append(f"<i>{tail}</i>")
    return "\n".join(lines)


def render_metric(metric: Metric, today: Optional[dt.date] = None) -> str:
    """Строка подтверждения записанного показателя."""
    kind = metrics.kind_of(metric.kind)
    if kind is None:
        return ""
    value = metrics.format_value(kind.key, metric.value)
    extra = f" <i>({metric.extra.replace('-', '–')})</i>" if metric.extra else ""
    when = ""
    if today is not None and metric.on_date != today:
        when = f" <i>· {format_date(metric.on_date, today)}</i>"
    return f"{kind.icon} {kind.title}: <b>{value}</b>{extra}{when}"


def render_line(measurement: Measurement, now: Optional[dt.datetime] = None) -> str:
    """Строка для списка последних измерений."""
    grade = classify(measurement.systolic, measurement.diastolic)
    pulse = f" ♥{measurement.pulse}" if measurement.pulse else ""
    note = f" · {esc(measurement.note)}" if measurement.note else ""
    return (
        f"{grade.icon} <code>{short_moment(measurement.measured_at, now)}</code>  "
        f"<b>{measurement.bp}</b>{pulse}{note} <i>#{measurement.id}</i>"
    )
