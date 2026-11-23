"""Telegram-бот: принимает deep link, привязывает Telegram к запросу входа
и открывает мини-приложение с кнопкой «Получить код»."""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from config import Settings

WELCOME = (
    "<b>NSO Diary</b>\n\n"
    "Код для входа выдаётся только по ссылке с сайта — так дневник "
    "защищён от посторонних.\n\n"
    "<b>Как войти:</b>\n"
    "1. Откройте сайт дневника.\n"
    "2. Введите логин и пароль.\n"
    "3. Нажмите «Открыть Telegram» — я пришлю кнопку с кодом.\n"
    "4. Введите код на сайте.\n\n"
    "Кнопка «Получить код» ниже работает только тогда, когда вы "
    "пришли по ссылке с сайта."
)


def site_keyboard(url: str) -> InlineKeyboardMarkup:
    """Кнопка на сайт — если человек открыл бота без ссылки на вход."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть сайт дневника", url=url)]]
    )


def webapp_keyboard(url: str, text: str = "Получить код") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))]]
    )


def build_router(settings: Settings, store) -> Router:
    router = Router(name="nso-diary")

    @router.message(CommandStart())
    async def on_start(message: Message, command: CommandObject) -> None:
        payload = (command.args or "").strip()

        if payload.startswith("auth_"):
            request_id = payload[len("auth_"):]
            request = store.get_auth_request(request_id) if store else None

            if not request:
                await message.answer(
                    "Ссылка для входа не найдена или устарела.\n"
                    "Вернитесь на сайт и начните вход заново."
                )
                return

            if message.from_user:
                store.update_auth_request(request_id, telegram_id=message.from_user.id)

            await message.answer(
                "Подтверждаем вход в <b>NSO Diary</b>.\n\n"
                "Нажмите кнопку ниже — откроется мини-приложение, "
                "в нём будет код для сайта.",
                reply_markup=webapp_keyboard(f"{settings.webapp_url}?req={request_id}"),
            )
            return

        # Бот открыт без ссылки на вход: мини-приложение не сможет выдать код,
        # поэтому ведём человека на сайт, а не в пустое окно.
        await message.answer(WELCOME, reply_markup=site_keyboard(settings.site_origin))

    @router.message(Command("code", "start_app", "app"))
    async def on_code(message: Message) -> None:
        await message.answer(
            "Чтобы получить код, начните вход на сайте: там нажмите "
            "«Открыть Telegram» — я пришлю ссылку с нужными данными.",
            reply_markup=site_keyboard(settings.site_origin),
        )

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        await message.answer(WELCOME, reply_markup=site_keyboard(settings.site_origin))

    @router.message()
    async def on_any(message: Message) -> None:
        await message.answer(
            "Я выдаю код только по ссылке с сайта. Откройте дневник, "
            "войдите и нажмите «Открыть Telegram».",
            reply_markup=site_keyboard(settings.site_origin),
        )

    return router


def build_bot(settings: Settings, store) -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(settings, store))
    return bot, dispatcher


async def setup_bot(bot: Bot, settings: Settings) -> None:
    """Постоянная кнопка меню в чате — быстрый доступ к дневнику."""
    try:
        await asyncio.wait_for(
            bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Дневник",
                    web_app=WebAppInfo(url=settings.webapp_url),
                )
            ),
            timeout=10,
        )
    except Exception:  # noqa: BLE001 — кнопка не критична для работы
        pass
