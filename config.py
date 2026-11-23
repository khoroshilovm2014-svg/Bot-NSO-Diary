"""Конфигурация сервиса.

Все значения читаются из переменных окружения. Если их нет — ищется файл .env
в нескольких местах (рядом с кодом, в рабочей папке контейнера).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Где искать .env. Pterodactyl запускает код из /home/container,
# локально это папка bot/ — перебираем оба варианта и рабочий каталог.
ENV_CANDIDATES = (
    BASE_DIR / ".env",
    Path.cwd() / ".env",
    Path("/home/container/.env"),
    # запасные имена — на случай, если панель не даёт создать файл с точкой
    BASE_DIR / ".env.nomadhost",
    Path.cwd() / ".env.nomadhost",
    Path("/home/container/.env.nomadhost"),
)

# Куда записали найденный файл — для диагностики при старте.
LOADED_ENV_FILE: Path | None = None


def load_dotenv(path: Path | str | None = None) -> Path | None:
    """Простой парсер .env: KEY=VALUE, без зависимостей.

    Возвращает путь к прочитанному файлу или None.
    Уже заданные переменные окружения не перезаписываются.
    """
    global LOADED_ENV_FILE

    candidates = (Path(path),) if path else ENV_CANDIDATES

    for file in candidates:
        try:
            if not file.is_file():
                continue
            content = file.read_text(encoding="utf-8-sig")
        except OSError:
            continue

        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

        LOADED_ENV_FILE = file
        return file

    return None


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    return _env(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_username: str
    public_base: str
    site_origin: str
    webapp_url: str
    supabase_url: str
    supabase_key: str
    jwt_secret: str
    webhook_secret: str
    auth_ttl: int
    code_ttl: int
    token_ttl: int
    max_attempts: int
    demo_mode: bool
    cors_origins: tuple[str, ...]
    cors_origin_regex: str

    @property
    def bot_enabled(self) -> bool:
        return bool(self.bot_token)

    @property
    def db_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)

    @property
    def webhook_url(self) -> str:
        return f"{self.public_base}/tg/webhook/{self.webhook_secret}"


def get_settings() -> Settings:
    load_dotenv()

    origins = _env(
        "CORS_ORIGINS",
        "https://nso-diary.ru,https://www.nso-diary.ru,"
        "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8000,http://127.0.0.1:8000",
    )
    public_base = _env("PUBLIC_BASE", "http://127.0.0.1:8000").rstrip("/")

    # Регулярка разрешает ЛЮБОЙ поддомен vercel.app и локальные адреса.
    # Так вход работает и на основном домене, и на preview-деплоях Vercel,
    # без ручного перечисления адресов в CORS_ORIGINS.
    origin_regex = _env(
        "CORS_ORIGIN_REGEX",
        r"https://([a-zA-Z0-9-]+\.)*vercel\.app"
        r"|https://([a-zA-Z0-9-]+\.)*onrender\.com"
        r"|http://localhost(:\d+)?"
        r"|http://127\.0\.0\.1(:\d+)?",
    )

    return Settings(
        bot_token=_env("BOT_TOKEN"),
        bot_username=_env("BOT_USERNAME", "nso_diary_bot").lstrip("@"),
        public_base=public_base,
        site_origin=_env("SITE_ORIGIN", "https://nso-diary.ru").rstrip("/"),
        webapp_url=_env("WEBAPP_URL", f"{public_base}/tg-app/"),
        supabase_url=_env("SUPABASE_URL").rstrip("/"),
        supabase_key=_env("SUPABASE_SERVICE_KEY") or _env("SUPABASE_KEY"),
        jwt_secret=_env("JWT_SECRET", "dev-secret-change-me"),
        webhook_secret=_env("WEBHOOK_SECRET", "dev-webhook"),
        auth_ttl=_int("AUTH_REQUEST_TTL", 600),
        code_ttl=_int("CODE_TTL", 300),
        token_ttl=_int("TOKEN_TTL", 60 * 60 * 12),
        max_attempts=_int("MAX_CODE_ATTEMPTS", 6),
        demo_mode=_bool("DEMO_MODE", False),
        cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
        cors_origin_regex=origin_regex,
    )


def diagnostics() -> list[str]:
    """Короткий отчёт о загруженных настройках — печатается при старте."""
    settings = get_settings()

    if LOADED_ENV_FILE:
        env_line = f".env найден: {LOADED_ENV_FILE}"
    else:
        env_line = ".env НЕ найден (искали: " + ", ".join(str(p) for p in ENV_CANDIDATES) + ")"

    return [
        env_line,
        f"SUPABASE_URL: {'задан' if settings.supabase_url else 'ПУСТО'}",
        f"SUPABASE_SERVICE_KEY: {'задан' if settings.supabase_key else 'ПУСТО'}",
        f"BOT_TOKEN: {'задан' if settings.bot_token else 'ПУСТО'}",
        f"PUBLIC_BASE: {settings.public_base}",
        f"DEMO_MODE: {'да' if settings.demo_mode else 'нет'}",
    ]
