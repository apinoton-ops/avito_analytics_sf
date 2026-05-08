from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "external_context.sqlite"

WEATHER_BOOL_FIELDS = {
    "is_rainy",
    "is_snowy",
    "is_cold_day",
    "is_bad_weather",
    "is_good_weather_for_construction",
}

CALENDAR_BOOL_FIELDS = {
    "is_weekend",
    "is_working_day",
    "is_public_holiday",
    "is_long_weekend",
    "is_preholiday",
    "is_between_holidays",
    "is_first_workday_after_holidays",
}

WEATHER_COLUMNS = [
    "date",
    "city",
    "latitude",
    "longitude",
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
    "source",
    "fetched_at",
]

CALENDAR_COLUMNS = [
    "date",
    "weekday",
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


def _db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path) if db_path else DEFAULT_DB_PATH


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weather_daily (
            date TEXT NOT NULL,
            city TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            temperature_2m_mean REAL,
            temperature_2m_min REAL,
            temperature_2m_max REAL,
            precipitation_sum REAL,
            rain_sum REAL,
            snowfall_sum REAL,
            wind_speed_10m_max REAL,
            wind_gusts_10m_max REAL,
            weather_code INTEGER,
            is_rainy INTEGER NOT NULL DEFAULT 0,
            is_snowy INTEGER NOT NULL DEFAULT 0,
            is_cold_day INTEGER NOT NULL DEFAULT 0,
            is_bad_weather INTEGER NOT NULL DEFAULT 0,
            is_good_weather_for_construction INTEGER NOT NULL DEFAULT 0,
            source TEXT,
            fetched_at TEXT,
            PRIMARY KEY (date, city)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_daily (
            date TEXT PRIMARY KEY,
            weekday INTEGER NOT NULL,
            weekday_name TEXT NOT NULL,
            is_weekend INTEGER NOT NULL DEFAULT 0,
            is_working_day INTEGER NOT NULL DEFAULT 0,
            is_public_holiday INTEGER NOT NULL DEFAULT 0,
            holiday_name TEXT,
            is_long_weekend INTEGER NOT NULL DEFAULT 0,
            is_preholiday INTEGER NOT NULL DEFAULT 0,
            is_between_holidays INTEGER NOT NULL DEFAULT 0,
            is_first_workday_after_holidays INTEGER NOT NULL DEFAULT 0,
            calendar_day_type TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _to_storage_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value


def _from_storage_row(row: sqlite3.Row | None, bool_fields: set[str]) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for field in bool_fields:
        if field in data and data[field] is not None:
            data[field] = bool(data[field])
    return data


def get_weather_row(city: str, date: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM weather_daily WHERE date = ? AND city = ?",
            (date, city),
        ).fetchone()
    return _from_storage_row(row, WEATHER_BOOL_FIELDS)


def save_weather_row(weather_row: dict[str, Any], db_path: str | Path | None = None) -> None:
    row = {col: _to_storage_value(weather_row.get(col)) for col in WEATHER_COLUMNS}
    columns = ", ".join(WEATHER_COLUMNS)
    placeholders = ", ".join("?" for _ in WEATHER_COLUMNS)
    updates = ", ".join(f"{col} = excluded.{col}" for col in WEATHER_COLUMNS if col not in {"date", "city"})
    with closing(connect(db_path)) as conn:
        conn.execute(
            f"""
            INSERT INTO weather_daily ({columns})
            VALUES ({placeholders})
            ON CONFLICT(date, city) DO UPDATE SET {updates}
            """,
            [row[col] for col in WEATHER_COLUMNS],
        )
        conn.commit()


def get_calendar_row(date: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM calendar_daily WHERE date = ?",
            (date,),
        ).fetchone()
    return _from_storage_row(row, CALENDAR_BOOL_FIELDS)


def save_calendar_row(calendar_row: dict[str, Any], db_path: str | Path | None = None) -> None:
    row = {col: _to_storage_value(calendar_row.get(col)) for col in CALENDAR_COLUMNS}
    columns = ", ".join(CALENDAR_COLUMNS)
    placeholders = ", ".join("?" for _ in CALENDAR_COLUMNS)
    updates = ", ".join(f"{col} = excluded.{col}" for col in CALENDAR_COLUMNS if col != "date")
    with closing(connect(db_path)) as conn:
        conn.execute(
            f"""
            INSERT INTO calendar_daily ({columns})
            VALUES ({placeholders})
            ON CONFLICT(date) DO UPDATE SET {updates}
            """,
            [row[col] for col in CALENDAR_COLUMNS],
        )
        conn.commit()
