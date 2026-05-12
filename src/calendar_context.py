from __future__ import annotations

import json
import logging
import os
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .external_context_storage import (
    DEFAULT_DB_PATH,
    get_calendar_row,
    save_calendar_row,
)

log = logging.getLogger("avito.external.calendar")
REPORT_TZ = ZoneInfo(os.environ.get("REPORT_TIMEZONE", "Asia/Novosibirsk"))

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CALENDAR_CONFIG = BASE_DIR / "config" / "ru_calendar_2026.json"

WEEKDAY_NAMES_RU = {
    1: "понедельник",
    2: "вторник",
    3: "среда",
    4: "четверг",
    5: "пятница",
    6: "суббота",
    7: "воскресенье",
}


def _parse_date(value: str | date_cls) -> date_cls:
    if isinstance(value, date_cls):
        return value
    return datetime.fromisoformat(str(value)).date()


def _date_str(value: str | date_cls) -> str:
    return _parse_date(value).strftime("%Y-%m-%d")


def _default_config_for_year(year: int) -> Path:
    path = BASE_DIR / "config" / f"ru_calendar_{year}.json"
    return path if path.exists() else DEFAULT_CALENDAR_CONFIG


def _load_config(config_path: str | Path | None = None, year: int | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else _default_config_for_year(year or datetime.now(REPORT_TZ).year)
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _in_periods(day: str, periods: list[dict[str, str]]) -> tuple[bool, str | None]:
    target = _parse_date(day)
    for period in periods:
        start = _parse_date(period["start"])
        end = _parse_date(period["end"])
        if start <= target <= end:
            return True, period.get("name")
    return False, None


def _calendar_day_type(flags: dict[str, bool]) -> str:
    # Приоритет важен, потому что один день может иметь несколько признаков.
    if flags["is_first_workday_after_holidays"]:
        return "first_workday_after_holidays"
    if flags["is_between_holidays"]:
        return "between_holidays"
    if flags["is_preholiday"]:
        return "preholiday"
    if flags["is_public_holiday"]:
        return "public_holiday"
    if flags["is_long_weekend"]:
        return "long_weekend"
    if flags["is_weekend"]:
        return "weekend"
    return "working_day"


def get_calendar_context(
    date: str | date_cls,
    *,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    day = _date_str(date)
    parsed = _parse_date(day)
    config = _load_config(config_path, parsed.year)

    if parsed.year != int(config.get("year", parsed.year)):
        log.warning("Для даты %s нет отдельного производственного календаря, используется базовая логика выходных", day)

    weekday = parsed.isoweekday()
    public_holidays = config.get("public_holidays", {})
    non_working_overrides = config.get("non_working_day_overrides", {})
    working_overrides = config.get("working_day_overrides", {})
    preholidays = config.get("preholidays", {})
    first_workdays = config.get("first_workdays_after_holidays", {})

    is_regular_weekend = weekday in (6, 7)
    is_public_holiday = day in public_holidays
    is_non_working_override = day in non_working_overrides
    is_working_override = day in working_overrides
    is_long_weekend, long_weekend_name = _in_periods(day, config.get("long_weekends", []))
    is_between_holidays, _between_name = _in_periods(day, config.get("between_holidays", []))
    is_preholiday = day in preholidays
    is_first_workday_after_holidays = day in first_workdays

    is_weekend = is_regular_weekend
    is_working_day = not (is_regular_weekend or is_public_holiday or is_non_working_override) or is_working_override
    holiday_name = public_holidays.get(day) or non_working_overrides.get(day)
    if not holiday_name and is_long_weekend and is_public_holiday:
        holiday_name = long_weekend_name

    flags = {
        "is_weekend": is_weekend,
        "is_working_day": is_working_day,
        "is_public_holiday": is_public_holiday,
        "is_long_weekend": is_long_weekend,
        "is_preholiday": is_preholiday,
        "is_between_holidays": is_between_holidays,
        "is_first_workday_after_holidays": is_first_workday_after_holidays,
    }

    return {
        "date": day,
        "weekday": weekday,
        "weekday_name": WEEKDAY_NAMES_RU[weekday],
        "is_weekend": flags["is_weekend"],
        "is_working_day": flags["is_working_day"],
        "is_public_holiday": flags["is_public_holiday"],
        "holiday_name": holiday_name,
        "is_long_weekend": flags["is_long_weekend"],
        "is_preholiday": flags["is_preholiday"],
        "is_between_holidays": flags["is_between_holidays"],
        "is_first_workday_after_holidays": flags["is_first_workday_after_holidays"],
        "calendar_day_type": _calendar_day_type(flags),
    }


def save_calendar_daily(calendar_row: dict[str, Any], db_path: str | Path | None = None) -> None:
    save_calendar_row(calendar_row, db_path)


def get_calendar_daily(
    date: str | date_cls,
    *,
    force_update: bool = False,
    db_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    day = _date_str(date)
    db_path = db_path or DEFAULT_DB_PATH
    if not force_update:
        cached = get_calendar_row(day, db_path)
        if cached:
            return cached

    row = get_calendar_context(day, config_path=config_path)
    save_calendar_daily(row, db_path)
    return row


def iter_dates(start: str, end: str) -> list[str]:
    current = _parse_date(start)
    finish = _parse_date(end)
    dates: list[str] = []
    while current <= finish:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates
