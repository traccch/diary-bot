"""Выгрузки: CSV для таблиц и текстовая сводка (запасной вариант вместо PDF)."""

from __future__ import annotations

import csv
import datetime as dt
import io
from typing import Sequence

from .classify import classify
from .db import Measurement, UserSettings
from .formatting import format_period, measurements_word
from .stats import Summary


def csv_bytes(measurements: Sequence[Measurement]) -> bytes:
    """CSV с точкой с запятой и BOM — Excel открывает без плясок с кодировкой."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["дата", "время", "верхнее", "нижнее", "пульс", "категория", "комментарий"])
    for measurement in measurements:
        grade = classify(measurement.systolic, measurement.diastolic)
        writer.writerow(
            [
                f"{measurement.measured_at:%d.%m.%Y}",
                f"{measurement.measured_at:%H:%M}",
                measurement.systolic,
                measurement.diastolic,
                measurement.pulse or "",
                grade.title,
                measurement.note,
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")


def text_report(
    measurements: Sequence[Measurement],
    summary: Summary,
    user: UserSettings,
    now: dt.datetime,
) -> bytes:
    """То же, что PDF, но обычным текстом — если графики недоступны."""
    lines = [
        "ДНЕВНИК АРТЕРИАЛЬНОГО ДАВЛЕНИЯ",
        f"Самоконтроль дома · сформировано {now:%d.%m.%Y}",
        "",
        f"Период:            {format_period(summary.start, summary.end)}",
        f"Измерений:         {summary.count} за {summary.days_covered} дн.",
        f"Среднее:           {summary.avg_sys}/{summary.avg_dia}"
        + (f", пульс {summary.avg_pulse}" if summary.avg_pulse else ""),
        f"Разброс:           {summary.min_sys}/{summary.min_dia} — "
        f"{summary.max_sys}/{summary.max_dia}",
        f"Целевые значения:  < {user.target_sys}/{user.target_dia} — достигнуты "
        f"в {summary.in_target} из {summary.count} ({summary.target_share}%)",
        "",
        "Распределение по категориям (ESC/ESH):",
    ]
    for grade, count in summary.grades:
        share = round(count / summary.count * 100)
        lines.append(f"    {grade.title:<34} {count:>4}  ({share}%)")

    if summary.parts:
        lines.append("")
        lines.append("По времени суток:")
        for part in summary.parts:
            pulse = f", пульс {part.avg_pulse}" if part.avg_pulse else ""
            lines.append(
                f"    {part.title:<10} n={part.count:<4} {part.avg_sys}/{part.avg_dia}{pulse}"
            )

    lines.extend(
        [
            "",
            f"ИЗМЕРЕНИЯ ({summary.count} {measurements_word(summary.count)})",
            f"{'Дата':<11}{'Время':<8}{'САД':>5}{'ДАД':>6}{'Пульс':>7}  "
            f"{'Категория':<24}Комментарий",
            "-" * 100,
        ]
    )
    for measurement in measurements:
        grade = classify(measurement.systolic, measurement.diastolic)
        lines.append(
            f"{measurement.measured_at:%d.%m.%Y} {measurement.measured_at:%H:%M}   "
            f"{measurement.systolic:>5}{measurement.diastolic:>6}"
            f"{(measurement.pulse or '—'):>7}  {grade.title[:24]:<24}{measurement.note}"
        )

    lines.extend(
        [
            "",
            "Домашние измерения: артериальной гипертензией считают средние значения",
            "от 135/85 мм рт. ст. (на приёме у врача — от 140/90). Дневник заполнен",
            "самостоятельно и не является медицинским заключением.",
        ]
    )
    return "\n".join(lines).encode("utf-8-sig")
