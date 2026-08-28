"""Общее для тестов: база в памяти вместо файла на диске.

Тесты создают базу на каждый тест — это правильно, они не должны видеть чужие
записи. Но файл ради этого не нужен: на Windows создание файла, журнала WAL и
проверка антивирусом стоят дороже, чем сам тест, и полный прогон растягивается
с секунд до минут. Там, где проверяется именно файл (миграции старой базы,
размер на диске), нужен настоящий путь — для этого есть temp_db.
"""

from __future__ import annotations

import asyncio
import unittest

from bot.db import Database


def _quiet_runner(self) -> None:
    """Асинхронные тесты без отладочного режима asyncio.

    С версии 3.11 IsolatedAsyncioTestCase запускает цикл с debug=True, а в
    этом режиме asyncio снимает стек на каждую создаваемую корутину и
    будущее. На нашем наборе это пятьдесят тысяч снимков стека и треть
    всего времени прогона — при том, что предупреждения о медленных
    колбэках нам ничего не говорят: тесты и должны считать.
    """
    self._asyncioRunner = asyncio.Runner(debug=False)


unittest.IsolatedAsyncioTestCase._setupAsyncioRunner = _quiet_runner


def _cache_ssl_context() -> None:
    """Один набор корневых сертификатов на весь прогон, а не на каждый тест.

    Каждый фейковый бот в тестах поднимает свою http-сессию, а та читает с
    диска и разбирает все корневые сертификаты системы — двадцать пять
    миллисекунд на пустом месте, и так больше сотни раз. В сеть тесты не
    ходят, содержимое контекста им безразлично.
    """
    import ssl

    original = ssl.create_default_context
    cached: dict[tuple, ssl.SSLContext] = {}

    def create(*args, **kwargs):
        key = (args, tuple(sorted(kwargs.items())))
        try:
            if key not in cached:
                cached[key] = original(*args, **kwargs)
            return cached[key]
        except TypeError:  # неожиданные аргументы — пусть работает как обычно
            return original(*args, **kwargs)

    ssl.create_default_context = create


_cache_ssl_context()

MEMORY = ":memory:"


def memory_db(tz: str = "Europe/Moscow") -> Database:
    """База, которая живёт только в памяти процесса."""
    return Database(MEMORY, tz)
