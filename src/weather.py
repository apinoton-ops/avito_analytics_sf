from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .external_context_storage import (
    DEFAULT_DB_PATH,
    get_weather_row,
    save_weather_row,
)

log = logging.getLogger("avito.external.weather")

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CITIES_CONFIG = BASE_DIR / "config" / "cities.json"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_SOURCE = "open-meteo-archive"

OPEN_METEO_DAILY_FIELDS = [
    "weather_code",
    "temperature_2m_mean",
    "temperature_2m_min",
    "temperature_2m_max",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
]

# Пороги вынесены отдельно, чтобы быстро менять бизнес-логику без переписывания кода.
WEATHER_THRESHOLDS = {
    "cold_day_max_temp_c": 8.0,
    "good_weather_min_temp_c": 10.0,
    "bad_precipitation_mm": 1.0,
    "good_precipitation_lt_mm": 1.0,
    "bad_wind_speed_ms": 10.0,
    "bad_wind_gust_ms": 15.0,
}

SNOW_WEATHER_CODES = {71, 73, 75, 77, 85, 86}


def _normalize_date(value: str) -> str:
    return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _num(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_cities_config(config_path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    path = Path(config_path) if config_path else DEFAULT_CITIES_CONFIG
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def get_city_coordinates(city: str, config_path: str | Path | None = None) -> dict[str, Any] | None:
    cities = load_cities_config(config_path)
    coords = cities.get(city)
    if not coords:
        log.warning("Город '%s' не найден в config/cities.json, погоду пропускаем", city)
        return None
    return {
        "city": city,
        "latitude": float(coords["latitude"]),
        "longitude": float(coords["longitude"]),
        "timezone": coords.get("timezone") or "auto",
    }


def fetch_weather_for_city_date(
    city: str,
    date: str,
    *,
    config_path: str | Path | None = None,
    timeout: int = 30,
) -> dict[str, Any] | None:
    report_date = _normalize_date(date)
    coords = get_city_coordinates(city, config_path)
    if not coords:
        return None

    params = {
        "latitude": coords["latitude"],
        "longitude": coords["longitude"],
        "start_date": report_date,
        "end_date": report_date,
        "daily": ",".join(OPEN_METEO_DAILY_FIELDS),
        "timezone": coords["timezone"],
        "wind_speed_unit": "ms",
        "precipitation_unit": "mm",
    }

    try:
        resp = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        log.warning("Open-Meteo не вернул погоду для %s за %s: %s", city, report_date, exc)
        return None

    return {
        "date": report_date,
        "city": city,
        "latitude": coords["latitude"],
        "longitude": coords["longitude"],
        "payload": resp.json(),
    }


def _first_daily_value(daily: dict[str, Any], field: str) -> Any:
    values = daily.get(field)
    if not isinstance(values, list) or not values:
        return None
    return values[0]


def normalize_weather(raw_weather: dict[str, Any]) -> dict[str, Any]:
    payload = raw_weather.get("payload") or {}
    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    if not dates:
        raise ValueError(f"В ответе Open-Meteo нет daily.time для {raw_weather.get('city')}")

    weather_code = _first_daily_value(daily, "weather_code")
    return {
        "date": str(dates[0]),
        "city": raw_weather["city"],
        "latitude": raw_weather["latitude"],
        "longitude": raw_weather["longitude"],
        "temperature_2m_mean": _first_daily_value(daily, "temperature_2m_mean"),
        "temperature_2m_min": _first_daily_value(daily, "temperature_2m_min"),
        "temperature_2m_max": _first_daily_value(daily, "temperature_2m_max"),
        "precipitation_sum": _first_daily_value(daily, "precipitation_sum"),
        "rain_sum": _first_daily_value(daily, "rain_sum"),
        "snowfall_sum": _first_daily_value(daily, "snowfall_sum"),
        "wind_speed_10m_max": _first_daily_value(daily, "wind_speed_10m_max"),
        "wind_gusts_10m_max": _first_daily_value(daily, "wind_gusts_10m_max"),
        "weather_code": int(weather_code) if weather_code is not None else None,
    }


def calculate_weather_flags(weather: dict[str, Any]) -> dict[str, bool]:
    temp_max = weather.get("temperature_2m_max")
    precipitation_sum = _num(weather.get("precipitation_sum"))
    rain_sum = _num(weather.get("rain_sum"))
    snowfall_sum = _num(weather.get("snowfall_sum"))
    wind_speed = _num(weather.get("wind_speed_10m_max"))
    wind_gusts = _num(weather.get("wind_gusts_10m_max"))
    weather_code = weather.get("weather_code")

    temp_max_num = _num(temp_max, default=999.0)
    is_rainy = precipitation_sum > 0 or rain_sum > 0
    is_snowy = snowfall_sum > 0 or weather_code in SNOW_WEATHER_CODES
    is_cold_day = temp_max is not None and temp_max_num < WEATHER_THRESHOLDS["cold_day_max_temp_c"]
    is_bad_weather = (
        is_cold_day
        or precipitation_sum > WEATHER_THRESHOLDS["bad_precipitation_mm"]
        or snowfall_sum > 0
        or wind_speed >= WEATHER_THRESHOLDS["bad_wind_speed_ms"]
        or wind_gusts >= WEATHER_THRESHOLDS["bad_wind_gust_ms"]
    )
    is_good_weather_for_construction = (
        temp_max is not None
        and temp_max_num >= WEATHER_THRESHOLDS["good_weather_min_temp_c"]
        and precipitation_sum < WEATHER_THRESHOLDS["good_precipitation_lt_mm"]
        and snowfall_sum == 0
        and wind_speed < WEATHER_THRESHOLDS["bad_wind_speed_ms"]
    )

    return {
        "is_rainy": is_rainy,
        "is_snowy": is_snowy,
        "is_cold_day": is_cold_day,
        "is_bad_weather": is_bad_weather,
        "is_good_weather_for_construction": is_good_weather_for_construction,
    }


def save_weather_daily(weather_row: dict[str, Any], db_path: str | Path | None = None) -> None:
    save_weather_row(weather_row, db_path)


def get_weather_daily(
    city: str,
    date: str,
    *,
    force_update: bool = False,
    db_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any] | None:
    report_date = _normalize_date(date)
    db_path = db_path or DEFAULT_DB_PATH

    if not force_update:
        cached = get_weather_row(city, report_date, db_path)
        if cached:
            return cached

    raw = fetch_weather_for_city_date(city, report_date, config_path=config_path)
    if raw is None:
        return None

    try:
        row = normalize_weather(raw)
    except Exception as exc:
        log.warning("Не удалось нормализовать погоду для %s за %s: %s", city, report_date, exc)
        return None

    row.update(calculate_weather_flags(row))
    row["source"] = OPEN_METEO_SOURCE
    row["fetched_at"] = _utc_now_iso()
    save_weather_daily(row, db_path)
    return row


def fetch_weather_forecast_placeholder(city: str) -> None:
    """TODO: добавить прогноз на завтра и 3 дня вперед через Open-Meteo Forecast API."""
    return None
