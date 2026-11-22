"""Точка входа NSO Diary.

Запуск:
    python main.py            — обычный старт (для Pterodactyl/NomadHost)
    python main.py --demo     — без базы и Telegram, ученик demo/demo
    python run.py             — локальная разработка с перезагрузкой

Если PUBLIC_BASE не задан и включён AUTO_TUNNEL, скрипт сам скачивает
cloudflared, поднимает HTTPS-туннель и подставляет его адрес.
Это нужно, потому что Telegram не открывает мини-приложения по http.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE_DIR = Path(__file__).resolve().parent

CLOUDFLARED_URLS = {
    "linux": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    "linux-arm64": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
    "windows": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
}

URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def log(message: str = "") -> None:
    print(message, flush=True)


def ensure_cloudflared() -> Path | None:
    """Возвращает путь к бинарнику cloudflared, скачивая его при необходимости."""
    exe = "cloudflared.exe" if platform.system() == "Windows" else "cloudflared"

    local = BASE_DIR / exe
    if local.exists():
        if platform.system() != "Windows":
            local.chmod(0o755)
        return local

    found = shutil.which("cloudflared")
    if found:
        return Path(found)

    key = "linux"
    machine = platform.machine().lower()
    if "aarch64" in machine or "arm64" in machine:
        key = "linux-arm64"
    if platform.system() == "Windows":
        key = "windows"

    url = CLOUDFLARED_URLS.get(key)
    if not url:
        return None

    log(f"      Скачиваю cloudflared ({key})...")
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=120) as response, open(local, "wb") as target:
            shutil.copyfileobj(response, target)
        local.chmod(0o755)
        return local
    except Exception as exc:  # noqa: BLE001
        log(f"      ! Не удалось скачать cloudflared: {exc}")
        return None


def start_tunnel(port: int, wait_seconds: int = 40) -> tuple[str | None, subprocess.Popen | None]:
    """Поднимает быстрый туннель Cloudflare и возвращает его публичный адрес."""
    binary = ensure_cloudflared()
    if not binary:
        return None, None

    log(f"      Запускаю туннель на порт {port}...")
    try:
        process = subprocess.Popen(
            [str(binary), "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"      ! Не удалось запустить cloudflared: {exc}")
        return None, None

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        line = process.stdout.readline() if process.stdout else ""
        if not line:
            if process.poll() is not None:
                break
            continue
        match = URL_RE.search(line)
        if match:
            return match.group(0), process

    if process.poll() is None:
        process.terminate()
    return None, None


def main() -> None:
    parser = argparse.ArgumentParser(description="NSO Diary API")
    parser.add_argument("--demo", action="store_true", help="без Supabase и Telegram, ученик demo/demo")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-tunnel", action="store_true", help="не поднимать туннель")
    args = parser.parse_args()

    if args.demo:
        os.environ["DEMO_MODE"] = "1"
        os.environ["SUPABASE_URL"] = ""
        os.environ["SUPABASE_SERVICE_KEY"] = ""
        os.environ["BOT_TOKEN"] = ""

    # Порт: Pterodactyl передаёт SERVER_PORT.
    port = args.port or int(os.environ.get("SERVER_PORT") or os.environ.get("PORT") or "8000")

    # Настройки читаем ДО запуска uvicorn, чтобы успеть подставить PUBLIC_BASE.
    from app.config import diagnostics, get_settings

    settings = get_settings()

    tunnel_process = None
    public_base = settings.public_base
    # Туннель ВЫКЛЮЧЕН по умолчанию: на хостингах с Pterodactyl за него
    # блокируют аккаунт. Включается только явно: AUTO_TUNNEL=1.
    need_tunnel = (
        not args.no_tunnel
        and not args.demo
        and not public_base.startswith("https://")
        and os.environ.get("AUTO_TUNNEL", "0") == "1"
    )

    log("=" * 56)
    log(" NSO Diary API")
    log("=" * 56)

    if need_tunnel:
        log("[туннель] PUBLIC_BASE не задан — поднимаю Cloudflare Tunnel")
        url, tunnel_process = start_tunnel(port)
        if url:
            os.environ["PUBLIC_BASE"] = url
            public_base = url
            log(f"[туннель] Адрес: {url}")
        else:
            log("[туннель] ! Адрес не получен — Telegram-мини-приложение не заработает")
            log("[туннель] ! Задайте PUBLIC_BASE вручную в .env")

    # Перечитываем настройки уже с адресом туннеля.
    settings = get_settings()

    log(f" Порт:      {port}")
    log(f" База:      {'Supabase' if settings.db_enabled else 'в памяти'}")
    log(f" Бот:       {'включён' if settings.bot_enabled else 'выключен'}")
    log(f" Публично:  {settings.public_base}")
    log("-" * 56)
    for line in diagnostics():
        log(f" {line}")
    log("=" * 56)

    if not settings.db_enabled:
        log("")
        log("! Supabase не подключён: данные будут в памяти и исчезнут при перезапуске.")
        log("  Проверьте, что .env или .env.nomadhost лежит рядом с main.py.")

    import uvicorn

    try:
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=port,
            proxy_headers=True,
            forwarded_allow_ips="*",
            log_level="info",
        )
    finally:
        if tunnel_process and tunnel_process.poll() is None:
            tunnel_process.terminate()


if __name__ == "__main__":
    main()
