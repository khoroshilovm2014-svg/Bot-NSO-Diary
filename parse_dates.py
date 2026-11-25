"""Приведение дат к формату базы.

Портал НСО отдаёт даты в разных видах: «16.11», «16.11.2026»,
«16 ноября». В таблице marks дата хранится как «16.11.2026».
"""

from __future__ import annotations

import re
from datetime import date

DATE_RE = re.compile(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?")

MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10,
    "ноябр": 11, "декабр": 12,
}


def normalize_mark_date(text: str) -> str:
    """«16.11», «16.11.26», «16 ноября» -> «16.11.2026».

    Если год не указан — подставляется текущий с поправкой на переход
    через Новый год.
    """
    if not text:
        return ""

    text = text.strip()
    today = date.today()

    match = DATE_RE.search(text)
    if match:
        day_num = int(match.group(1))
        month = int(match.group(2))
        year_raw = match.group(3)

        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
        else:
            year = today.year
            if month == 12 and today.month == 1:
                year -= 1
            elif month == 1 and today.month == 12:
                year += 1

        if 1 <= day_num <= 31 and 1 <= month <= 12:
            return "%02d.%02d.%04d" % (day_num, month, year)

    # Дата словами: «16 ноября»
    low = text.lower()
    day_match = re.search(r"\b(\d{1,2})\b", low)
    if day_match:
        day_num = int(day_match.group(1))
        for stem, month in MONTHS_RU.items():
            if stem in low:
                year = today.year
                if month == 12 and today.month == 1:
                    year -= 1
                elif month == 1 and today.month == 12:
                    year += 1
                if 1 <= day_num <= 31:
                    return "%02d.%02d.%04d" % (day_num, month, year)

    return ""
