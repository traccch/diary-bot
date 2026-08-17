"""Агрегаты дневника: средние, разброс, время суток, динамика, текст сводки."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional, Sequence

from . import metrics
from .classify import ALL_GRADES, CRISIS_DIA, CRISIS_SYS, Grade, classify, in_target
from .db import Database, Measurement, Metric, UserSettings
from .formatting import (
    bar,
    days_word,
    esc,
    format_period,
    measurements_word,
    plural,
    short_moment,
    sparkline,
)

#: Ключ периода → (сколько дней, название)
PERIODS: tuple[tuple[str, int, str], ...] = (
    ("week", 7, "Неделя"),
    ("month", 30, "Месяц"),
    ("quarter", 90, "3 месяца"),
    ("all", 0, "Всё время"),
)

PERIOD_DAYS = {key: days for key, days, _ in PERIODS}
PERIOD_TITLES = {
    "week": "за неделю",
    "month": "за 30 дней",
    "quarter": "за 3 месяца",
    "all": "за всё время",
}

#: Границы времени суток: (ключ, название, час начала, час конца включительно)
DAY_PARTS: tuple[tuple[str, str, int, int], ...] = (
    ("morning", "Утро", 4, 11),
    ("afternoon", "День", 12, 17),
    ("evening", "Вечер", 18, 22),
    ("night", "Ночь", 23, 3),
)


def day_part(moment: dt.datetime) -> tuple[str, str]:
    hour = moment.hour
    for key, title, start, end in DAY_PARTS:
        if start <= end:
            if start <= hour <= end:
                return key, title
        elif hour >= start or hour <= end:
            return key, title
    return "night", "Ночь"


@dataclass(frozen=True)
class PartStat:
    key: str
    title: str
    count: int
    avg_sys: int
    avg_dia: int
    avg_pulse: Optional[int]


@dataclass(frozen=True)
class DayStat:
    date: dt.date
    count: int
    avg_sys: int
    avg_dia: int
    avg_pulse: Optional[int]
    max_sys: int


@dataclass(frozen=True)
class Summary:
    count: int
    start: dt.date
    end: dt.date
    days_covered: int
    avg_sys: int
    avg_dia: int
    avg_pulse: Optional[int]
    min_sys: int
    max_sys: int
    min_dia: int
    max_dia: int
    in_target: int
    crisis: int
    grades: list[tuple[Grade, int]] = field(default_factory=list)
    parts: list[PartStat] = field(default_factory=list)
    days: list[DayStat] = field(default_factory=list)
    peaks: list[Measurement] = field(default_factory=list)

    @property
    def target_share(self) -> int:
        return round(self.in_target / self.count * 100) if self.count else 0


def period_range(period: str, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """Границы периода в локальном времени пользователя."""
    end = now.replace(hour=23, minute=59, second=0, microsecond=0)
    days = PERIOD_DAYS.get(period, 30)
    if days <= 0:
        return dt.datetime(1970, 1, 1), end
    start = dt.datetime.combine(now.date() - dt.timedelta(days=days - 1), dt.time.min)
    return start, end


def _mean(values: Sequence[float]) -> int:
    return round(sum(values) / len(values))


def summarize(
    measurements: Sequence[Measurement], target_sys: int, target_dia: int
) -> Optional[Summary]:
    if not measurements:
        return None

    systolic = [m.systolic for m in measurements]
    diastolic = [m.diastolic for m in measurements]
    pulses = [m.pulse for m in measurements if m.pulse]

    counted: dict[str, int] = {}
    for measurement in measurements:
        grade = classify(measurement.systolic, measurement.diastolic)
        counted[grade.key] = counted.get(grade.key, 0) + 1
    grades = [
        (grade, counted[grade.key])
        for grade in sorted(ALL_GRADES, key=lambda item: -item.order)
        if grade.key in counted
    ]

    parts: list[PartStat] = []
    for key, title, _, _ in DAY_PARTS:
        chunk = [m for m in measurements if day_part(m.measured_at)[0] == key]
        if not chunk:
            continue
        chunk_pulses = [m.pulse for m in chunk if m.pulse]
        parts.append(
            PartStat(
                key=key,
                title=title,
                count=len(chunk),
                avg_sys=_mean([m.systolic for m in chunk]),
                avg_dia=_mean([m.diastolic for m in chunk]),
                avg_pulse=_mean(chunk_pulses) if chunk_pulses else None,
            )
        )

    by_date: dict[dt.date, list[Measurement]] = {}
    for measurement in measurements:
        by_date.setdefault(measurement.measured_at.date(), []).append(measurement)
    days = []
    for date in sorted(by_date):
        chunk = by_date[date]
        chunk_pulses = [m.pulse for m in chunk if m.pulse]
        days.append(
            DayStat(
                date=date,
                count=len(chunk),
                avg_sys=_mean([m.systolic for m in chunk]),
                avg_dia=_mean([m.diastolic for m in chunk]),
                avg_pulse=_mean(chunk_pulses) if chunk_pulses else None,
                max_sys=max(m.systolic for m in chunk),
            )
        )

    peaks = sorted(measurements, key=lambda m: (-m.systolic, -m.diastolic))[:3]

    return Summary(
        count=len(measurements),
        start=min(m.measured_at for m in measurements).date(),
        end=max(m.measured_at for m in measurements).date(),
        days_covered=len(by_date),
        avg_sys=_mean(systolic),
        avg_dia=_mean(diastolic),
        avg_pulse=_mean(pulses) if pulses else None,
        min_sys=min(systolic),
        max_sys=max(systolic),
        min_dia=min(diastolic),
        max_dia=max(diastolic),
        in_target=sum(
            1 for m in measurements if in_target(m.systolic, m.diastolic, target_sys, target_dia)
        ),
        crisis=sum(
            1 for m in measurements if m.systolic >= CRISIS_SYS or m.diastolic >= CRISIS_DIA
        ),
        grades=grades,
        parts=parts,
        days=days,
        peaks=peaks,
    )


def trend_line(current: Summary, previous: Optional[Summary]) -> Optional[str]:
    """Сравнение с предыдущим таким же периодом."""
    if previous is None or previous.count < 3 or current.count < 3:
        return None
    delta_sys = current.avg_sys - previous.avg_sys
    delta_dia = current.avg_dia - previous.avg_dia
    if abs(delta_sys) < 2 and abs(delta_dia) < 2:
        return (
            f"Столько же, сколько периодом раньше "
            f"({previous.avg_sys}/{previous.avg_dia}) — ровно."
        )
    icon = "🔻" if delta_sys + delta_dia < 0 else "🔺"
    word = "ниже" if delta_sys + delta_dia < 0 else "выше"
    return (
        f"{icon} Периодом раньше было {previous.avg_sys}/{previous.avg_dia} — "
        f"сейчас {word} на {abs(delta_sys)}/{abs(delta_dia)}."
    )


def render_summary(summary: Summary, user: UserSettings, trend: Optional[str]) -> list[str]:
    """Тело сводки без заголовка — общее для /stats и выгрузки врачу."""
    pulse = f" · ♥ {summary.avg_pulse}" if summary.avg_pulse else ""
    lines = [
        f"Среднее: <b>{summary.avg_sys}/{summary.avg_dia}</b>{pulse}",
        f"Разброс: {summary.min_sys}/{summary.min_dia} … {summary.max_sys}/{summary.max_dia}",
        f"В целевом (&lt;{user.target_sys}/{user.target_dia}): "
        f"<b>{summary.in_target}</b> из {summary.count} ({summary.target_share}%)",
    ]
    if trend:
        lines.append(trend)
    if summary.crisis:
        lines.append(
            f"‼️ Кризовых значений (≥{CRISIS_SYS}/{CRISIS_DIA}): <b>{summary.crisis}</b> — "
            "их стоит показать врачу отдельно."
        )

    lines.append("")
    lines.append("<b>Распределение</b>")
    top = max(count for _, count in summary.grades)
    width = max(len(grade.short) for grade, _ in summary.grades)
    for grade, count in summary.grades:
        share = round(count / summary.count * 100)
        lines.append(
            f"{grade.icon} <code>{grade.short.ljust(width)}</code> "
            f"{bar(count, top, 8)} {count} · {share}%"
        )

    if len(summary.parts) > 1:
        lines.append("")
        lines.append("<b>По времени суток</b>")
        width = max(len(part.title) for part in summary.parts)
        for part in summary.parts:
            part_pulse = f" ♥{part.avg_pulse}" if part.avg_pulse else ""
            lines.append(
                f"<code>{part.title.ljust(width)}</code> "
                f"<b>{part.avg_sys}/{part.avg_dia}</b>{part_pulse} "
                f"<i>({part.count})</i>"
            )

    if len(summary.days) > 2:
        chart = sparkline([day.avg_sys for day in summary.days])
        lines.append("")
        lines.append("<b>Динамика</b> (среднее верхнее по дням)")
        lines.append(f"<code>{chart}</code>")
        lines.append(
            f"<i>{summary.days[0].avg_sys} → {summary.days[-1].avg_sys} · "
            f"{summary.days_covered} {days_word(summary.days_covered)} с записями</i>"
        )

    return lines


async def build_report(
    db: Database, user: UserSettings, period: str, now: dt.datetime
) -> str:
    """Текст /stats за выбранный период."""
    start, end = period_range(period, now)
    measurements = await db.measurements_between(user.user_id, start, end)
    title = PERIOD_TITLES.get(period, "за 30 дней")

    if not measurements:
        total = await db.count_measurements(user.user_id)
        if total:
            return (
                f"🩺 <b>Давление {esc(title)}</b>\n\n"
                "За этот период записей нет — попробуй период пошире."
            )
        return (
            f"🩺 <b>Давление {esc(title)}</b>\n\n"
            "Дневник пока пуст. Отправь измерение: <code>120/80 68</code>"
        )

    summary = summarize(measurements, user.target_sys, user.target_dia)
    assert summary is not None

    trend = None
    days = PERIOD_DAYS.get(period, 30)
    if days > 0:
        previous = await db.measurements_between(
            user.user_id, start - dt.timedelta(days=days), start - dt.timedelta(minutes=1)
        )
        trend = trend_line(summary, summarize(previous, user.target_sys, user.target_dia))

    header = [
        f"🩺 <b>Давление {esc(title)}</b>",
        f"<i>{format_period(summary.start, summary.end)} · "
        f"{summary.count} {measurements_word(summary.count)}</i>",
        "",
    ]
    body = render_summary(summary, user, trend)
    body.extend(await build_health_block(db, user, measurements, start, end))
    return "\n".join(header + body)


# ------------------------------------------------------- показатели здоровья


@dataclass(frozen=True)
class MetricSummary:
    kind: metrics.MetricKind
    count: int
    average: float
    lowest: float
    highest: float
    latest: float
    latest_date: dt.date
    first: float
    values: list[tuple[dt.date, float]]

    @property
    def delta(self) -> float:
        """Насколько последнее значение отличается от первого за период."""
        return self.latest - self.first


@dataclass(frozen=True)
class Correlation:
    """Давление в дни с низким и высоким значением показателя.

    Порог — медиана самого пользователя: у «шестичасового» сна и у
    «восьмичасового» она разная, и фиксированная граница одному из них
    не сказала бы ничего.
    """

    kind: metrics.MetricKind
    threshold: float
    low_count: int
    low_sys: int
    low_dia: int
    high_count: int
    high_sys: int
    high_dia: int
    morning_only: bool

    @property
    def delta_sys(self) -> int:
        return self.low_sys - self.high_sys

    @property
    def delta_dia(self) -> int:
        return self.low_dia - self.high_dia

    @property
    def meaningful(self) -> bool:
        return abs(self.delta_sys) >= 3 or abs(self.delta_dia) >= 3


def summarize_metric(
    kind: metrics.MetricKind, values: Sequence[Metric]
) -> Optional[MetricSummary]:
    if not values:
        return None
    ordered = sorted(values, key=lambda item: item.on_date)
    numbers = [item.value for item in ordered]
    return MetricSummary(
        kind=kind,
        count=len(ordered),
        average=sum(numbers) / len(numbers),
        lowest=min(numbers),
        highest=max(numbers),
        latest=ordered[-1].value,
        latest_date=ordered[-1].on_date,
        first=ordered[0].value,
        values=[(item.on_date, item.value) for item in ordered],
    )


#: Меньше этого числа пар сравнение — гадание на кофейной гуще.
MIN_PAIRS = 10
MIN_GROUP = 4


def correlate(
    measurements: Sequence[Measurement],
    values: Sequence[Metric],
    kind: metrics.MetricKind,
    morning_only: bool = False,
) -> Optional[Correlation]:
    """Сравнивает давление в дни ниже и выше медианы показателя."""
    by_date = {item.on_date: item.value for item in values}
    pairs = [
        (by_date[m.measured_at.date()], m)
        for m in measurements
        if m.measured_at.date() in by_date
        and (not morning_only or day_part(m.measured_at)[0] == "morning")
    ]
    if len(pairs) < MIN_PAIRS:
        return None

    threshold = metrics.median([value for value, _ in pairs])
    low = [m for value, m in pairs if value <= threshold]
    high = [m for value, m in pairs if value > threshold]
    if len(low) < MIN_GROUP or len(high) < MIN_GROUP:
        return None

    return Correlation(
        kind=kind,
        threshold=threshold,
        low_count=len(low),
        low_sys=_mean([m.systolic for m in low]),
        low_dia=_mean([m.diastolic for m in low]),
        high_count=len(high),
        high_sys=_mean([m.systolic for m in high]),
        high_dia=_mean([m.diastolic for m in high]),
        morning_only=morning_only,
    )


def render_metric_line(summary: MetricSummary) -> str:
    kind = summary.kind
    average = metrics.format_value(kind.key, summary.average)
    if kind.key == metrics.WEIGHT.key:
        delta = summary.delta
        if abs(delta) < 0.15 or summary.count < 2:
            tail = "без изменений за период"
        else:
            sign = "−" if delta < 0 else "+"
            tail = f"{sign}{abs(delta):.1f}".replace(".", ",") + " кг за период"
        return (
            f"{kind.icon} <b>{kind.title}</b>: "
            f"{metrics.format_value(kind.key, summary.latest)} · {tail}"
        )

    # у шагов и пульса единица уже названа в заголовке строки — не повторяем
    average = metrics.format_value(
        kind.key, summary.average, short=kind.key != metrics.SLEEP.key
    )
    extra = {
        metrics.SLEEP.key: f"{summary.count} "
        + plural(summary.count, "ночь", "ночи", "ночей"),
        metrics.STEPS.key: f"{summary.count} "
        + plural(summary.count, "день", "дня", "дней"),
        metrics.RESTING_PULSE.key: f"{summary.count} "
        + plural(summary.count, "замер", "замера", "замеров"),
    }[kind.key]
    return f"{kind.icon} <b>{kind.title}</b>: {average} в среднем · {extra}"


def render_correlation(correlation: Correlation) -> list[str]:
    kind = correlation.kind
    # у сна короткая форма выглядит как время на часах («7:00»), а нужна длительность
    threshold = metrics.format_value(
        kind.key, correlation.threshold, short=kind.key == metrics.STEPS.key
    )

    if kind.key == metrics.SLEEP.key:
        header = "Сон и утреннее давление"
        low_title, high_title = f"Ночи до {threshold}", "Ночи длиннее"
        subject = "После коротких ночей"
    else:
        header = "Шаги и давление"
        low_title, high_title = f"Дни до {threshold} шагов", "Дни активнее"
        subject = "В малоподвижные дни"

    lines = [
        f"{kind.icon} <b>{header}</b>",
        f"{low_title} <i>(n={correlation.low_count})</i> — "
        f"<b>{correlation.low_sys}/{correlation.low_dia}</b>",
        f"{high_title} <i>(n={correlation.high_count})</i> — "
        f"<b>{correlation.high_sys}/{correlation.high_dia}</b>",
    ]
    if not correlation.meaningful:
        lines.append("<i>Разница в пределах погрешности.</i>")
        return lines

    # delta считается как «ниже порога минус выше порога»: плюс значит,
    # что в дни с меньшим значением показателя давление выше
    word = "выше" if correlation.delta_sys > 0 else "ниже"
    lines.append(
        f"<i>{subject} давление {word} на "
        f"{abs(correlation.delta_sys)}/{abs(correlation.delta_dia)}.</i>"
    )
    return lines


async def collect_health(
    db: Database,
    user: UserSettings,
    measurements: Sequence[Measurement],
    start: dt.datetime,
    end: dt.datetime,
) -> tuple[list[MetricSummary], list[Correlation]]:
    """Показатели за период и их связь с давлением."""
    summaries: list[MetricSummary] = []
    links: list[Correlation] = []
    stored: dict[str, list[Metric]] = {}

    for kind in metrics.ALL_KINDS:
        values = await db.metrics_between(user.user_id, kind.key, start.date(), end.date())
        stored[kind.key] = values
        summary = summarize_metric(kind, values)
        if summary is not None:
            summaries.append(summary)

    # Сон сравниваем только с утренними замерами: именно на них он и виден.
    for kind, morning_only in ((metrics.SLEEP, True), (metrics.STEPS, False)):
        correlation = correlate(measurements, stored[kind.key], kind, morning_only)
        if correlation is not None:
            links.append(correlation)

    return summaries, links


async def build_health_block(
    db: Database,
    user: UserSettings,
    measurements: Sequence[Measurement],
    start: dt.datetime,
    end: dt.datetime,
) -> list[str]:
    """Блок «Здоровье» для сводки в чате."""
    summaries, links = await collect_health(db, user, measurements, start, end)
    if not summaries:
        return []

    lines = ["", "<b>Здоровье</b>"]
    lines.extend(render_metric_line(summary) for summary in summaries)

    if links:
        lines.append("")
        lines.append("<b>Что с чем связано</b>")
        for index, correlation in enumerate(links):
            if index:
                lines.append("")
            lines.extend(render_correlation(correlation))

    return lines


def health_lines_plain(
    summaries: Sequence[MetricSummary], links: Sequence[Correlation]
) -> list[str]:
    """То же самое для PDF и текстового отчёта — без разметки и эмодзи."""
    if not summaries:
        return []

    lines = ["", "Показатели здоровья:"]
    for summary in summaries:
        value = metrics.format_value(summary.kind.key, summary.average)
        title = f"{summary.kind.title} (среднее):"
        lines.append(f"    {title:<26} {value}   n={summary.count}")
        if summary.kind.key == metrics.WEIGHT.key and summary.count > 1:
            delta = summary.delta
            sign = "-" if delta < 0 else "+"
            change = f"{abs(delta):.1f}".replace(".", ",")
            lines.append(
                f"    {'Вес (динамика):':<26} "
                f"{metrics.format_value(summary.kind.key, summary.first)} → "
                f"{metrics.format_value(summary.kind.key, summary.latest)} "
                f"({sign}{change} кг)"
            )

    for correlation in links:
        threshold = metrics.format_value(correlation.kind.key, correlation.threshold)
        when = "утреннее" if correlation.morning_only else "дневное"
        lines.append("")
        lines.append(
            f"Давление и «{correlation.kind.title.lower()}» "
            f"(порог — медиана {threshold}):"
        )
        lines.append(
            f"    ниже порога:  {correlation.low_sys}/{correlation.low_dia} "
            f"({when}, n={correlation.low_count})"
        )
        lines.append(
            f"    выше порога:  {correlation.high_sys}/{correlation.high_dia} "
            f"({when}, n={correlation.high_count})"
        )
    return lines


def render_peaks(summary: Summary, now: dt.datetime) -> list[str]:
    lines = ["<b>Самые высокие</b>"]
    for measurement in summary.peaks:
        grade = classify(measurement.systolic, measurement.diastolic)
        note = f" · {esc(measurement.note)}" if measurement.note else ""
        lines.append(
            f"{grade.icon} <code>{short_moment(measurement.measured_at, now)}</code> "
            f"<b>{measurement.bp}</b>{note}"
        )
    return lines
