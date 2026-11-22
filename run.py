"""Запуск API и бота.

    python run.py            — как в .env (Supabase + Telegram)
    python run.py --demo     — без базы и бота: ученик demo/demo
    python run.py --port 9000
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from app.config import get_settings

# Консоль Windows по умолчанию cp1251 — иначе print с юникодом падает.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="NSO Diary API")
    parser.add_argument("--demo", action="store_true", help="без Supabase и Telegram, ученик demo/demo")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--reload", action="store_true", help="автоперезапуск при изменении кода")
    args = parser.parse_args()

    if args.demo:
        # Пустые строки важнее .env: load_dotenv не перезаписывает уже заданные ключи.
        os.environ["DEMO_MODE"] = "1"
        os.environ["SUPABASE_URL"] = ""
        os.environ["SUPABASE_SERVICE_KEY"] = ""
        os.environ["BOT_TOKEN"] = ""

    if args.reload:
        os.environ["RELOAD"] = "1"

    settings = get_settings()
    port = args.port or int(os.environ.get("PORT", "8000"))

    print(f"NSO Diary API   -> http://127.0.0.1:{port}")
    print(f"Мини-приложение -> {settings.webapp_url}")
    print(f"База: {'Supabase' if settings.db_enabled else 'в памяти (демо)'}")
    print(f"Бот:  {'включён' if settings.bot_enabled else 'выключен'}")

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=port,
        reload=args.reload,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
