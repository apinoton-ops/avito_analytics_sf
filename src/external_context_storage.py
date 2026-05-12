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

AVITO_ENRICHED_BOOL_FIELDS = WEATHER_BOOL_FIELDS | CALENDAR_BOOL_FIELDS

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

AVITO_ENRICHED_COLUMNS = [
    "date",
    "account",
    "payment_model",
    "ad_id",
    "theme",
    "title_full",
    "city",
    "price",
    "views_today",
    "contacts_today",
    "favorites_today",
    "views_delta",
    "contacts_delta",
    "favorites_delta",
    "views_total",
    "contacts_total",
    "favorites_total",
    "views_90d",
    "contacts_90d",
    "favorites_90d",
    "impressions_30d",
    "contacts_show_phone_today",
    "contacts_messenger_today",
    "spending_today",
    "cpl_today",
    "average_view_cost",
    "average_contact_cost",
    "days_on_avito",
    "vas",
    "status",
    "data_quality",
    "stats_v1_status",
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
    "saved_at",
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS avito_daily_enriched (
            date TEXT NOT NULL,
            account TEXT NOT NULL,
            payment_model TEXT,
            ad_id TEXT NOT NULL,
            theme TEXT,
            title_full TEXT,
            city TEXT,
            price REAL,
            views_today INTEGER,
            contacts_today INTEGER,
            favorites_today INTEGER,
            views_delta INTEGER,
            contacts_delta INTEGER,
            favorites_delta INTEGER,
            views_total INTEGER,
            contacts_total INTEGER,
            favorites_total INTEGER,
            views_90d INTEGER,
            contacts_90d INTEGER,
            favorites_90d INTEGER,
            impressions_30d INTEGER,
            contacts_show_phone_today INTEGER,
            contacts_messenger_today INTEGER,
            spending_today REAL,
            cpl_today REAL,
            average_view_cost REAL,
            average_contact_cost REAL,
            days_on_avito INTEGER,
            vas TEXT,
            status TEXT,
            data_quality TEXT,
            stats_v1_status TEXT,
            temperature_2m_mean REAL,
            temperature_2m_min REAL,
            temperature_2m_max REAL,
            precipitation_sum REAL,
            rain_sum REAL,
            snowfall_sum REAL,
            wind_speed_10m_max REAL,
            wind_gusts_10m_max REAL,
            weather_code INTEGER,
            is_rainy INTEGER,
            is_snowy INTEGER,
            is_cold_day INTEGER,
            is_bad_weather INTEGER,
            is_good_weather_for_construction INTEGER,
            weekday_name TEXT,
            is_weekend INTEGER,
            is_working_day INTEGER,
            is_public_holiday INTEGER,
            holiday_name TEXT,
            is_long_weekend INTEGER,
            is_preholiday INTEGER,
            is_between_holidays INTEGER,
            is_first_workday_after_holidays INTEGER,
            calendar_day_type TEXT,
            saved_at TEXT,
            PRIMARY KEY (date, account, ad_id)
        )
        """
    )
    _ensure_columns(
        conn,
        "avito_daily_enriched",
        {
            "data_quality": "TEXT",
            "stats_v1_status": "TEXT",
        },
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_avito_daily_enriched_date ON avito_daily_enriched(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_avito_daily_enriched_city ON avito_daily_enriched(city)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_avito_daily_enriched_account ON avito_daily_enriched(account)")
    conn.commit()


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


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


def save_avito_enriched_rows(rows: list[dict[str, Any]], db_path: str | Path | None = None) -> int:
    if not rows:
        return 0

    columns = ", ".join(AVITO_ENRICHED_COLUMNS)
    placeholders = ", ".join("?" for _ in AVITO_ENRICHED_COLUMNS)
    updates = ", ".join(
        f"{col} = excluded.{col}"
        for col in AVITO_ENRICHED_COLUMNS
        if col not in {"date", "account", "ad_id"}
    )
    prepared = [
        [_to_storage_value(row.get(col)) for col in AVITO_ENRICHED_COLUMNS]
        for row in rows
    ]

    with closing(connect(db_path)) as conn:
        conn.executemany(
            f"""
            INSERT INTO avito_daily_enriched ({columns})
            VALUES ({placeholders})
            ON CONFLICT(date, account, ad_id) DO UPDATE SET {updates}
            """,
            prepared,
        )
        conn.commit()
    return len(rows)


def count_avito_enriched_rows(date: str | None = None, db_path: str | Path | None = None) -> int:
    with closing(connect(db_path)) as conn:
        if date:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM avito_daily_enriched WHERE date = ?",
                (date,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM avito_daily_enriched").fetchone()
    return int(row["cnt"] or 0)
