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
        return []


def build_store(settings) -> SupabaseStore | MemoryStore:
    if settings.db_enabled:
        return SupabaseStore(settings.supabase_url, settings.supabase_key)
    return MemoryStore()
