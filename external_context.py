#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI для загрузки погодного и календарного контекста."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from src.enrich_avito_report import build_external_context_for_report
from src.weather import load_cities_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Собрать внешний контекст для Avito-отчета")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Дата отчета YYYY-MM-DD")
    parser.add_argument("--cities", nargs="*", help="Города. Если не указаны, берутся все города из config/cities.json")
    parser.add_argument("--force-update", action="store_true", help="Перезапросить погоду даже при наличии кеша")
    parser.add_argument("--db", default=None, help="Путь к SQLite-файлу, по умолчанию data/external_context.sqlite")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    args = parse_args()
    cities = args.cities or sorted(load_cities_config().keys())
    db_path = Path(args.db) if args.db else None
    result = build_external_context_for_report(
        args.date,
        cities,
        force_update=args.force_update,
        db_path=db_path,
    )
    logging.info("Готово: %s", result)


if __name__ == "__main__":
    main()
