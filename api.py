"""HTTP API: вход, подтверждение в Telegram, выдача кода и сессии.

Сценарий:
1. POST /api/auth/login      — логин + пароль → запрос на вход + ссылка на бота
2. POST /api/auth/confirm    — вызов из мини-приложения → код для ввода на сайте
3. POST /api/auth/verify     — код → JWT-сессия
4. GET  /api/me, /api/marks  — данные с JWT
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from config import Settings, diagnostics, get_settings
from db import build_store, iso, is_expired, now, parse_iso
from security import (
    codes_match,
    generate_code,
    hash_password,
    issue_token,
    validate_init_data,
    verify_password,
    verify_token,
)

router = APIRouter(prefix="/api", tags=["auth"])

_settings: Settings = get_settings()
_store = build_store(_settings)


# --------------------------------------------------------------------------
# Схемы
# --------------------------------------------------------------------------


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class EsiaIn(BaseModel):
    code: str = Field(min_length=1, max_length=128)


class RequestOut(BaseModel):
    request_id: str
    deeplink: str
    expires_in: int
    name: str | None = None


class ConfirmIn(BaseModel):
    request_id: str = Field(min_length=8, max_length=64)
    init_data: str = ""


class ConfirmOut(BaseModel):
    code: str
    expires_in: int
    name: str | None = None


class StatusOut(BaseModel):
    status: str
    expires_in: int
    deeplink: str | None = None
    has_code: bool = False


class VerifyIn(BaseModel):
    request_id: str = Field(min_length=8, max_length=64)
    code: str = Field(min_length=4, max_length=8)


class TokenOut(BaseModel):
    token: str
    student: dict


# --------------------------------------------------------------------------
# Вспомогательные функции
# --------------------------------------------------------------------------


def get_store():
    return _store


def get_config() -> Settings:
    return _settings


def ensure_demo_student() -> None:
    """В демо-режиме создаёт ученика demo/demo, чтобы можно было всё проверить."""
    if not _settings.demo_mode:
        return
    if _store.find_student_by_login("demo"):
        return
    _store.add_student(
        login="demo",
        password_hash=hash_password("demo"),
        full_name="Демо Ученик",
        class_name="9 «А»",
    )


def deeplink_for(request_id: str) -> str:
    return f"https://t.me/{_settings.bot_username}?start=auth_{request_id}"


def public_student(row: dict | None) -> dict:
    row = row or {}
    return {
        "id": row.get("id"),
        "name": row.get("full_name") or "",
        "class_name": row.get("class_name") or "",
        "login": row.get("login") or "",
    }


def seconds_left(value: str | None) -> int:
    moment = parse_iso(value)
    if not moment:
        return 0
    return max(0, int((moment - now()).total_seconds()))


def load_request(request_id: str) -> dict:
    request = get_store().get_auth_request(request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Запрос на вход не найден")
    return request


def assert_alive(request: dict) -> None:
    if request.get("status") in {"confirmed", "expired"}:
        if request.get("status") == "confirmed":
            raise HTTPException(status_code=409, detail="Вход уже подтверждён")
        raise HTTPException(status_code=410, detail="Срок действия истёк")
    if is_expired(request.get("expires_at")):
        get_store().update_auth_request(request["id"], status="expired")
        raise HTTPException(status_code=410, detail="Срок действия истёк")


def current_student(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Нужна авторизация")
    payload = verify_token(authorization.split(" ", 1)[1].strip(), _settings.jwt_secret)
    if not payload:
        raise HTTPException(status_code=401, detail="Сессия истекла")
    student = get_store().get_student(payload.get("sub", ""))
    if not student:
        raise HTTPException(status_code=401, detail="Ученик не найден")
    return student


# --------------------------------------------------------------------------
# Шаг 1: первичный вход
# --------------------------------------------------------------------------


@router.post("/auth/login", response_model=RequestOut)
def login(body: LoginIn, store=Depends(get_store)) -> RequestOut:
    student = store.find_student_by_login(body.login.strip())
    if not student or not verify_password(body.password, student.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    request = store.create_auth_request(student["id"], "password", _settings.auth_ttl)
    return RequestOut(
        request_id=request["id"],
        deeplink=deeplink_for(request["id"]),
        expires_in=_settings.auth_ttl,
        name=student.get("full_name"),
    )


@router.post("/auth/gosuslugi", response_model=RequestOut)
def gosuslugi(body: EsiaIn, store=Depends(get_store)) -> RequestOut:
    """Вход через Госуслуги.

    TODO: заменить на реальный обмен кода с ЕСИА. Сейчас код ЕСИА ищется
    в поле students.esia_id — так проверяется вся цепочка до Telegram.
    """
    student = store.find_student_by_esia(body.code.strip())
    if not student:
        raise HTTPException(status_code=404, detail="Аккаунт Госуслуг не привязан к дневнику")

    request = store.create_auth_request(student["id"], "gosuslugi", _settings.auth_ttl)
    return RequestOut(
        request_id=request["id"],
        deeplink=deeplink_for(request["id"]),
        expires_in=_settings.auth_ttl,
        name=student.get("full_name"),
    )


# --------------------------------------------------------------------------
# Шаг 2: подтверждение в мини-приложении
# --------------------------------------------------------------------------


@router.post("/auth/confirm", response_model=ConfirmOut)
def confirm(body: ConfirmIn, store=Depends(get_store)) -> ConfirmOut:
    request = load_request(body.request_id)
    assert_alive(request)

    if int(request.get("attempts") or 0) >= _settings.max_attempts:
        raise HTTPException(status_code=429, detail="Слишком много запросов кода")

    telegram_id = None
    if body.init_data:
        data = validate_init_data(body.init_data, _settings.bot_token)
        if not data:
            raise HTTPException(status_code=401, detail="Не удалось проверить данные Telegram")
        telegram_id = ((data.get("user") or {}).get("id")) or None
    elif not _settings.demo_mode:
        raise HTTPException(status_code=401, detail="Откройте мини-приложение в Telegram")

    code = generate_code()
    student = store.get_student(request["student_id"])

    store.update_auth_request(
        request["id"],
        status="code_issued",
        code=code,
        telegram_id=telegram_id,
        code_expires_at=iso(now() + timedelta(seconds=_settings.code_ttl)),
        attempts=int(request.get("attempts") or 0) + 1,
    )

    return ConfirmOut(
        code=code,
        expires_in=_settings.code_ttl,
        name=(student or {}).get("full_name"),
    )


@router.get("/auth/status", response_model=StatusOut)
def status(request_id: str = Query(min_length=8, max_length=64), store=Depends(get_store)) -> StatusOut:
    request = store.get_auth_request(request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Запрос на вход не найден")

    if request.get("status") not in {"confirmed", "expired"} and is_expired(request.get("expires_at")):
        store.update_auth_request(request_id, status="expired")
        request["status"] = "expired"

    left = seconds_left(request.get("code_expires_at")) if request.get("status") == "code_issued" else 0

    return StatusOut(
        status=request.get("status") or "pending",
        expires_in=left if request.get("status") == "code_issued" else seconds_left(request.get("expires_at")),
        deeplink=deeplink_for(request_id),
        has_code=bool(request.get("code")),
    )


# --------------------------------------------------------------------------
# Шаг 3: проверка кода и сессия
# --------------------------------------------------------------------------


@router.post("/auth/verify", response_model=TokenOut)
def verify(body: VerifyIn, store=Depends(get_store)) -> TokenOut:
    request = load_request(body.request_id)
    assert_alive(request)

    if request.get("status") != "code_issued" or not request.get("code"):
        raise HTTPException(status_code=409, detail="Код ещё не выдан. Получите его в Telegram")

    if int(request.get("attempts") or 0) > _settings.max_attempts:
        raise HTTPException(status_code=429, detail="Слишком много попыток. Начните вход заново")

    if is_expired(request.get("code_expires_at")):
        raise HTTPException(status_code=410, detail="Код истёк. Получите новый в Telegram")

    if not codes_match(request["code"], body.code):
        store.update_auth_request(body.request_id, attempts=int(request.get("attempts") or 0) + 1)
        raise HTTPException(status_code=401, detail="Неверный код")

    student = store.get_student(request["student_id"])
    if not student:
        raise HTTPException(status_code=404, detail="Ученик не найден")

    store.update_auth_request(body.request_id, status="confirmed", confirmed_at=iso(now()))
    token = issue_token({"sub": student["id"], "login": student.get("login")}, _settings.jwt_secret, _settings.token_ttl)

    return TokenOut(token=token, student=public_student(student))


# --------------------------------------------------------------------------
# Данные
# --------------------------------------------------------------------------


@router.get("/me")
def me(student: dict = Depends(current_student)) -> dict:
    return public_student(student)


@router.get("/marks")
def marks(student: dict = Depends(current_student), store=Depends(get_store)) -> dict:
    rows = store.list_marks(student["id"])
    return {"items": rows}


@router.get("/schedule")
def schedule(student: dict = Depends(current_student), store=Depends(get_store)) -> dict:
    """Расписание уроков класса ученика."""
    class_name = student.get("class_name") or ""
    rows = store.list_schedule(class_name) if class_name else []
    return {"items": rows, "class_name": class_name}


@router.get("/homework")
def homework(student: dict = Depends(current_student), store=Depends(get_store)) -> dict:
    """Домашние задания класса ученика."""
    class_name = student.get("class_name") or ""
    rows = store.list_homework(class_name) if class_name else []
    return {"items": rows, "class_name": class_name}


@router.get("/week")
def week(
    monday: str = Query(default="", max_length=10, description="Дата понедельника YYYY-MM-DD"),
    student: dict = Depends(current_student),
    store=Depends(get_store),
) -> dict:
    """Вся учебная неделя (пн–пт): уроки, задания и оценки по каждому дню.

    Один запрос вместо пяти — экран «Дневник» открывается быстрее,
    а точки «есть ДЗ» можно показать сразу на всех днях.
    """
    from datetime import date as date_cls
    from datetime import timedelta

    class_name = student.get("class_name") or ""

    # Понедельник: из параметра, иначе — начало текущей недели.
    if monday:
        try:
            year, month, day_num = (int(part) for part in monday.split("-"))
            start = date_cls(year, month, day_num)
        except (ValueError, TypeError):
            start = None
    else:
        start = None

    if start is None:
        today = date_cls.today()
        start = today - timedelta(days=today.weekday())

    lessons = store.list_schedule(class_name) if class_name else []
    tasks = store.list_homework(class_name) if class_name else []
    all_marks = store.list_marks(student["id"])

    # Оценки по датам: «12.11.2026» -> [{'subject':..., 'mark':...}]
    marks_by_date: dict[str, list[dict]] = {}
    for mark in all_marks:
        key = (mark.get("mark_date") or "").strip()
        if key:
            marks_by_date.setdefault(key, []).append(mark)

    days = []

    for offset in range(5):
        day_num = offset + 1
        current = start + timedelta(days=offset)
        date_iso = current.strftime("%Y-%m-%d")
        date_ru = "%02d.%02d.%04d" % (current.day, current.month, current.year)

        day_lessons = [row for row in lessons if int(row.get("day_of_week") or 0) == day_num]

        day_tasks: dict[str, list[dict]] = {}
        for task in tasks:
            if int(task.get("day_of_week") or 0) not in (0, day_num):
                continue
            key = (task.get("subject") or "").strip().lower()
            day_tasks.setdefault(key, []).append(task)

        day_marks: dict[str, list[str]] = {}
        for mark in marks_by_date.get(date_ru, []):
            key = (mark.get("subject") or "").strip().lower()
            day_marks.setdefault(key, []).append(mark.get("mark"))

        items = []
        used: set[int] = set()

        for lesson in day_lessons:
            key = (lesson.get("subject") or "").strip().lower()
            related = day_tasks.get(key, [])
            used.add(id(related))
            items.append(
                {
                    "lesson": lesson.get("lesson"),
                    "subject": lesson.get("subject"),
                    "room": lesson.get("room"),
                    "start_time": lesson.get("start_time"),
                    "end_time": lesson.get("end_time"),
                    "homework": related,
                    "marks": day_marks.get(key, []),
                }
            )

        for key, related in day_tasks.items():
            if id(related) in used:
                continue
            for task in related:
                items.append(
                    {
                        "lesson": None,
                        "subject": task.get("subject"),
                        "room": None,
                        "start_time": None,
                        "end_time": None,
                        "homework": [task],
                        "marks": day_marks.get(key, []),
                    }
                )

        days.append(
            {
                "day": day_num,
                "date": date_iso,
                "date_ru": date_ru,
                "items": items,
                "lessons_count": len(day_lessons),
                "homework_count": sum(len(item["homework"]) for item in items),
            }
        )

    return {
        "class_name": class_name,
        "monday": start.strftime("%Y-%m-%d"),
        "days": days,
    }


@router.get("/day")
def day(
    day: int = Query(default=1, ge=1, le=7),
    date: str = Query(default="", max_length=10),
    student: dict = Depends(current_student),
    store=Depends(get_store),
) -> dict:
    """Всё для экрана «Дневник»: уроки, задания и оценки на день.

    Параметры:
      day  — день недели (1 = понедельник)
      date — дата в виде YYYY-MM-DD, по ней подбираются оценки
    """
    class_name = student.get("class_name") or ""

    lessons = store.list_schedule(class_name) if class_name else []
    lessons = [row for row in lessons if int(row.get("day_of_week") or 0) == day]

    tasks = store.list_homework(class_name) if class_name else []

    # Оценки за выбранную дату. В базе дата хранится как «12.11.2026».
    marks_by_subject: dict[str, list[dict]] = {}
    if date:
        try:
            year, month, day_num = (int(part) for part in date.split("-"))
            target = "%02d.%02d.%04d" % (day_num, month, year)
        except (ValueError, TypeError):
            target = ""

        if target:
            for mark in store.list_marks(student["id"]):
                if (mark.get("mark_date") or "").strip() != target:
                    continue
                key = (mark.get("subject") or "").strip().lower()
                marks_by_subject.setdefault(key, []).append(mark.get("mark"))

    # Задания привязываем к уроку по названию предмета и дню недели.
    by_subject: dict[str, list[dict]] = {}
    for task in tasks:
        if int(task.get("day_of_week") or 0) not in (0, day):
            continue
        by_subject.setdefault((task.get("subject") or "").strip().lower(), []).append(task)

    items = []
    used: set[int] = set()

    for lesson in lessons:
        key = (lesson.get("subject") or "").strip().lower()
        related = by_subject.get(key, [])
        used.add(id(related))
        items.append(
            {
                "lesson": lesson.get("lesson"),
                "subject": lesson.get("subject"),
                "room": lesson.get("room"),
                "start_time": lesson.get("start_time"),
                "end_time": lesson.get("end_time"),
                "homework": related,
                "marks": marks_by_subject.get(key, []),
            }
        )

    # Задания по предметам, которых нет в расписании этого дня.
    for key, related in by_subject.items():
        if id(related) in used:
            continue
        for task in related:
            items.append(
                {
                    "lesson": None,
                    "subject": task.get("subject"),
                    "room": None,
                    "start_time": None,
                    "end_time": None,
                    "homework": [task],
                    "marks": marks_by_subject.get(key, []),
                }
            )

    homework_count = sum(len(item["homework"]) for item in items)

    return {
        "day": day,
        "date": date,
        "class_name": class_name,
        "items": items,
        "lessons_count": len(lessons),
        "homework_count": homework_count,
    }


@router.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "db": "supabase" if _settings.db_enabled else "memory",
        "bot": bool(_settings.bot_enabled),
        "demo": _settings.demo_mode,
        "env": diagnostics(),
    }
