#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Экспорт накопленных SQLite-данных в CSV."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from src.data_export import DEFAULT_EXPORT_DIR, export_external_context_csvs, get_data_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Экспортировать накопленные данные Avito/погоды/календаря в CSV")
    parser.add_argument("--db", default=None, help="Путь к SQLite-файлу, по умолчанию data/external_context.sqlite")
    parser.add_argument("--output-dir", default=str(DEFAULT_EXPORT_DIR), help="Папка для CSV")
    parser.add_argument("--summary-only", action="store_true", help="Только показать краткую сводку без экспорта")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args()
    db_path = Path(args.db) if args.db else None
    output_dir = Path(args.output_dir)

    summary = get_data_summary(db_path)
    logging.info("Сводка накопленных данных: %s", json.dumps(summary, ensure_ascii=False))

    if args.summary_only:
        return

    result = export_external_context_csvs(db_path, output_dir)
    logging.info("CSV экспорт: %s", json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
