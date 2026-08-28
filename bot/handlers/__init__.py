"""Сборка роутеров. Порядок важен: router ловит свободный текст, он последний."""

from aiogram import Router

from ..car import handlers as car
from ..english import handlers as english
from ..money import handlers as money
from ..pressure import handlers as pressure
from . import common, health, hub, menu, reminders, router as shared, transfer, update


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(common.router)
    root.include_router(menu.router)
    root.include_router(hub.router)
    root.include_router(transfer.router)
    root.include_router(update.router)
    root.include_router(reminders.router)
    root.include_router(health.router)
    root.include_router(car.router)
    root.include_router(pressure.build_router())
    root.include_router(money.build_router())
    root.include_router(english.build_router())
    root.include_router(shared.router)
    return root


__all__ = ["build_router"]
