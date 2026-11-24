"""Вход через Госуслуги (ЕСИА).

Как это должно работать по-настоящему
=====================================

1. Сайт открывает страницу Госуслуг:

       https://esia.gosuslugi.ru/aas/oauth2/ac
           ?client_id=<ESIA_CLIENT_ID>
           &client_secret=<ESIA_CLIENT_SECRET>
           &redirect_uri=<ESIA_REDIRECT_URI>
           &scope=openid+id_doc+fullname
           &response_type=code
           &state=<случайная строка>
           &timestamp=<время>
           &access_type=offline

2. Человек вводит свой логин Госуслуг и подтверждает доступ.

3. Госуслуги возвращают на redirect_uri код:

       <redirect_uri>?code=<код>&state=<та же строка>

4. Сервер меняет код на токен:

       POST https://esia.gosuslugi.ru/aas/oauth2/te
       code=<код>&client_id=...&client_secret=...
       &redirect_uri=...&grant_type=authorization_code

5. По токену читаются данные человека:

       GET https://esia.gosuslugi.ru/rs/prns/<oid>?embed=elements(...)
       Authorization: Bearer <access_token>

6. По СНИЛС (или OID) находим ученика в таблице students (поле esia_id)
   и создаём запрос на вход — дальше как обычно, подтверждение в Telegram.


Почему сейчас работает имитация
==============================

Реальная интеграция требует регистрации информационной системы в СИА:
нужны ИП или юрлицо, усиленная квалифицированная ЭЦП и заявка,
которая рассматривается до 30 дней. Без выданных client_id/client_secret
Госуслуги просто не пустят в свой OAuth.

Поэтому по умолчанию включён режим имитации (ESIA_MOCK=1):
он повторяет ту же логику и ту же последовательность шагов, но
подставляет тестовый профиль. Как только получите настоящие ключи —
впишите их в ESIA_CLIENT_ID / ESIA_CLIENT_SECRET и поставьте ESIA_MOCK=0.
Код менять не придётся.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# --------------------------------------------------------------------------
# Ссылки ЕСИА
# --------------------------------------------------------------------------

AUTH_PATH = "/aas/oauth2/ac"
TOKEN_PATH = "/aas/oauth2/te"
PRNS_PATH = "/rs/prns"


class EsiaError(Exception):
    """Ошибка обмена с Госуслугами."""


# --------------------------------------------------------------------------
# Построение ссылки авторизации
# --------------------------------------------------------------------------


def make_state(secret: str, ttl: int = 900) -> str:
    """Подписанный state: защищает от подмены ответа.

    Внутри — метка времени и случайная часть, снаружи — HMAC-подпись,
    чтобы callback нельзя было подделать.
    """
    raw = "%d.%s" % (int(time.time()), secrets.token_hex(8))
    signature = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return raw + "." + signature


def check_state(state: str, secret: str, ttl: int = 900) -> bool:
    """Проверяет подпись и срок жизни state."""
    if not state or state.count(".") != 2:
        return False

    raw, signature = state.rsplit(".", 1)
    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]

    if not hmac.compare_digest(expected, signature):
        return False

    try:
        created = int(raw.split(".", 1)[0])
    except (ValueError, IndexError):
        return False

    return time.time() - created <= ttl


def build_auth_url(
    portal: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    scope: str,
    state: str,
) -> str:
    """Ссылка, на которую отправляем человека входить через Госуслуги."""
    params = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "response_type": "code",
        "state": state,
        "timestamp": str(int(time.time())),
        "access_type": "offline",
    }
    return portal.rstrip("/") + AUTH_PATH + "?" + urlencode(params)


# --------------------------------------------------------------------------
# Обмен кода на токен и чтение профиля
# --------------------------------------------------------------------------


def exchange_code(
    portal: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    timeout: int = 20,
) -> dict:
    """Меняет код авторизации на access_token."""
    body = urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode()

    request = Request(
        portal.rstrip("/") + TOKEN_PATH,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:  # noqa: BLE001
        raise EsiaError("Госуслуги не приняли код авторизации: %s" % exc) from exc

    if "access_token" not in payload:
        raise EsiaError("В ответе Госуслуг нет access_token")

    return payload


def fetch_profile(portal: str, access_token: str, timeout: int = 20) -> dict:
    """Читает профиль человека по токену."""
    request = Request(
        portal.rstrip("/") + PRNS_PATH + "?embed=elements(fullname)",
        headers={"Authorization": "Bearer " + access_token},
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except Exception as exc:  # noqa: BLE001
        raise EsiaError("Не удалось прочитать профиль Госуслуг: %s" % exc) from exc


def profile_to_identity(profile: dict) -> dict:
    """Приводит ответ ЕСИА к общему виду.

    Возвращает словарь с ключами: esia_id, full_name, snils.
    esia_id — это OID (стабильный идентификатор человека в ЕСИА),
    по нему ученик ищется в таблице students.
    """
    oid = str(profile.get("oid") or profile.get("sub") or "").strip()

    full_name = ""
    name_block = profile.get("fullname")
    if isinstance(name_block, dict):
        parts = [
            name_block.get("lastName"),
            name_block.get("firstName"),
            name_block.get("middleName"),
        ]
        full_name = " ".join(part for part in parts if part).strip()
    elif isinstance(name_block, str):
        full_name = name_block.strip()

    snils = str(profile.get("snils") or "").strip()

    return {"esia_id": oid, "full_name": full_name, "snils": snils}


# --------------------------------------------------------------------------
# Имитация (для разработки и демонстрации)
# --------------------------------------------------------------------------


DEMO_PROFILE = {
    "oid": "esia-demo-0001",
    "fullname": {
        "lastName": "Ученик",
        "firstName": "Демо",
        "middleName": "Михайлович",
    },
    "snils": "123-456-789 00",
}


def mock_profile(login: str = "demo") -> dict:
    """Тестовый профиль: OID собирается из логина, чтобы ученики не слипались."""
    profile = dict(DEMO_PROFILE)
    profile["oid"] = "esia-%s" % login.strip().lower()
    return profile
