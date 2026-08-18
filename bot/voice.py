"""Расшифровка голосовых сообщений.

Бот не разбирает речь сам: он отдаёт файл внешнему распознавателю, а дальше
работает уже обычный разбор текста. По умолчанию это whisper.cpp — он идёт
на процессоре, не требует видеокарты и не отправляет запись наружу, что для
дневника здоровья существенно.

Распознаватель подключается через .env и по умолчанию выключен: без него
бот просто попросит написать текстом.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

#: Дольше этого голосовые не расшифровываем: на слабом процессоре это минуты.
MAX_SECONDS = 120
TIMEOUT_SECONDS = 300


class TranscribeError(RuntimeError):
    """Расшифровать не вышло, причина — в тексте."""


class Transcriber(Protocol):
    """Что угодно, что умеет превратить файл с речью в строку."""

    @property
    def ready(self) -> bool: ...

    async def transcribe(self, audio: Path) -> str: ...


@dataclass(frozen=True)
class VoiceConfig:
    binary: str = ""
    model: str = ""
    language: str = "ru"

    @property
    def enabled(self) -> bool:
        return bool(self.binary and self.model)


class WhisperCppTranscriber:
    """Локальный whisper.cpp: вызывает бинарник и читает его вывод.

    Формат ogg/opus, в котором Telegram присылает голосовые, whisper.cpp
    не понимает — поэтому нужен ffmpeg, чтобы перегнать запись в WAV 16 кГц.
    """

    def __init__(self, config: VoiceConfig) -> None:
        self._config = config
        self._ffmpeg = shutil.which("ffmpeg") or ""

    @property
    def ready(self) -> bool:
        if not self._config.enabled:
            return False
        return (
            Path(self._config.binary).exists()
            and Path(self._config.model).exists()
            and bool(self._ffmpeg)
        )

    def why_not_ready(self) -> str:
        if not self._config.enabled:
            return "распознавание речи не настроено (VOICE_BINARY и VOICE_MODEL в .env)"
        if not Path(self._config.binary).exists():
            return f"не нашёл whisper.cpp по пути {self._config.binary}"
        if not Path(self._config.model).exists():
            return f"не нашёл модель по пути {self._config.model}"
        if not self._ffmpeg:
            return "не нашёл ffmpeg — без него голосовые не перекодировать"
        return ""

    async def transcribe(self, audio: Path) -> str:
        if not self.ready:
            raise TranscribeError(self.why_not_ready())

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "voice.wav"
            await self._run(
                [self._ffmpeg, "-y", "-i", str(audio), "-ar", "16000", "-ac", "1", str(wav)],
                "перекодировать запись",
            )
            output = await self._run(
                [
                    self._config.binary,
                    "-m", self._config.model,
                    "-f", str(wav),
                    "-l", self._config.language,
                    "-nt",          # без таймкодов
                    "-np",          # без служебного вывода
                    "-t", str(max(1, (os.cpu_count() or 2) - 1)),
                ],
                "расшифровать запись",
            )

        text = " ".join(line.strip() for line in output.splitlines() if line.strip())
        if not text:
            raise TranscribeError("в записи не разобрать слов")
        return text

    async def _run(self, args: list[str], what: str) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise TranscribeError(f"не успел {what} за {TIMEOUT_SECONDS} с") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise TranscribeError(f"не смог {what}: {exc}") from exc

        if process.returncode != 0:
            tail = stderr.decode("utf-8", "replace").strip()[-300:]
            raise TranscribeError(f"не смог {what}:\n{tail}")
        return stdout.decode("utf-8", "replace")


class DisabledTranscriber:
    """Заглушка: распознавание не настроено."""

    ready = False

    def why_not_ready(self) -> str:
        return "распознавание речи не настроено (VOICE_BINARY и VOICE_MODEL в .env)"

    async def transcribe(self, audio: Path) -> str:  # pragma: no cover - не вызывается
        raise TranscribeError(self.why_not_ready())


def build_transcriber(config: Optional[VoiceConfig]) -> Transcriber:
    if config is None or not config.enabled:
        return DisabledTranscriber()
    return WhisperCppTranscriber(config)


def clean_speech(text: str) -> str:
    """Приводит расшифровку к тому, что понимает разбор текста.

    Диктовка почти всегда звучит как «сто двадцать на восемьдесят»: числа
    whisper пишет цифрами, а вот «на» между ними и точку в конце убираем сами.
    """
    text = (text or "").strip()
    text = text.replace(" ", " ")
    return text.strip(" .!?\n\t")
