"""Точка сборки FastAPI: API, мини-приложение и webhook бота."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import api
from config import get_settings
from db import build_store
from miniapp import MINIAPP_HTML  # noqa: E402,F401

settings = get_settings()
store = build_store(settings)
api._store = store
api._settings = settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.bot = None
    app.state.dispatcher = None

    api.ensure_demo_student()

    if settings.bot_enabled:
        from bot import build_bot, setup_bot

        bot, dispatcher = build_bot(settings, store)
        app.state.bot = bot
        app.state.dispatcher = dispatcher

        await setup_bot(bot, settings)

        if settings.public_base.startswith("https://"):
            try:
                # Без таймаута старт зависает, если Telegram недоступен.
                await asyncio.wait_for(
                    bot.set_webhook(settings.webhook_url, drop_pending_updates=False),
                    timeout=15,
                )
                print(f"[bot] webhook установлен: {settings.public_base}/tg/webhook/***", flush=True)
            except Exception as exc:  # noqa: BLE001 — Telegram может быть недоступен
                print(f"[bot] webhook НЕ установлен: {exc}", flush=True)

    yield

    if app.state.bot is not None:
        await app.state.bot.session.close()


app = FastAPI(
    title="NSO Diary API",
    description="Вход через логин/пароль или Госуслуги + подтверждение в Telegram.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins) or ["*"],
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=3600,
)

app.include_router(api.router)


@app.exception_handler(RuntimeError)
async def db_error_handler(_: Request, exc: RuntimeError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": "Ошибка базы данных", "error": str(exc)[:300]})


@app.post("/tg/webhook/{secret}", include_in_schema=False)
async def telegram_webhook(secret: str, request: Request) -> dict:
    if secret != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    bot = getattr(request.app.state, "bot", None)
    dispatcher = getattr(request.app.state, "dispatcher", None)
    if bot is None or dispatcher is None:
        raise HTTPException(status_code=503, detail="Bot disabled")

    from aiogram.types import Update

    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": bot})
    await dispatcher.feed_update(bot, update)
    return {"ok": True}


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/tg-app/")


from miniapp import render as render_miniapp  # noqa: E402


@app.get("/tg-app/", include_in_schema=False)
@app.get("/tg-app/index.html", include_in_schema=False)
async def miniapp_page() -> HTMLResponse:
    """Мини-приложение «Получить код» отдаётся прямо из кода."""
    return HTMLResponse(render_miniapp(settings.site_origin))
