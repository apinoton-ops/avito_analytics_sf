from __future__ import annotations

import csv
import logging
import sqlite3
from pathlib import Path
from typing import Any

from .external_context_storage import DEFAULT_DB_PATH

log = logging.getLogger("avito.external.export")

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_EXPORT_DIR = BASE_DIR / "reports" / "data_exports"

EXPORT_TABLES = {
    "avito_daily_enriched": "date, account, ad_id",
    "weather_daily": "date, city",
    "calendar_daily": "date",
}


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def export_table_to_csv(
    conn: sqlite3.Connection,
    table: str,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    if table not in EXPORT_TABLES:
        raise ValueError(f"Экспорт таблицы не разрешен: {table}")

    output_path = Path(output_dir) if output_dir else DEFAULT_EXPORT_DIR
    output_path.mkdir(parents=True, exist_ok=True)
    csv_path = output_path / f"{table}.csv"

    if not _table_exists(conn, table):
        log.warning("Таблица %s не найдена, экспорт пропущен", table)
        return {"table": table, "path": str(csv_path), "rows": 0, "exists": False}

    cursor = conn.execute(f"SELECT * FROM {table} ORDER BY {EXPORT_TABLES[table]}")
    columns = [col[0] for col in cursor.description]
    rows_count = 0

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in cursor:
            writer.writerow({column: row[column] for column in columns})
            rows_count += 1

    log.info("CSV экспортирован: %s (%s строк)", csv_path, rows_count)
    return {"table": table, "path": str(csv_path), "rows": rows_count, "exists": True}


def export_external_context_csvs(
    db_path: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not path.exists():
        log.warning("SQLite-файл не найден, экспорт CSV пропущен: %s", path)
        return []

    with _connect(path) as conn:
        return [export_table_to_csv(conn, table, output_dir) for table in EXPORT_TABLES]


def get_data_summary(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not path.exists():
        return []

    summary: list[dict[str, Any]] = []
    with _connect(path) as conn:
        for table in EXPORT_TABLES:
            if not _table_exists(conn, table):
                summary.append({"table": table, "exists": False, "rows": 0})
                continue
            row = conn.execute(
                f"SELECT COUNT(*) AS rows_count, MIN(date) AS min_date, MAX(date) AS max_date FROM {table}"
            ).fetchone()
            item = {
                "table": table,
                "exists": True,
                "rows": row["rows_count"],
                "min_date": row["min_date"],
                "max_date": row["max_date"],
            }
            if table == "weather_daily":
                cities = conn.execute(
                    "SELECT COUNT(DISTINCT city) AS cities_count FROM weather_daily"
                ).fetchone()
                item["cities"] = cities["cities_count"]
            if table == "avito_daily_enriched":
                accounts = conn.execute(
                    "SELECT COUNT(DISTINCT account) AS accounts_count FROM avito_daily_enriched"
                ).fetchone()
                item["accounts"] = accounts["accounts_count"]
            summary.append(item)
    return summary
