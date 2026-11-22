"""Криптография и проверки: подпись initData Telegram, JWT, коды, пароли.

Только стандартная библиотека — модуль можно тестировать без установки
FastAPI/aiogram.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl

# --------------------------------------------------------------------------
# initData из Telegram Mini App
# --------------------------------------------------------------------------


def _data_check_string(fields: dict[str, str], exclude: set[str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(fields.items()) if key not in exclude)


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age: int = 86400,
) -> dict | None:
    """Проверяет подпись initData и возвращает распарсенные поля.

    Возвращает None, если подпись неверна или данные устарели.
    """
    if not init_data or not bot_token:
        return None

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None

    fields = dict(pairs)
    received_hash = fields.get("hash")
    if not received_hash:
        return None

    # Секретный ключ: HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

    # Поле signature появилось в 2024 году; в части клиентов оно не участвует
    # в data-check-string, поэтому допускаем оба варианта.
    valid = False
    for exclude in ({"hash", "signature"}, {"hash"}):
        check_string = _data_check_string(fields, exclude)
        calculated = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(calculated, received_hash):
            valid = True
            break

    if not valid:
        return None

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        return None

    if max_age and time.time() - auth_date > max_age:
        return None

    user_raw = fields.get("user")
    if user_raw:
        try:
            fields["user"] = json.loads(user_raw)
        except json.JSONDecodeError:
            return None

    fields["auth_date"] = auth_date
    return fields


# --------------------------------------------------------------------------
# JWT (HS256) — минимальная реализация без зависимостей
# --------------------------------------------------------------------------


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_token(payload: dict, secret: str, ttl: int) -> str:
    now = int(time.time())
    body = {**payload, "iat": now, "exp": now + ttl}
    header_segment = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body_segment = _b64encode(json.dumps(body, separators=(",", ":")).encode())
    signing_input = f"{header_segment}.{body_segment}"
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64encode(signature)}"


def verify_token(token: str, secret: str) -> dict | None:
    if not token or token.count(".") != 2:
        return None

    header_segment, body_segment, signature_segment = token.split(".")

    expected = hmac.new(
        secret.encode(),
        f"{header_segment}.{body_segment}".encode(),
        hashlib.sha256,
    ).digest()

    try:
        received = _b64decode(signature_segment)
    except ValueError:
        return None

    if not hmac.compare_digest(expected, received):
        return None

    try:
        payload = json.loads(_b64decode(body_segment))
    except (ValueError, json.JSONDecodeError):
        return None

    if int(payload.get("exp", 0)) < int(time.time()):
        return None

    return payload


# --------------------------------------------------------------------------
# Коды подтверждения
# --------------------------------------------------------------------------


def generate_code(length: int = 6) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def codes_match(expected: str, provided: str) -> bool:
    return hmac.compare_digest(str(expected).strip(), str(provided).strip())


# --------------------------------------------------------------------------
# Пароли (PBKDF2-HMAC-SHA256)
# --------------------------------------------------------------------------

_ITERATIONS = 200_000


def hash_password(password: str, iterations: int = _ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    try:
        algorithm, iterations_raw, salt, digest_hex = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            (password or "").encode(),
            bytes.fromhex(salt),
            int(iterations_raw),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False
