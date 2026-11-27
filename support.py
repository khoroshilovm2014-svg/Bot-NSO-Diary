"""Поддержка: обращения, сообщения, вложения и админ-доступ.

Выделено отдельным модулем, чтобы не раздувать api.py.

Модель данных (см. sql/06_support.sql):

    support_tickets   — само обращение
    support_messages  — переписка внутри обращения
    support_files     — вложения (хранятся как base64 в тексте)
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

# Префикс /api уже задан в api.py — здесь только пути.
router = APIRouter(tags=["support"])

# Ограничения
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024   # 5 МБ
MAX_ATTACHMENTS = 3
MAX_MESSAGE = 4000

CATEGORIES = {"sync", "marks", "login", "bug", "idea", "other"}
NOTICE_KINDS = {"maintenance", "warning", "info"}
STATUSES = {"open", "progress", "answered", "closed"}


# --------------------------------------------------------------------------
# Схемы
# --------------------------------------------------------------------------


class TicketIn(BaseModel):
    category: str = Field(default="other", max_length=24)
    subject: str = Field(min_length=3, max_length=120)
    message: str = Field(min_length=10, max_length=MAX_MESSAGE)


class MessageIn(BaseModel):
    text: str = Field(min_length=2, max_length=MAX_MESSAGE)


class AttachmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    type: str = Field(default="image/png", max_length=80)
    data: str = Field(min_length=8)


class StatusIn(BaseModel):
    status: str = Field(min_length=3, max_length=16)


class AdminLoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserFlagIn(BaseModel):
    blocked: bool | None = None
    sync_disabled: bool | None = None
    note: str | None = Field(default=None, max_length=200)


class NoticeIn(BaseModel):
    kind: str = Field(default="info", max_length=16)
    title: str = Field(min_length=2, max_length=120)
    text: str = Field(default="", max_length=600)
    active: bool = True


# --------------------------------------------------------------------------
# Утилиты
# --------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _settings():
    """Настройки берём из api в момент вызова — так избегаем циклического импорта."""
    import api

    return api._settings


def _store():
    import api

    return api.get_store()


def _student_dependency():
    """Студент по обычной сессии."""
    import api

    return api.current_student


def _issue(payload: dict, ttl: int) -> str:
    import api

    return api.issue_token(payload, api._settings.jwt_secret, ttl)


def _verify(token: str) -> dict | None:
    import api

    return api.verify_token(token, api._settings.jwt_secret)


def student_of(authorization: str | None) -> dict:
    """Достаёт ученика из заголовка Authorization."""
    import api

    return api.current_student(authorization)


# --------------------------------------------------------------------------
# Хранилище обращений
#
# Работает и с Supabase, и с памятью — через методы, которые есть в db.py.
# --------------------------------------------------------------------------


#
# В демо-режиме (MemoryStore) база не используется — обращения живут
# в памяти процесса. Логика одна и та же, меняется только слой хранения.


_MEMORY: dict[str, list[dict]] = {
    "support_tickets": [],
    "support_messages": [],
    "support_files": [],
    "student_flags": [],
    "notices": [],
}


def _has_supabase() -> bool:
    return hasattr(_store(), "_request")


def _matches(row: dict, params: dict) -> bool:
    """Проверяет строку по фильтрам вида field=eq.value."""
    for key, raw in params.items():
        if key in {"select", "order", "limit", "offset"}:
            continue

        value = str(raw)
        if not value.startswith("eq."):
            continue

        expected = value[3:]
        actual = row.get(key)

        # Приводим к строке в нижнем регистре: True -> "true",
        # 1 -> "1". Так один и тот же фильтр работает и для boolean,
        # и для чисел, и для текста.
        if isinstance(actual, bool):
            actual = "true" if actual else "false"
        else:
            actual = str(actual).lower() if actual is not None else ""

        if actual != expected.lower():
            return False

    return True


def _memory_rows(table: str, params: dict) -> list[dict]:
    rows = [dict(r) for r in _MEMORY.get(table, []) if _matches(r, params)]

    order = params.get("order") or ""
    if order:
        field, _, direction = order.partition(".")
        rows.sort(
            key=lambda r: str(r.get(field) or ""),
            reverse=(direction == "desc"),
        )

    limit = params.get("limit")
    if limit:
        try:
            rows = rows[: int(limit)]
        except ValueError:
            pass

    return rows


def _rows(table: str, **params) -> list[dict]:
    if not _has_supabase():
        return _memory_rows(table, params)

    try:
        return _store()._request("GET", table, params=params) or []
    except RuntimeError:
        return _memory_rows(table, params)


def _insert(table: str, body: dict | list) -> None:
    items = body if isinstance(body, list) else [body]

    if not _has_supabase():
        _MEMORY.setdefault(table, []).extend(dict(item) for item in items)
        return

    try:
        _store()._request("POST", table, body=body, prefer="return=minimal")
    except RuntimeError as error:
        # Чаще всего это отсутствующая таблица: в Supabase не выполнен
        # sql/06_support.sql или sql/07_admin.sql.
        raise HTTPException(
            status_code=503,
            detail=f"Таблица {table} недоступна — выполните SQL-миграции",
        ) from error


def _patch(table: str, row_id: str, body: dict) -> None:
    if not _has_supabase():
        for row in _MEMORY.get(table, []):
            if row.get("id") == row_id:
                row.update(body)
        return

    try:
        _store()._request(
            "PATCH",
            table,
            params={"id": f"eq.{row_id}"},
            body=body,
            prefer="return=minimal",
        )
    except RuntimeError:
        pass


def _ticket_preview(text: str, size: int = 90) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= size:
        return flat
    return flat[: size - 1].rstrip() + "…"


def _decode_attachment(data: str) -> bytes:
    """Проверяет и декодирует base64 из вложения."""
    payload = data.split(",", 1)[-1]
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=400, detail="Файл повреждён") from error

    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="Файл больше 5 МБ")

    return raw


# --------------------------------------------------------------------------
# Ученик: создание и просмотр своих обращений
# --------------------------------------------------------------------------


@router.post("/support/ticket")
def create_ticket(
    body: TicketIn,
    authorization: str | None = Header(default=None),
) -> dict:
    """Создаёт обращение в поддержку."""
    student = student_of(authorization)

    category = body.category if body.category in CATEGORIES else "other"
    now = _now()
    ticket_id = str(uuid.uuid4())

    _insert(
        "support_tickets",
        {
            "id": ticket_id,
            "student_id": student["id"],
            "category": category,
            "subject": body.subject.strip(),
            "status": "open",
            "preview": _ticket_preview(body.message),
            "message_count": 1,
            "created_at": now,
            "updated_at": now,
        },
    )

    _insert(
        "support_messages",
        {
            "id": str(uuid.uuid4()),
            "ticket_id": ticket_id,
            "author": "student",
            "text": body.message.strip(),
            "created_at": now,
        },
    )

    return {"id": ticket_id, "status": "open"}


@router.get("/support/tickets")
def my_tickets(authorization: str | None = Header(default=None)) -> dict:
    """Список обращений текущего ученика."""
    student = student_of(authorization)

    rows = _rows(
        "support_tickets",
        select="id,subject,status,category,preview,message_count,created_at,updated_at",
        student_id=f"eq.{student['id']}",
        order="updated_at.desc",
        limit="60",
    )

    # В memory-режиме проекция полей не применяется — чистим вручную.
    fields = (
        "id", "subject", "status", "category", "preview",
        "message_count", "created_at", "updated_at",
    )
    items = [{key: row.get(key) for key in fields} for row in rows]

    return {"items": items}


@router.get("/support/ticket/{ticket_id}")
def my_ticket(
    ticket_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    """Одно обращение с перепиской."""
    student = student_of(authorization)

    rows = _rows(
        "support_tickets",
        select="*",
        id=f"eq.{ticket_id}",
        student_id=f"eq.{student['id']}",
        limit="1",
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Обращение не найдено")

    ticket = rows[0]

    messages = _rows(
        "support_messages",
        select="id,author,text,created_at",
        ticket_id=f"eq.{ticket_id}",
        order="created_at.asc",
    )

    files = _rows(
        "support_files",
        select="id,message_id,name,url",
        ticket_id=f"eq.{ticket_id}",
    )

    by_message: dict[str, list[dict]] = {}
    for file in files:
        by_message.setdefault(file.get("message_id") or "", []).append(
            {"name": file.get("name"), "url": file.get("url")}
        )

    for message in messages:
        message["attachments"] = by_message.get(message["id"], [])

    ticket["messages"] = messages
    ticket.pop("student_id", None)

    return ticket


@router.post("/support/ticket/{ticket_id}/message")
def add_message(
    ticket_id: str,
    body: MessageIn,
    authorization: str | None = Header(default=None),
) -> dict:
    """Ученик дописывает сообщение в своё обращение."""
    student = student_of(authorization)

    rows = _rows(
        "support_tickets",
        select="id,status",
        id=f"eq.{ticket_id}",
        student_id=f"eq.{student['id']}",
        limit="1",
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Обращение не найдено")

    if rows[0].get("status") == "closed":
        raise HTTPException(status_code=409, detail="Обращение закрыто")

    now = _now()

    _insert(
        "support_messages",
        {
            "id": str(uuid.uuid4()),
            "ticket_id": ticket_id,
            "author": "student",
            "text": body.text.strip(),
            "created_at": now,
        },
    )

    current = int(rows[0].get("message_count") or 0)
    _patch(
        "support_tickets",
        ticket_id,
        {
            "message_count": current + 1,
            "status": "open",
            "updated_at": now,
        },
    )

    return {"ok": True}


@router.post("/support/ticket/{ticket_id}/attach")
def attach_file(
    ticket_id: str,
    body: AttachmentIn,
    authorization: str | None = Header(default=None),
) -> dict:
    """Прикладывает скриншот к последнему сообщению ученика."""
    student = student_of(authorization)

    rows = _rows(
        "support_tickets",
        select="id",
        id=f"eq.{ticket_id}",
        student_id=f"eq.{student['id']}",
        limit="1",
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Обращение не найдено")

    existing = _rows(
        "support_files",
        select="id",
        ticket_id=f"eq.{ticket_id}",
    )

    if len(existing) >= MAX_ATTACHMENTS:
        raise HTTPException(status_code=409, detail="Слишком много вложений")

    raw = _decode_attachment(body.data)

    if not body.type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Можно прикладывать только картинки")

    messages = _rows(
        "support_messages",
        select="id",
        ticket_id=f"eq.{ticket_id}",
        author="eq.student",
        order="created_at.desc",
        limit="1",
    )

    message_id = messages[0]["id"] if messages else ""

    # Картинку храним прямо в строке: отдельное хранилище файлов
    # усложнило бы развёртывание, а размер ограничен 5 МБ.
    encoded = base64.b64encode(raw).decode()
    url = f"data:{body.type};base64,{encoded}"

    _insert(
        "support_files",
        {
            "id": str(uuid.uuid4()),
            "ticket_id": ticket_id,
            "message_id": message_id,
            "name": body.name.strip()[:180],
            "url": url,
            "created_at": _now(),
        },
    )

    return {"ok": True, "size": len(raw)}


# --------------------------------------------------------------------------
# Админ
# --------------------------------------------------------------------------


def admin_guard(authorization: str | None = Header(default=None)) -> dict:
    """Проверяет админский токен."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Нужна авторизация")

    payload = _verify(authorization.split(" ", 1)[1].strip())

    if not payload or payload.get("scope") != "admin":
        raise HTTPException(status_code=401, detail="Нет доступа")

    return payload


@router.post("/admin/login")
def admin_login(body: AdminLoginIn) -> dict:
    """Вход в админ-панель по логину и паролю из настроек."""
    import api

    settings = api._settings

    expected_login = settings.admin_login
    expected_password = settings.admin_password

    if not expected_login or not expected_password:
        raise HTTPException(status_code=503, detail="Админ-доступ не настроен")

    login_ok = api.verify_password(body.login.strip(), expected_login)
    password_ok = api.verify_password(body.password.strip(), expected_password)

    # Логин и пароль в настройках могут быть обычным текстом — тогда
    # сравниваем напрямую, а не как хеш.
    if not login_ok:
        login_ok = body.login.strip() == expected_login
    if not password_ok:
        password_ok = body.password.strip() == expected_password

    if not (login_ok and password_ok):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    ttl = 60 * 60 * 12   # 12 часов
    token = _issue({"sub": "admin", "scope": "admin", "role": "admin"}, ttl)

    return {"token": token, "expires_in": ttl}


@router.get("/admin/tickets")
def admin_tickets(_: dict = Depends(admin_guard)) -> dict:
    """Все обращения для админ-панели."""
    rows = _rows(
        "support_tickets",
        select="id,student_id,subject,status,category,preview,"
               "message_count,created_at,updated_at",
        order="updated_at.desc",
        limit="200",
    )

    # Подставляем имена учеников
    store = _store()
    names: dict[str, tuple[str, str]] = {}

    for row in rows:
        student_id = row.get("student_id") or ""
        if student_id in names:
            continue

        try:
            student = store.get_student(student_id)
        except Exception:  # noqa: BLE001
            student = None

        if student:
            names[student_id] = (
                student.get("full_name") or "Ученик",
                student.get("class_name") or "",
            )
        else:
            names[student_id] = ("Ученик", "")

    for row in rows:
        name, class_name = names.get(row.get("student_id") or "", ("Ученик", ""))
        row["student_name"] = name
        row["class_name"] = class_name
        row.pop("student_id", None)

    return {"items": rows}


@router.get("/admin/ticket/{ticket_id}")
def admin_ticket(ticket_id: str, _: dict = Depends(admin_guard)) -> dict:
    """Обращение с перепиской и вложениями."""
    rows = _rows("support_tickets", select="*", id=f"eq.{ticket_id}", limit="1")

    if not rows:
        raise HTTPException(status_code=404, detail="Обращение не найдено")

    ticket = rows[0]

    store = _store()
    student_id = ticket.get("student_id") or ""

    try:
        student = store.get_student(student_id)
    except Exception:  # noqa: BLE001
        student = None

    ticket["student_name"] = (student or {}).get("full_name") or "Ученик"
    ticket["class_name"] = (student or {}).get("class_name") or ""

    messages = _rows(
        "support_messages",
        select="id,author,text,created_at",
        ticket_id=f"eq.{ticket_id}",
        order="created_at.asc",
    )

    files = _rows(
        "support_files",
        select="id,message_id,name,url",
        ticket_id=f"eq.{ticket_id}",
    )

    by_message: dict[str, list[dict]] = {}
    for file in files:
        by_message.setdefault(file.get("message_id") or "", []).append(
            {"name": file.get("name"), "url": file.get("url")}
        )

    for message in messages:
        message["attachments"] = by_message.get(message["id"], [])

    ticket["messages"] = messages
    ticket.pop("student_id", None)

    return ticket


@router.post("/admin/ticket/{ticket_id}/status")
def admin_status(
    ticket_id: str,
    body: StatusIn,
    _: dict = Depends(admin_guard),
) -> dict:
    """Меняет статус обращения."""
    if body.status not in STATUSES:
        raise HTTPException(status_code=400, detail="Неизвестный статус")

    _patch("support_tickets", ticket_id, {"status": body.status, "updated_at": _now()})
    return {"ok": True}


@router.post("/admin/ticket/{ticket_id}/reply")
def admin_reply(
    ticket_id: str,
    body: MessageIn,
    _: dict = Depends(admin_guard),
) -> dict:
    """Ответ поддержки ученику."""
    rows = _rows(
        "support_tickets",
        select="id,message_count",
        id=f"eq.{ticket_id}",
        limit="1",
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Обращение не найдено")

    now = _now()

    _insert(
        "support_messages",
        {
            "id": str(uuid.uuid4()),
            "ticket_id": ticket_id,
            "author": "staff",
            "text": body.text.strip(),
            "created_at": now,
        },
    )

    current = int(rows[0].get("message_count") or 0)
    _patch(
        "support_tickets",
        ticket_id,
        {
            "message_count": current + 1,
            "status": "answered",
            "updated_at": now,
        },
    )

    return {"ok": True}


# --------------------------------------------------------------------------
# Пользователи: список, блокировка, ограничение синхронизации
# --------------------------------------------------------------------------


def _all_students() -> list[dict]:
    """Все ученики для админки. Пароли наружу не отдаём."""
    fields = ("id", "login", "full_name", "class_name", "created_at")

    if not _has_supabase():
        return [
            {key: row.get(key) for key in fields}
            for row in _store().students.values()
        ]

    return _rows(
        "students",
        select=",".join(fields),
        order="created_at.desc",
        limit="300",
    )


def flags_of(student_id: str) -> dict:
    """Флаги ученика. Пустой словарь, если их ещё нет."""
    rows = _rows(
        "student_flags",
        select="id,student_id,blocked,sync_disabled,note,updated_at",
        student_id=f"eq.{student_id}",
        limit="1",
    )
    return rows[0] if rows else {}


def set_flags(student_id: str, values: dict) -> dict:
    """Создаёт или обновляет флаги ученика."""
    now = _now()
    existing = flags_of(student_id)

    if existing:
        _patch("student_flags", existing["id"], {**values, "updated_at": now})
    else:
        _insert(
            "student_flags",
            {
                "id": str(uuid.uuid4()),
                "student_id": student_id,
                "blocked": False,
                "sync_disabled": False,
                "note": "",
                **values,
                "created_at": now,
                "updated_at": now,
            },
        )

    return flags_of(student_id)


@router.get("/admin/users")
def admin_users(_: dict = Depends(admin_guard)) -> dict:
    """Список учеников с их ограничениями."""
    students = _all_students()

    all_flags = _rows("student_flags", select="*")

    if not _has_supabase():
        by_student = {row.get("student_id"): row for row in all_flags}
    else:
        by_student = {row.get("student_id"): row for row in all_flags}

    items = []

    for student in students:
        student_id = student.get("id") or ""
        flags = by_student.get(student_id) or {}

        items.append(
            {
                "id": student_id,
                "login": student.get("login") or "",
                "name": student.get("full_name") or "Ученик",
                "class_name": student.get("class_name") or "",
                "created_at": student.get("created_at"),
                "blocked": bool(flags.get("blocked")),
                "sync_disabled": bool(flags.get("sync_disabled")),
                "note": flags.get("note") or "",
                "updated_at": flags.get("updated_at"),
            }
        )

    blocked = sum(1 for item in items if item["blocked"])
    limited = sum(1 for item in items if item["sync_disabled"])

    return {
        "items": items,
        "total": len(items),
        "blocked": blocked,
        "limited": limited,
    }


@router.post("/admin/user/{student_id}/flags")
def admin_set_flags(
    student_id: str,
    body: UserFlagIn,
    _: dict = Depends(admin_guard),
) -> dict:
    """Блокировка, ограничение синхронизации и заметка."""
    values: dict = {}

    if body.blocked is not None:
        values["blocked"] = body.blocked
    if body.sync_disabled is not None:
        values["sync_disabled"] = body.sync_disabled
    if body.note is not None:
        values["note"] = body.note.strip()[:200]

    if not values:
        raise HTTPException(status_code=400, detail="Нечего менять")

    flags = set_flags(student_id, values)
    return {"ok": True, "flags": flags}


# --------------------------------------------------------------------------
# Уведомления о работах
# --------------------------------------------------------------------------


@router.get("/admin/notices")
def admin_notices(_: dict = Depends(admin_guard)) -> dict:
    """Все уведомления для админки."""
    rows = _rows("notices", select="*", order="created_at.desc", limit="40")
    return {"items": rows}


@router.post("/admin/notice")
def admin_create_notice(
    body: NoticeIn,
    _: dict = Depends(admin_guard),
) -> dict:
    """Создаёт уведомление: работы, предупреждение или информация."""
    kind = body.kind if body.kind in NOTICE_KINDS else "info"
    now = _now()

    notice = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "title": body.title.strip(),
        "text": body.text.strip(),
        "active": bool(body.active),
        "created_at": now,
    }

    _insert("notices", notice)
    return {"ok": True, "notice": notice}


@router.post("/admin/notice/{notice_id}/toggle")
def admin_toggle_notice(
    notice_id: str,
    _: dict = Depends(admin_guard),
) -> dict:
    """Включает или выключает уведомление."""
    rows = _rows("notices", select="id,active", id=f"eq.{notice_id}", limit="1")

    if not rows:
        raise HTTPException(status_code=404, detail="Уведомление не найдено")

    new_state = not bool(rows[0].get("active"))
    _patch("notices", notice_id, {"active": new_state, "updated_at": _now()})

    return {"ok": True, "active": new_state}


@router.delete("/admin/notice/{notice_id}")
def admin_delete_notice(
    notice_id: str,
    _: dict = Depends(admin_guard),
) -> dict:
    """Удаляет уведомление."""
    if not _has_supabase():
        _MEMORY["notices"] = [
            row for row in _MEMORY["notices"] if row.get("id") != notice_id
        ]
        return {"ok": True}

    try:
        _store()._request(
            "DELETE",
            "notices",
            params={"id": f"eq.{notice_id}"},
            prefer="return=minimal",
        )
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="Не удалось удалить") from error

    return {"ok": True}


@router.get("/notice")
def public_notice() -> dict:
    """Активное уведомление для учеников — показывается баннером на сайте."""
    rows = _rows(
        "notices",
        select="id,kind,title,text,created_at",
        active="eq.true",
        order="created_at.desc",
        limit="1",
    )

    if not rows:
        return {"notice": None}

    notice = rows[0]
    return {"notice": notice}
