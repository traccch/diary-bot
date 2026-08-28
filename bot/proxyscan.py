"""Поиск локального прокси, который уже поднят на этой машине.

VPN-приложения (Happ, v2rayN, Nekoray, sing-box, Clash) поднимают у себя
локальный прокси и слушают его на каком-то порту. Порт у каждого свой, а
человеку, который просто хочет, чтобы работало, знать их неоткуда. Поэтому
бот проходит по известным портам сам и берёт первый, через который
действительно достучался до Telegram.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

#: Порты, на которых чаще всего сидят локальные прокси, и схемы к ним.
#: Порядок неслучаен: сверху то, что встречается чаще.
KNOWN_PORTS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (2080, ("socks5", "http")),   # Happ, Nekoray, sing-box
    (10808, ("socks5",)),          # v2rayN — socks
    (10809, ("http",)),            # v2rayN — http
    (7890, ("http", "socks5")),    # Clash — общий порт
    (7891, ("socks5",)),           # Clash — socks
    (1080, ("socks5",)),           # классика
    (20170, ("socks5", "http")),   # Hiddify
    (8080, ("http",)),
)

#: Сколько ждать ответа от порта: локальный прокси отвечает мгновенно.
PORT_TIMEOUT = 0.5
#: А сколько — от самого Telegram через него.
CHECK_TIMEOUT = 6.0

CHECK_URL = "https://api.telegram.org"


async def port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Слушает ли кто-нибудь этот порт."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=PORT_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def works(url: str) -> bool:
    """Проходит ли через этот прокси запрос к Telegram."""
    try:
        import aiohttp
    except ImportError:  # pragma: no cover - aiohttp приезжает вместе с aiogram
        return False

    try:
        from aiohttp_socks import ProxyConnector
    except ImportError:
        ProxyConnector = None  # type: ignore[assignment]

    try:
        if url.startswith("socks"):
            if ProxyConnector is None:
                return False
            connector = ProxyConnector.from_url(url)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(CHECK_URL, timeout=aiohttp.ClientTimeout(total=CHECK_TIMEOUT)):
                    return True
        async with aiohttp.ClientSession() as session:
            async with session.get(
                CHECK_URL, proxy=url, timeout=aiohttp.ClientTimeout(total=CHECK_TIMEOUT)
            ):
                return True
    except Exception:  # noqa: BLE001 - любая осечка означает «этот не годится»
        return False


@dataclass(frozen=True)
class Probe:
    """Что нашлось на одном порту."""

    port: int
    open: bool
    url: str = ""  # заполнен, если через него видно Telegram

    @property
    def good(self) -> bool:
        return bool(self.url)


async def scan(
    ports: Sequence[tuple[int, tuple[str, ...]]] = KNOWN_PORTS
) -> list[Probe]:
    """Проходит по всем портам и рассказывает про каждый.

    Отдельно от find(), потому что человеку важно не только «нашёл или нет»,
    но и «а работает ли вообще моё VPN-приложение»: закрытые порты по всему
    списку означают, что на этой машине его просто нет.
    """
    found: list[Probe] = []
    for port, schemes in ports:
        if not await port_open(port):
            found.append(Probe(port, False))
            continue
        working = ""
        for scheme in schemes:
            url = f"{scheme}://127.0.0.1:{port}"
            logger.debug("Пробую прокси %s", url)
            if await works(url):
                working = url
                break
        found.append(Probe(port, True, working))
    return found


async def find(ports: Sequence[tuple[int, tuple[str, ...]]] = KNOWN_PORTS) -> Optional[str]:
    """Первый локальный прокси, через который видно Telegram. None — нет такого."""
    for probe in await scan(ports):
        if probe.good:
            logger.info("Нашёл локальный прокси: %s", probe.url)
            return probe.url
    return None
