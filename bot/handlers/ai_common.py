"""Общее для ИИ-путей: сохранение разобранных записей и ответ пользователю."""

from __future__ import annotations

import datetime as dt

from ..ai import AiResult
from ..classify import alert
from ..db import Database, UserSettings
from ..formatting import esc, render_measurement, render_metric

NO_AI = (
    "🤖 Это умеет только версия с подключённым ИИ, а ключа нет.\n\n"
    "Бесплатный ключ берётся за минуту на "
    '<a href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</a> '
    "(нужен только аккаунт Google, карта не нужна). Впиши его в файл "
    "<code>.env</code> строкой <code>AI_API_KEY=…</code> и перезапусти бота."
)


async def save_ai_entry(
    db: Database, user: UserSettings, now: dt.datetime, result: AiResult
) -> str:
    """Записывает разобранное моделью и собирает ответ пользователю."""
    blocks: list[str] = []

    for item in result.measurements:
        measurement = await db.add_measurement(
            user_id=user.user_id,
            systolic=item.systolic,
            diastolic=item.diastolic,
            pulse=item.pulse,
            measured_at=item.measured_at,
            note=item.note,
        )
        blocks.append(render_measurement(measurement, now))

        warning = alert(measurement.systolic, measurement.diastolic, measurement.pulse)
        if warning:
            blocks.append(warning)

    metric_lines = []
    for item in result.metrics:
        metric = await db.set_metric(user.user_id, item.kind, item.on_date, item.value)
        metric_lines.append(render_metric(metric, now.date()))
    if metric_lines:
        blocks.append("\n".join(metric_lines))

    if result.transcript:
        blocks.append(f"<i>Услышал: {esc(result.transcript)}</i>")

    return "\n\n".join(blocks)
