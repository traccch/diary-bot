"""Роутеры бота. Порядок важен: entry ловит любой свободный текст, он последний."""

from aiogram import Router

from . import common, entry, export, reminders, reports, voice


def build_router() -> Router:
    router = Router(name="root")
    router.include_router(common.router)
    router.include_router(reminders.router)
    router.include_router(reports.router)
    router.include_router(export.router)
    router.include_router(voice.router)
    router.include_router(entry.router)
    return router


__all__ = ["build_router"]
