"""Проверка подписки на Telegram-канал.

Перед выдачей кода для входа бот проверяет, что человек подписан
на канал проекта. Это делается через Bot API: getChatMember.

Важно: бот должен быть администратором канала, иначе Telegram
не даст посмотреть участников. Если проверить не удалось
(бот не админ, сеть недоступна), доступ не блокируется —
иначе из-за сбоя на стороне Telegram никто не смог бы войти.
"""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Статусы Telegram, при которых человек считается подписанным.
SUBSCRIBED = {"creator", "administrator", "member"}

# Статусы, при которых точно не подписан.
NOT_SUBSCRIBED = {"left", "kicked"}

# Кэш: не дёргаем Bot API на каждый запрос.
_CACHE: dict[int, tuple[float, bool | None]] = {}
_CACHE_TTL = 60.0


def _api(token: str, method: str, payload: dict, timeout: float = 8.0) -> dict | None:
    """Вызов метода Bot API. None — если запрос не удался."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")

    request = Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")

    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None


def check_subscription(
    telegram_id: int | None,
    token: str,
    channel: str,
    use_cache: bool = True,
) -> bool | None:
    """Подписан ли человек на канал.

    Возвращает:
      True  — подписан;
      False — не подписан;
      None  — проверить не удалось (бот не админ, канал не найден, сеть).
    """
    if not telegram_id or not token or not channel:
        return None

    now = time.time()

    if use_cache:
        cached = _CACHE.get(telegram_id)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]

    result = _api(
        token,
        "getChatMember",
        {"chat_id": channel, "user_id": int(telegram_id)},
    )

    if not result or not result.get("ok"):
        # Бот не администратор канала или канал указан неверно.
        _CACHE[telegram_id] = (now, None)
        return None

    status = ((result.get("result") or {}).get("status") or "").lower()

    if status in SUBSCRIBED:
        answer: bool | None = True
    elif status in NOT_SUBSCRIBED:
        answer = False
    else:
        # Неизвестный статус — считаем, что проверить не смогли.
        answer = None

    _CACHE[telegram_id] = (now, answer)
    return answer


def forget(telegram_id: int | None) -> None:
    """Сбрасывает кэш для человека — после нажатия «Я подписался»."""
    if telegram_id:
        _CACHE.pop(int(telegram_id), None)


def channel_link(channel: str, fallback: str = "") -> str:
    """Ссылка на канал из @username."""
    name = (channel or "").strip().lstrip("@")

    if name.startswith("http"):
        return name

    if not name:
        return fallback

    return f"https://t.me/{name}"
