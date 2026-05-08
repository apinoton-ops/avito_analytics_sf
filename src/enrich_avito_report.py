from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .calendar_context import get_calendar_daily, save_calendar_daily
from .external_context_storage import DEFAULT_DB_PATH
from .weather import get_weather_daily

log = logging.getLogger("avito.external.enrich")

ACCOUNT_PAYMENT_MODELS = {
    "СтройФит": "publication",
    "КаучПол": "click_or_view",
}

WEATHER_ENRICH_FIELDS = [
    "temperature_2m_mean",
    "temperature_2m_min",
    "temperature_2m_max",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "weather_code",
    "is_rainy",
    "is_snowy",
    "is_cold_day",
    "is_bad_weather",
    "is_good_weather_for_construction",
]

CALENDAR_ENRICH_FIELDS = [
    "weekday_name",
    "is_weekend",
    "is_working_day",
    "is_public_holiday",
    "holiday_name",
    "is_long_weekend",
    "is_preholiday",
    "is_between_holidays",
    "is_first_workday_after_holidays",
    "calendar_day_type",
]


def _normalize_date(value: str | None) -> str:
    if value:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def _unique_cities(cities: Iterable[str]) -> list[str]:
    return sorted({str(city).strip() for city in cities if city and str(city).strip() and str(city).strip() != "—"})


def build_external_context_for_report(
    report_date: str | None,
    cities: Iterable[str],
    *,
    force_update: bool = False,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    date = _normalize_date(report_date)
    db_path = db_path or DEFAULT_DB_PATH
    city_list = _unique_cities(cities)

    calendar_row = get_calendar_daily(date, force_update=force_update, db_path=db_path)
    save_calendar_daily(calendar_row, db_path)

    weather_saved = 0
    weather_missing: list[str] = []
    for city in city_list:
        row = get_weather_daily(city, date, force_update=force_update, db_path=db_path)
        if row is None:
            weather_missing.append(city)
            continue
        weather_saved += 1

    if weather_missing:
        log.warning("Погода не сохранена для городов: %s", ", ".join(weather_missing))

    log.info(
        "Внешний контекст обновлен: дата %s, календарь ✓, погода %s/%s городов",
        date,
        weather_saved,
        len(city_list),
    )
    return {
        "date": date,
        "cities_requested": city_list,
        "weather_saved": weather_saved,
        "weather_missing": weather_missing,
        "calendar_saved": True,
        "db_path": str(db_path),
    }


def enrich_avito_rows_with_weather_and_calendar(
    rows: list[dict[str, Any]],
    report_date: str | None,
    *,
    force_update: bool = False,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    date = _normalize_date(report_date)
    db_path = db_path or DEFAULT_DB_PATH
    cities = [row.get("city") for row in rows]
    build_external_context_for_report(date, cities, force_update=force_update, db_path=db_path)
    calendar_row = get_calendar_daily(date, db_path=db_path)

    enriched: list[dict[str, Any]] = []
    for row in rows:
        city = row.get("city")
        weather_row = get_weather_daily(city, date, db_path=db_path) if city else None
        enriched_row = dict(row)
        enriched_row["date"] = date
        enriched_row["payment_model"] = ACCOUNT_PAYMENT_MODELS.get(row.get("account"))

        for field in WEATHER_ENRICH_FIELDS:
            enriched_row[field] = weather_row.get(field) if weather_row else None
        for field in CALENDAR_ENRICH_FIELDS:
            enriched_row[field] = calendar_row.get(field) if calendar_row else None

        enriched.append(enriched_row)

    return enriched
