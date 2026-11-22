"""Telegram-бот: принимает deep link, привязывает Telegram к запросу входа
и открывает мини-приложение с кнопкой «Получить код»."""

from __future__ import annotations

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
    "Я выдаю код для входа в дневник.\n\n"
    "Как это работает:\n"
    "1. Введите логин и пароль на сайте.\n"
    "2. Нажмите «Открыть Telegram» — откроюсь я.\n"
    "3. В мини-приложении нажмите «Получить код».\n"
    "4. Введите код на сайте — и вы внутри."
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

        await message.answer(WELCOME, reply_markup=webapp_keyboard(settings.webapp_url))

    @router.message(Command("code", "start_app", "app"))
    async def on_code(message: Message) -> None:
        await message.answer(
            "Откройте мини-приложение и нажмите «Получить код».",
            reply_markup=webapp_keyboard(settings.webapp_url),
        )

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        await message.answer(WELCOME, reply_markup=webapp_keyboard(settings.webapp_url))

    @router.message()
    async def on_any(message: Message) -> None:
        await message.answer(
            "Я понимаю только команды. Нажмите кнопку ниже, чтобы получить код.",
            reply_markup=webapp_keyboard(settings.webapp_url),
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
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Дневник",
                web_app=WebAppInfo(url=settings.webapp_url),
            )
        )
    except Exception:  # noqa: BLE001 — кнопка не критична для работы
        pass
