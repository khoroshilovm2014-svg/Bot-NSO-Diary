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


def student_flags(student_id: str) -> dict:
    """Флаги ученика: блокировка и ограничение синхронизации.

    Импорт ленивый: support.py сам обращается к функциям этого модуля,
    поэтому на этапе загрузки импортировать его нельзя.
    """
    try:
        import support

        return support.flags_of(student_id)
    except Exception:  # noqa: BLE001
        return {}


def current_student(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Нужна авторизация")
    payload = verify_token(authorization.split(" ", 1)[1].strip(), _settings.jwt_secret)
    if not payload:
        raise HTTPException(status_code=401, detail="Сессия истекла")

    # Ключ закладки (scope=ingest) даёт доступ только к загрузке данных,
    # но не к чтению дневника. Иначе утёкшая закладка = утёкшие оценки.
    if payload.get("scope") == "ingest":
        raise HTTPException(status_code=401, detail="Это ключ синхронизации, а не сессия")

    student = get_store().get_student(payload.get("sub", ""))
    if not student:
        raise HTTPException(status_code=401, detail="Ученик не найден")

    flags = student_flags(student["id"])
    if flags.get("blocked"):
        raise HTTPException(
            status_code=403,
            detail="Аккаунт заблокирован. Напишите в поддержку.",
        )

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


class DirectOut(BaseModel):
    token: str
    expires_in: int
    student: dict


@router.post("/auth/direct", response_model=DirectOut)
def auth_direct(body: LoginIn, store=Depends(get_store)) -> DirectOut:
    """Вход по логину и паролю — сразу сессия, без кода в Telegram.

    Раньше после пароля нужно было открыть бота и ввести 6-значный код.
    Для личного дневника это лишний шаг, поэтому здесь сессия выдаётся сразу.
    Срок жизни — 90 дней, чтобы не входить постоянно.
    """
    student = store.find_student_by_login(body.login.strip())

    if not student or not verify_password(
        body.password, student.get("password_hash") or ""
    ):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    ttl = 60 * 60 * 24 * 90
    token = issue_token(
        {"sub": student["id"], "scope": "session"},
        _settings.jwt_secret,
        ttl,
    )

    return DirectOut(
        token=token,
        expires_in=ttl,
        student={
            "id": student["id"],
            "name": student.get("full_name"),
            "class_name": student.get("class_name"),
        },
    )


@router.post("/auth/gosuslugi", response_model=RequestOut)
def gosuslugi(body: EsiaIn, store=Depends(get_store)) -> RequestOut:
    """Старый простой вход: код ЕСИА сверяется с полем students.esia_id.

    Оставлен для совместимости. Полноценный вход — через /auth/esia/start.
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


# --------------------------------------------------------------------------
# Приём данных из закладки (bookmarklet)
#
# Зачем: официальный дневник НСО защищён антиботом ServicePipe — обычные
# HTTP-запросы получают заглушку с JS-проверкой. Но в браузере ученика
# страница уже открыта и проверку прошла. Поэтому сбор идёт на стороне
# браузера (закладка), а сюда приходит готовый результат.
#
# Так работает и на телефоне: не нужен ни Python, ни Chrome, ни сервер
# с браузером. Достаточно открыть дневник и нажать закладку.
# --------------------------------------------------------------------------


class BookmarkMarkIn(BaseModel):
    subject: str = Field(min_length=1, max_length=120)
    mark: str = Field(min_length=1, max_length=16)
    mark_date: str = Field(min_length=1, max_length=32)


class BookmarkLessonIn(BaseModel):
    lesson: int = Field(default=0, ge=0, le=20)
    subject: str = Field(default="", max_length=120)
    room: str = Field(default="", max_length=60)
    start_time: str = Field(default="", max_length=16)
    end_time: str = Field(default="", max_length=16)
    homework: str = Field(default="", max_length=2000)
    marks: list[str] = Field(default_factory=list)


class BookmarkDayIn(BaseModel):
    day_of_week: int = Field(default=0, ge=0, le=7)
    date: str = Field(default="", max_length=32)
    lessons: list[BookmarkLessonIn] = Field(default_factory=list)


class BookmarkIn(BaseModel):
    """Данные, собранные закладкой на странице дневника."""

    token: str = Field(min_length=8, description="Сессия NSO Diary")
    grades: list[BookmarkMarkIn] = Field(default_factory=list)
    days: list[BookmarkDayIn] = Field(default_factory=list)


class BookmarkOut(BaseModel):
    ok: bool
    marks: int
    lessons: int
    homework: int
    class_name: str = ""
    message: str = ""


class BookmarkKeyOut(BaseModel):
    key: str
    expires_in: int
    student: str = ""


@router.post("/ingest/key", response_model=BookmarkKeyOut)
def ingest_key(student: dict = Depends(current_student)) -> BookmarkKeyOut:
    """Выдаёт долгий ключ для закладки.

    Зачем отдельный ключ: закладка работает на school.nso.ru — это другой
    домен, поэтому localStorage сайта ей недоступен. Ключ встраивается
    в закладку при установке и живёт 90 дней.

    Ключ даёт доступ ТОЛЬКО к загрузке данных (/ingest/bookmarklet),
    читать чужие данные или входить с ним нельзя.
    """
    ttl = 60 * 60 * 24 * 90

    key = issue_token(
        {"sub": student["id"], "scope": "ingest", "name": student.get("full_name") or ""},
        _settings.jwt_secret,
        ttl,
    )

    return BookmarkKeyOut(
        key=key,
        expires_in=ttl,
        student=student.get("full_name") or "",
    )


@router.post("/ingest/bookmarklet", response_model=BookmarkOut)
def ingest_bookmarklet(body: BookmarkIn, store=Depends(get_store)) -> BookmarkOut:
    """Принимает оценки и дневник, собранные закладкой в браузере.

    Авторизация — ключ синхронизации (scope=ingest) или обычный JWT.
    Данные попадают только в аккаунт владельца ключа.
    """
    payload = verify_token(body.token.strip(), _settings.jwt_secret)
    if not payload:
        raise HTTPException(status_code=401, detail="Ключ недействителен — обновите закладку")

    # Ключ закладки не должен работать как сессия чтения, и наоборот.
    scope = payload.get("scope")
    if scope and scope != "ingest":
        raise HTTPException(status_code=401, detail="Неверный тип ключа")

    student = store.get_student(payload.get("sub", ""))
    if not student:
        raise HTTPException(status_code=401, detail="Ученик не найден")

    flags = student_flags(student["id"])

    if flags.get("blocked"):
        raise HTTPException(
            status_code=403,
            detail="Аккаунт заблокирован. Напишите в поддержку.",
        )

    if flags.get("sync_disabled"):
        raise HTTPException(
            status_code=403,
            detail="Синхронизация временно ограничена администратором.",
        )

    class_name = (student.get("class_name") or "").strip()

    # --- Оценки -----------------------------------------------------------
    marks_saved = 0

    from parse_dates import normalize_mark_date

    collected: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add_mark(subject: str, mark: str, date_text: str) -> None:
        """Добавляет оценку, если такой ещё нет."""
        subject = (subject or "").strip()
        mark = (mark or "").strip()
        mark_date = normalize_mark_date(date_text)

        if not subject or not mark or not mark_date:
            return

        key = (subject.lower(), mark, mark_date)
        if key in seen:
            return

        seen.add(key)
        collected.append(
            {
                "student_id": student["id"],
                "subject": subject,
                "mark": mark,
                "mark_date": mark_date,
            }
        )

    # Оценки берём ТОЛЬКО со страницы успеваемости: там есть точные даты.
    #
    # Раньше здесь была «догадка»: если оценок нет, брать их из уроков.
    # Из-за неё запуск закладки на странице дневника стирал все оценки
    # и заменял их парой чисел из уроков. Убрано.
    for item in body.grades:
        add_mark(item.subject, item.mark, item.mark_date)

    # Заменяем оценки только если пришёл полный список со страницы успеваемости.
    # Пустой список означает «страница не открылась» — тогда ничего не трогаем.
    if collected:
        store.clear_marks(student["id"])
        marks_saved = store.insert_marks(student["id"], collected)

    # --- Дневник ----------------------------------------------------------
    lessons_saved = 0
    homework_saved = 0

    if body.days and class_name:
        schedule_rows = []
        homework_rows = []

        for day in body.days:
            day_of_week = day.day_of_week or 1
            date_ru = day.date or ""

            for lesson in day.lessons:
                subject = lesson.subject.strip()
                if not subject:
                    continue

                schedule_rows.append(
                    {
                        "class_name": class_name,
                        "day_of_week": day_of_week,
                        "lesson": lesson.lesson or (len(schedule_rows) + 1),
                        "subject": subject,
                        "room": lesson.room.strip(),
                        "start_time": lesson.start_time.strip(),
                        "end_time": lesson.end_time.strip(),
                    }
                )

                task = lesson.homework.strip()
                if task:
                    homework_rows.append(
                        {
                            "class_name": class_name,
                            "subject": subject,
                            "day_of_week": day_of_week,
                            "due_date": date_ru,
                            "task": task,
                        }
                    )

        if schedule_rows:
            store.clear_schedule(class_name)
            store.clear_homework(class_name)
            lessons_saved = store.insert_schedule(schedule_rows)
            homework_saved = store.insert_homework(homework_rows)

    if hasattr(store, "log_sync"):
        try:
            store.log_sync(
                "bookmarklet",
                marks=marks_saved,
                schedule=lessons_saved,
                homework=homework_saved,
                ok=True,
            )
        except Exception:  # noqa: BLE001
            pass

    return BookmarkOut(
        ok=True,
        marks=marks_saved,
        lessons=lessons_saved,
        homework=homework_saved,
        class_name=class_name,
        message="Данные загружены",
    )


@router.get("/sync/status")
def sync_status(student: dict = Depends(current_student), store=Depends(get_store)) -> dict:
    """Когда данные обновлялись в последний раз.

    Сайт показывает напоминание «пора обновить через закладку»,
    если прошло больше двух часов.
    """
    from datetime import datetime, timezone

    last = None
    source = ""

    getter = getattr(store, "last_sync", None)
    if callable(getter):
        try:
            last = getter()
        except Exception:  # noqa: BLE001
            last = None

    if isinstance(last, dict):
        source = last.get("source") or ""
        last = last.get("created_at")

    if not last:
        return {
            "last_sync": None,
            "source": source,
            "minutes_ago": None,
            "stale": True,
        }

    # Считаем, сколько минут назад это было.
    minutes = None
    try:
        moment = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        minutes = int((datetime.now(timezone.utc) - moment).total_seconds() // 60)
    except (ValueError, TypeError):
        minutes = None

    return {
        "last_sync": str(last),
        "source": source,
        "minutes_ago": minutes,
        "stale": minutes is None or minutes >= 120,
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


# --------------------------------------------------------------------------
# Поддержка и админ-панель
#
# Модуль support.py подключается последним: он обращается к функциям
# этого файла (_settings, get_store, current_student), поэтому импорт
# должен идти после их определения.
# --------------------------------------------------------------------------

from support import router as support_router  # noqa: E402

router.include_router(support_router)
