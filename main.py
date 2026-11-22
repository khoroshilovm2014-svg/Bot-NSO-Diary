"""NSO Diary API — точка входа.

Запуск:
    python main.py           — обычный старт (Render, Hugging Face, VPS)
    python main.py --demo    — без базы и Telegram, ученик demo/demo

Порт берётся из PORT (Render/HF) или SERVER_PORT (Pterodactyl).
"""

from __future__ import annotations

import argparse
import os
import sys

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="NSO Diary API")
    parser.add_argument("--demo", action="store_true", help="без Supabase и Telegram")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    if args.demo:
        os.environ["DEMO_MODE"] = "1"
        os.environ["SUPABASE_URL"] = ""
        os.environ["SUPABASE_SERVICE_KEY"] = ""
        os.environ["BOT_TOKEN"] = ""

    port = args.port or int(os.environ.get("PORT") or os.environ.get("SERVER_PORT") or "8000")

    from config import diagnostics, get_settings

    settings = get_settings()

    print("=" * 56, flush=True)
    print(" NSO Diary API", flush=True)
    print("=" * 56, flush=True)
    print(f" Порт:      {port}", flush=True)
    print(f" База:      {'Supabase' if settings.db_enabled else 'в памяти'}", flush=True)
    print(f" Бот:       {'включён' if settings.bot_enabled else 'выключен'}", flush=True)
    print(f" Публично:  {settings.public_base}", flush=True)
    print("-" * 56, flush=True)
    for line in diagnostics():
        print(f" {line}", flush=True)
    print("=" * 56, flush=True)

    if not settings.db_enabled:
        print(flush=True)
        print("! Supabase не подключён — данные будут в памяти и исчезнут", flush=True)
        print("  при перезапуске. Проверьте переменные окружения.", flush=True)

    if not settings.public_base.startswith("https://"):
        print(flush=True)
        print("! PUBLIC_BASE не начинается с https://", flush=True)
        print("  Telegram не сможет открыть мини-приложение.", flush=True)
        print("  Впишите адрес сервиса, например https://xxx.onrender.com", flush=True)

    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )


if __name__ == "__main__":
    main()
