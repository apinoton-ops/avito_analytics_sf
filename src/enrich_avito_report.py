from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .calendar_context import get_calendar_daily, save_calendar_daily
from .external_context_storage import (
    DEFAULT_DB_PATH,
    get_calendar_row,
    get_weather_row,
    save_avito_enriched_rows as save_enriched_rows_to_storage,
)
from .weather import get_weather_daily

log = logging.getLogger("avito.external.enrich")
REPORT_TZ = ZoneInfo(os.environ.get("REPORT_TIMEZONE", "Asia/Novosibirsk"))

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

REPORT_WEATHER_FIELDS = [
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

REPORT_CALENDAR_FIELDS = [
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
    return datetime.now(REPORT_TZ).strftime("%Y-%m-%d")


def _unique_cities(cities: Iterable[str]) -> list[str]:
    return sorted({str(city).strip() for city in cities if city and str(city).strip() and str(city).strip() != "—"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def _pick_fields(row: dict[str, Any] | None, fields: list[str]) -> dict[str, Any]:
    if not row:
        return {}
    return {field: row.get(field) for field in fields}


def build_external_context_payload_for_report(
    report_date: str | None,
    cities: Iterable[str],
    *,
    force_update: bool = False,
    db_path: str | Path | None = None,
    ensure_context: bool = True,
) -> dict[str, Any]:
    date = _normalize_date(report_date)
    db_path = db_path or DEFAULT_DB_PATH
    city_list = _unique_cities(cities)

    if ensure_context:
        build_external_context_for_report(date, city_list, force_update=force_update, db_path=db_path)

    calendar_row = get_calendar_row(date, db_path)
    weather_by_city: dict[str, dict[str, Any]] = {}
    missing_weather_cities: list[str] = []

    for city in city_list:
        weather_row = get_weather_row(city, date, db_path)
        if weather_row is None:
            missing_weather_cities.append(city)
            continue
        weather_by_city[city] = _pick_fields(weather_row, REPORT_WEATHER_FIELDS)

    return {
        "calendar": _pick_fields(calendar_row, REPORT_CALENDAR_FIELDS),
        "weatherByCity": weather_by_city,
        "missingWeatherCities": missing_weather_cities,
        "fieldNotes": {
            "calendar": "Признаки дня: рабочий/выходной/праздник, длинные выходные, предпраздничный и межпраздничный периоды.",
            "weather": "Погода по городам: температура, осадки, дождь, снег, ветер, порывы и расчетные флаги качества погоды.",
        },
    }


def enrich_avito_rows_with_weather_and_calendar(
    rows: list[dict[str, Any]],
    report_date: str | None,
    *,
    force_update: bool = False,
    db_path: str | Path | None = None,
    ensure_context: bool = True,
) -> list[dict[str, Any]]:
    date = _normalize_date(report_date)
    db_path = db_path or DEFAULT_DB_PATH
    cities = [row.get("city") for row in rows]
    if ensure_context:
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


def _to_enriched_storage_row(row: dict[str, Any], saved_at: str) -> dict[str, Any]:
    return {
        "date": row.get("date"),
        "account": row.get("account"),
        "payment_model": row.get("payment_model"),
        "ad_id": str(row.get("id")),
        "theme": row.get("title"),
        "title_full": row.get("titleFull"),
        "city": row.get("city"),
        "price": row.get("price"),
        "views_today": row.get("viewsToday"),
        "contacts_today": row.get("contactsToday"),
        "favorites_today": row.get("favoritesToday"),
        "views_delta": row.get("viewsDelta"),
        "contacts_delta": row.get("contactsDelta"),
        "favorites_delta": row.get("favoritesDelta"),
        "views_total": row.get("viewsTotal"),
        "contacts_total": row.get("contactsTotal"),
        "favorites_total": row.get("favoritesTotal"),
        "views_90d": row.get("views90d"),
        "contacts_90d": row.get("contacts90d"),
        "favorites_90d": row.get("favorites90d"),
        "impressions_30d": row.get("impressions30d"),
        "contacts_show_phone_today": row.get("contactsShowPhoneToday"),
        "contacts_messenger_today": row.get("contactsMessengerToday"),
        "spending_today": row.get("spendingRub"),
        "cpl_today": row.get("cpl"),
        "average_view_cost": row.get("averageViewCostRub"),
        "average_contact_cost": row.get("averageContactCostRub"),
        "days_on_avito": row.get("daysOnAvito"),
        "vas": row.get("vas"),
        "status": row.get("status"),
        "data_quality": row.get("dataQuality") or "ok",
        "stats_v1_status": row.get("statsV1Status") or "ok",
        "temperature_2m_mean": row.get("temperature_2m_mean"),
        "temperature_2m_min": row.get("temperature_2m_min"),
        "temperature_2m_max": row.get("temperature_2m_max"),
        "precipitation_sum": row.get("precipitation_sum"),
        "rain_sum": row.get("rain_sum"),
        "snowfall_sum": row.get("snowfall_sum"),
        "wind_speed_10m_max": row.get("wind_speed_10m_max"),
        "wind_gusts_10m_max": row.get("wind_gusts_10m_max"),
        "weather_code": row.get("weather_code"),
        "is_rainy": row.get("is_rainy"),
        "is_snowy": row.get("is_snowy"),
        "is_cold_day": row.get("is_cold_day"),
        "is_bad_weather": row.get("is_bad_weather"),
        "is_good_weather_for_construction": row.get("is_good_weather_for_construction"),
        "weekday_name": row.get("weekday_name"),
        "is_weekend": row.get("is_weekend"),
        "is_working_day": row.get("is_working_day"),
        "is_public_holiday": row.get("is_public_holiday"),
        "holiday_name": row.get("holiday_name"),
        "is_long_weekend": row.get("is_long_weekend"),
        "is_preholiday": row.get("is_preholiday"),
        "is_between_holidays": row.get("is_between_holidays"),
        "is_first_workday_after_holidays": row.get("is_first_workday_after_holidays"),
        "calendar_day_type": row.get("calendar_day_type"),
        "saved_at": saved_at,
    }


def save_enriched_avito_rows(
    rows: list[dict[str, Any]],
    report_date: str | None,
    *,
    force_update: bool = False,
    db_path: str | Path | None = None,
    ensure_context: bool = True,
) -> int:
    enriched = enrich_avito_rows_with_weather_and_calendar(
        rows,
        report_date,
        force_update=force_update,
        db_path=db_path,
        ensure_context=ensure_context,
    )
    saved_at = _utc_now_iso()
    storage_rows = [_to_enriched_storage_row(row, saved_at) for row in enriched]
    saved_count = save_enriched_rows_to_storage(storage_rows, db_path)
    log.info("Обогащенные Avito-строки сохранены: %s", saved_count)
    return saved_count
