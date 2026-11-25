"""Доступ к данным: Supabase (PostgREST) или память для локальной разработки.

Интерфейс одинаковый, поэтому API не знает, где лежат данные.
В продакшене используется SupabaseStore с service_role ключом.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# --------------------------------------------------------------------------
# Утилиты времени
# --------------------------------------------------------------------------


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_expired(value: str | None) -> bool:
    moment = parse_iso(value)
    return moment is None or moment <= now()


# --------------------------------------------------------------------------
# Supabase (PostgREST)
# --------------------------------------------------------------------------


class SupabaseStore:
    """Работа с таблицами через REST API Supabase (service_role)."""

    def __init__(self, url: str, key: str, timeout: int = 15) -> None:
        self.base = url.rstrip("/")
        self.key = key
        self.timeout = timeout

    # -- низкий уровень ----------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: dict | list | None = None,
        prefer: str | None = None,
    ):
        url = f"{self.base}/rest/v1/{path}"
        if params:
            url += "?" + urlencode(params)

        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(url, data=data, method=method)
        request.add_header("apikey", self.key)
        request.add_header("Authorization", f"Bearer {self.key}")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        if prefer:
            request.add_header("Prefer", prefer)

        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"Supabase {method} {path}: HTTP {exc.code} {detail}") from exc

        return json.loads(raw) if raw else None

    def _select_one(self, table: str, **filters) -> dict | None:
        params = {"select": "*", "limit": "1", **{k: f"eq.{v}" for k, v in filters.items()}}
        rows = self._request("GET", table, params=params)
        return rows[0] if rows else None

    # -- студенты ----------------------------------------------------------

    def find_student_by_login(self, login: str) -> dict | None:
        return self._select_one("students", login=login.lower())

    def find_student_by_esia(self, esia_id: str) -> dict | None:
        return self._select_one("students", esia_id=esia_id)

    def get_student(self, student_id: str) -> dict | None:
        return self._select_one("students", id=student_id)

    def add_student(
        self,
        login: str,
        password_hash: str | None,
        full_name: str,
        class_name: str = "",
        esia_id: str | None = None,
    ) -> dict:
        rows = self._request(
            "POST",
            "students",
            body={
                "login": login.lower(),
                "password_hash": password_hash,
                "full_name": full_name,
                "class_name": class_name,
                "esia_id": esia_id,
            },
            prefer="return=representation",
        )
        return rows[0]

    def set_esia_id(self, student_id: str, esia_id: str) -> None:
        """Привязывает идентификатор Госуслуг к ученику."""
        self._request(
            "PATCH",
            "students",
            params={"id": f"eq.{student_id}"},
            body={"esia_id": esia_id},
            prefer="return=minimal",
        )

    # -- запросы на вход ---------------------------------------------------

    def create_auth_request(self, student_id: str, method: str, ttl: int) -> dict:
        rows = self._request(
            "POST",
            "auth_requests",
            body={
                "student_id": student_id,
                "method": method,
                "status": "pending",
                "expires_at": iso(now() + timedelta(seconds=ttl)),
            },
            prefer="return=representation",
        )
        return rows[0]

    def get_auth_request(self, request_id: str) -> dict | None:
        return self._select_one("auth_requests", id=request_id)

    def update_auth_request(self, request_id: str, **fields) -> dict | None:
        rows = self._request(
            "PATCH",
            "auth_requests",
            params={"id": f"eq.{request_id}"},
            body=fields,
            prefer="return=representation",
        )
        return rows[0] if rows else None

    # -- отметки -----------------------------------------------------------

    def list_marks(self, student_id: str) -> list[dict]:
        return self._request(
            "GET",
            "marks",
            params={
                "select": "*",
                "student_id": f"eq.{student_id}",
                "order": "mark_date.desc",
            },
        ) or []

    # -- расписание и домашние задания -------------------------------------

    def list_schedule(self, class_name: str) -> list[dict]:
        return self._request(
            "GET",
            "schedule",
            params={
                "select": "*",
                "class_name": f"eq.{class_name}",
                "order": "day_of_week.asc,lesson.asc",
            },
        ) or []

    def list_homework(self, class_name: str) -> list[dict]:
        try:
            return self._request(
                "GET",
                "homework",
                params={
                    "select": "*",
                    "class_name": f"eq.{class_name}",
                    "order": "due_date.asc,subject.asc",
                },
            ) or []
        except RuntimeError:
            # Таблица homework ещё не создана (не применён sql/03_homework.sql).
            # Расписание при этом должно работать, поэтому отдаём пустой список.
            return []

    # -- запись (данные из закладки / парсера) ------------------------------

    def clear_marks(self, student_id: str) -> None:
        """Удаляет оценки ученика перед новой загрузкой."""
        self._request(
            "DELETE",
            "marks",
            params={"student_id": f"eq.{student_id}"},
            prefer="return=minimal",
        )

    def insert_marks(self, student_id: str, rows: list[dict]) -> int:
        """Вставляет оценки. rows: [{subject, mark, mark_date}]."""
        if not rows:
            return 0
        payload = [
            {
                "student_id": student_id,
                "subject": row["subject"],
                "mark": row["mark"],
                "mark_date": row["mark_date"],
            }
            for row in rows
        ]
        self._request("POST", "marks", body=payload, prefer="return=minimal")
        return len(payload)

    def clear_schedule(self, class_name: str) -> None:
        self._request(
            "DELETE",
            "schedule",
            params={"class_name": f"eq.{class_name}"},
            prefer="return=minimal",
        )

    def insert_schedule(self, rows: list[dict]) -> int:
        """Вставляет уроки. rows: [{class_name, day_of_week, lesson, subject, ...}]."""
        if not rows:
            return 0
        self._request("POST", "schedule", body=rows, prefer="return=minimal")
        return len(rows)

    def clear_homework(self, class_name: str) -> None:
        try:
            self._request(
                "DELETE",
                "homework",
                params={"class_name": f"eq.{class_name}"},
                prefer="return=minimal",
            )
        except RuntimeError:
            # Таблицы ещё нет — нечего чистить.
            pass

    def insert_homework(self, rows: list[dict]) -> int:
        """Вставляет задания. rows: [{class_name, subject, day_of_week, due_date, task}]."""
        if not rows:
            return 0
        try:
            self._request("POST", "homework", body=rows, prefer="return=minimal")
        except RuntimeError:
            return 0
        return len(rows)

    def last_sync(self) -> dict | None:
        """Последняя успешная синхронизация (для напоминаний на сайте)."""
        try:
            rows = self._request(
                "GET",
                "sync_log",
                params={
                    "select": "source,created_at,marks_count,schedule_count,homework_count",
                    "ok": "eq.true",
                    "order": "created_at.desc",
                    "limit": "1",
                },
            )
        except RuntimeError:
            # Таблицы нет — считаем, что данных о синхронизации не было.
            return None

        return rows[0] if rows else None

    def log_sync(self, source: str, marks: int, schedule: int, homework: int,
                 ok: bool = True, error: str = "") -> None:
        """Пишет отметку о синхронизации (если таблица создана)."""
        try:
            self._request(
                "POST",
                "sync_log",
                body={
                    "source": source,
                    "marks_count": marks,
                    "schedule_count": schedule,
                    "homework_count": homework,
                    "ok": ok,
                    "error": error[:500],
                },
                prefer="return=minimal",
            )
        except RuntimeError:
            # Таблицы может не быть — это не ошибка.
            pass


# --------------------------------------------------------------------------
# Память (DEV / DEMO)
# --------------------------------------------------------------------------


class MemoryStore:
    """Хранилище в памяти процесса: удобно для тестов и демо без Supabase."""

    def __init__(self) -> None:
        self.students: dict[str, dict] = {}
        self.requests: dict[str, dict] = {}

    def find_student_by_login(self, login: str) -> dict | None:
        login = login.lower()
        return next((s for s in self.students.values() if s["login"] == login), None)

    def find_student_by_esia(self, esia_id: str) -> dict | None:
        return next((s for s in self.students.values() if s.get("esia_id") == esia_id), None)

    def get_student(self, student_id: str) -> dict | None:
        return self.students.get(student_id)

    def add_student(
        self,
        login: str,
        password_hash: str | None,
        full_name: str,
        class_name: str = "",
        esia_id: str | None = None,
    ) -> dict:
        student = {
            "id": str(uuid.uuid4()),
            "login": login.lower(),
            "password_hash": password_hash,
            "full_name": full_name,
            "class_name": class_name,
            "esia_id": esia_id,
            "created_at": iso(now()),
        }
        self.students[student["id"]] = student
        return student

    def set_esia_id(self, student_id: str, esia_id: str) -> None:
        student = self.students.get(student_id)
        if student:
            student["esia_id"] = esia_id

    def create_auth_request(self, student_id: str, method: str, ttl: int) -> dict:
        request = {
            "id": str(uuid.uuid4()),
            "student_id": student_id,
            "method": method,
            "status": "pending",
            "code": None,
            "telegram_id": None,
            "attempts": 0,
            "expires_at": iso(now() + timedelta(seconds=ttl)),
            "code_expires_at": None,
            "created_at": iso(now()),
        }
        self.requests[request["id"]] = request
        return request

    def get_auth_request(self, request_id: str) -> dict | None:
        return self.requests.get(request_id)

    def update_auth_request(self, request_id: str, **fields) -> dict | None:
        request = self.requests.get(request_id)
        if not request:
            return None
        request.update(fields)
        return request

    def list_marks(self, student_id: str) -> list[dict]:
        return [m for m in self._marks if m.get("student_id") == student_id]

    def list_schedule(self, class_name: str) -> list[dict]:
        return [s for s in self._schedule if s.get("class_name") == class_name]

    def list_homework(self, class_name: str) -> list[dict]:
        return [h for h in self._homework if h.get("class_name") == class_name]

    # -- запись (в памяти: складываем в списки) -----------------------------

    @property
    def _marks(self) -> list[dict]:
        if not hasattr(self, "_marks_store"):
            self._marks_store: list[dict] = []
        return self._marks_store

    @property
    def _schedule(self) -> list[dict]:
        if not hasattr(self, "_schedule_store"):
            self._schedule_store: list[dict] = []
        return self._schedule_store

    @property
    def _homework(self) -> list[dict]:
        if not hasattr(self, "_homework_store"):
            self._homework_store: list[dict] = []
        return self._homework_store

    def clear_marks(self, student_id: str) -> None:
        self._marks_store = [
            m for m in self._marks if m.get("student_id") != student_id
        ]

    def insert_marks(self, student_id: str, rows: list[dict]) -> int:
        for row in rows:
            self._marks.append(
                {
                    "id": str(uuid.uuid4()),
                    "student_id": student_id,
                    "subject": row["subject"],
                    "mark": row["mark"],
                    "mark_date": row["mark_date"],
                }
            )
        return len(rows)

    def clear_schedule(self, class_name: str) -> None:
        self._schedule_store = [
            s for s in self._schedule if s.get("class_name") != class_name
        ]

    def insert_schedule(self, rows: list[dict]) -> int:
        for row in rows:
            self._schedule.append({**row, "id": str(uuid.uuid4())})
        return len(rows)

    def clear_homework(self, class_name: str) -> None:
        self._homework_store = [
            h for h in self._homework if h.get("class_name") != class_name
        ]

    def insert_homework(self, rows: list[dict]) -> int:
        for row in rows:
            self._homework.append({**row, "id": str(uuid.uuid4())})
        return len(rows)

    def log_sync(self, source: str, marks: int, schedule: int, homework: int,
                 ok: bool = True, error: str = "") -> None:
        """Запоминает время синхронизации (в памяти)."""
        from datetime import datetime, timezone

        self._last_sync = {
            "source": source,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "marks_count": marks,
            "schedule_count": schedule,
            "homework_count": homework,
        }

    def last_sync(self) -> dict | None:
        return getattr(self, "_last_sync", None)


def build_store(settings) -> SupabaseStore | MemoryStore:
    if settings.db_enabled:
        return SupabaseStore(settings.supabase_url, settings.supabase_key)
    return MemoryStore()
