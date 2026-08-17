"""Обмен данными с внешним ИИ: выгрузка JSON и разбор правленого файла.

Смысл круга такой: бот отдаёт дневник одним файлом; человек скармливает его
любому ИИ («проверь, нет ли опечаток»); ИИ возвращает тот же файл с правками;
бот показывает, что именно изменится, и применяет только после подтверждения.
Ничего не удаляется молча.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from . import metrics
from .db import Measurement, Metric

FORMAT = "pressure-export"
VERSION = 1

#: Больше — это уже не дневник, а чей-то чужой файл.
MAX_ROWS = 5000

#: Границы правдоподобия: за ними это не измерение, а опечатка.
SYS_RANGE = (60, 260)
DIA_RANGE = (30, 200)
PULSE_RANGE = (25, 220)

INSTRUCTIONS = (
    "Это выгрузка дневника артериального давления из телеграм-бота. Проверь "
    "измерения на опечатки: перепутанные верхнее и нижнее (80/120), лишний ноль "
    "(1200 вместо 120), пульс в поле давления, дубли одной записи, время из "
    "будущего. Верни ЭТОТ ЖЕ JSON целиком, изменив только ошибочные поля. У "
    "существующих записей обязательно сохрани id. Новое измерение добавляй без "
    "поля id. Чтобы удалить запись, добавь ей \"delete\": true. Время — "
    "ГГГГ-ММ-ДД ЧЧ:ММ. Медицинских выводов делать не нужно — только явные "
    "ошибки ввода."
)


class ImportError_(ValueError):
    """Файл не похож на нашу выгрузку."""


def _in_range(value: Any, bounds: tuple[int, int]) -> Optional[int]:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return number if bounds[0] <= number <= bounds[1] else None


def _field(row: dict, key: str, bounds: tuple[int, int]) -> tuple[Optional[int], bool]:
    """Значение поля и признак «всё в порядке».

    Поля нет или оно null — (None, True), менять нечего. Поле есть, но в нём
    чепуха — (None, False): такую строку надо не проглотить молча, а показать
    в числе неразобранных.
    """
    if key not in row or row[key] is None:
        return None, True
    value = _in_range(row[key], bounds)
    return value, value is not None


def _to_moment(value: Any) -> Optional[dt.datetime]:
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(str(value), pattern)
        except (TypeError, ValueError):
            continue
    return None


def dump(
    measurements: Sequence[Measurement],
    health: Sequence[Metric],
    target: tuple[int, int],
    timezone: str,
    now: dt.datetime,
) -> bytes:
    """Выгрузка со всем контекстом: цель, часовой пояс, показатели здоровья."""
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "_instructions": INSTRUCTIONS,
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "timezone": timezone,
        "target": {"systolic": target[0], "diastolic": target[1]},
        "measurements": [
            {
                "id": item.id,
                "measured_at": item.measured_at.strftime("%Y-%m-%d %H:%M"),
                "systolic": item.systolic,
                "diastolic": item.diastolic,
                "pulse": item.pulse,
                "note": item.note,
            }
            for item in measurements
        ],
        "metrics": [
            {
                "kind": item.kind,
                "date": item.on_date.isoformat(),
                "value": metrics.chart_value(item.kind, item.value),
                "unit": metrics.BY_KEY[item.kind].axis
                if item.kind in metrics.BY_KEY
                else "",
            }
            for item in health
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


@dataclass(frozen=True)
class Change:
    """Одна правка. Тип: update / create / delete."""

    kind: str
    measurement_id: Optional[int]
    systolic: Optional[int] = None
    diastolic: Optional[int] = None
    pulse: Optional[int] = None
    measured_at: Optional[dt.datetime] = None
    note: Optional[str] = None
    before: Optional[Measurement] = None


@dataclass(frozen=True)
class Plan:
    changes: tuple[Change, ...]
    skipped: int
    unchanged: int

    def of(self, kind: str) -> list[Change]:
        return [change for change in self.changes if change.kind == kind]

    def __bool__(self) -> bool:
        return bool(self.changes)


def parse(raw: bytes, existing: Sequence[Measurement], now: dt.datetime) -> Plan:
    """Сравнивает присланный файл с дневником и собирает список правок."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ImportError_("Это не JSON — пришли файл, который отдал /export") from exc

    if not isinstance(data, dict) or "measurements" not in data:
        raise ImportError_(
            "В файле нет списка measurements — похоже, это другой файл"
        )

    rows = data.get("measurements")
    if not isinstance(rows, list):
        raise ImportError_("Поле measurements должно быть списком")
    if len(rows) > MAX_ROWS:
        raise ImportError_(f"Слишком много записей: {len(rows)}, максимум {MAX_ROWS}")

    by_id = {item.id: item for item in existing}
    changes: list[Change] = []
    skipped = 0
    unchanged = 0

    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue

        raw_id = row.get("id")
        measurement_id = raw_id if isinstance(raw_id, int) else None
        current = by_id.get(measurement_id) if measurement_id is not None else None

        if row.get("delete"):
            if current is not None:
                changes.append(Change("delete", measurement_id, before=current))
            else:
                skipped += 1
            continue

        systolic, systolic_ok = _field(row, "systolic", SYS_RANGE)
        diastolic, diastolic_ok = _field(row, "diastolic", DIA_RANGE)
        pulse, pulse_ok = _field(row, "pulse", PULSE_RANGE)

        moment = _to_moment(row.get("measured_at")) if row.get("measured_at") else None
        time_ok = not row.get("measured_at") or moment is not None

        note = row.get("note")
        note = str(note).strip()[:200] if isinstance(note, str) else None

        # Верхнее должно быть выше нижнего — иначе модель что-то напутала сама.
        swapped = (
            systolic is not None and diastolic is not None and systolic <= diastolic
        )
        if not (systolic_ok and diastolic_ok and pulse_ok and time_ok) or swapped:
            skipped += 1
            continue

        if current is None:
            if systolic is None or diastolic is None:
                skipped += 1
                continue
            changes.append(
                Change("create", None, systolic, diastolic, pulse, moment or now, note or "")
            )
            continue

        if (
            (systolic is None or systolic == current.systolic)
            and (diastolic is None or diastolic == current.diastolic)
            and (pulse is None or pulse == current.pulse)
            and (moment is None or moment == current.measured_at)
            and (note is None or note == current.note)
        ):
            unchanged += 1
            continue

        changes.append(
            Change(
                "update",
                measurement_id,
                systolic if systolic != current.systolic else None,
                diastolic if diastolic != current.diastolic else None,
                pulse if pulse != current.pulse else None,
                moment if moment != current.measured_at else None,
                note if note != current.note else None,
                before=current,
            )
        )

    # Записи, которых в файле не оказалось, не трогаем: ИИ мог прислать кусок.
    return Plan(tuple(changes), skipped, unchanged)
