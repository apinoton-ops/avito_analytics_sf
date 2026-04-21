#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Avito Analytics — ежедневный отчёт для GitHub Actions.
Секреты читаются из переменных окружения (GitHub Secrets).
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent

CLIENT_ID     = os.environ.get("AVITO_CLIENT_ID")
CLIENT_SECRET = os.environ.get("AVITO_CLIENT_SECRET")
USER_ID       = os.environ.get("AVITO_USER_ID")

API_BASE      = "https://api.avito.ru"
OUTPUT_DIR    = BASE_DIR / "reports"
HISTORY_FILE  = BASE_DIR / "history.json"
TEMPLATE_FILE = BASE_DIR / "template.html"

OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("avito")


def get_access_token():
    log.info("Получаем access_token…")
    resp = requests.post(
        f"{API_BASE}/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    log.info("Токен получен ✓")
    return token


def fetch_all_items(token):
    log.info("Запрашиваем список активных объявлений…")
    items, page = [], 1
    while True:
        resp = requests.get(
            f"{API_BASE}/core/v1/items",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100, "page": page, "status": "active"},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json().get("resources", [])
        if not batch:
            break
        items.extend(batch)
        log.info(f"  страница {page}: +{len(batch)} объявлений")
        if len(batch) < 100:
            break
        page += 1
        time.sleep(0.3)
    log.info(f"Всего активных объявлений: {len(items)}")
    return items


def fetch_stats(token, item_ids, days_back=30):
    log.info(f"Запрашиваем статистику за последние {days_back} дней…")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    stats_by_item = {}
    BATCH = 200
    for i in range(0, len(item_ids), BATCH):
        batch = item_ids[i:i + BATCH]
        resp = requests.post(
            f"{API_BASE}/stats/v1/accounts/{USER_ID}/items",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            json={
                "dateFrom":  date_from,
                "dateTo":    date_to,
                "fields":    ["uniqViews", "views", "contacts", "favorites"],
                "itemIds":   batch,
                "periodGrouping": "day",
            },
            timeout=60,
        )
        resp.raise_for_status()
        for row in resp.json().get("result", {}).get("items", []):
            stats_by_item[row["itemId"]] = row.get("stats", [])
        time.sleep(0.5)
        log.info(f"  батч {i}–{i+len(batch)}: ✓")
    return stats_by_item


def build_dataset(items, stats):
    today     = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    dataset = []
    for it in items:
        item_id    = it["id"]
        item_stats = stats.get(item_id, [])
        views_today = views_yesterday = 0
        views_total = contacts_total = favorites_total = 0

        for day in item_stats:
            v = day.get("views", 0)
            c = day.get("contacts", 0)
            f = day.get("favorites", 0)
            views_total     += v
            contacts_total  += c
            favorites_total += f
            d = day.get("date", "")
            if d == today:
                views_today = v
            elif d == yesterday:
                views_yesterday = v

        dataset.append({
            "id":            item_id,
            "title":         it.get("title", "—"),
            "city":          it.get("address", "—"),
            "url":           it.get("url", ""),
            "price":         it.get("price", 0),
            "viewsToday":    views_today,
            "viewsDelta":    views_today - views_yesterday,
            "views30d":      views_total,
            "contacts30d":   contacts_total,
            "favorites30d":  favorites_total,
            "status":        it.get("status", ""),
        })
    return dataset


def save_history(dataset):
    history = {}
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = {}

    today = datetime.now().strftime("%Y-%m-%d")
    history[today] = [
        {"id": r["id"], "title": r["title"], "city": r["city"],
         "views": r["viewsToday"], "contacts": r["contacts30d"]}
        for r in dataset
    ]

    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    history = {d: v for d, v in history.items() if d >= cutoff}

    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"История обновлена: {len(history)} дней")


def render_html(dataset):
    tpl = TEMPLATE_FILE.read_text(encoding="utf-8")
    data_json = json.dumps(dataset, ensure_ascii=False, indent=2)
    tpl = tpl.replace("/*__DATA__*/", data_json)
    tpl = tpl.replace("__GENERATED_AT__", datetime.now().strftime("%d.%m.%Y %H:%M"))
    return tpl


def main():
    if not all([CLIENT_ID, CLIENT_SECRET, USER_ID]):
        log.error("Не хватает секретов AVITO_CLIENT_ID / AVITO_CLIENT_SECRET / AVITO_USER_ID")
        sys.exit(1)

    token    = get_access_token()
    items    = fetch_all_items(token)
    if not items:
        log.warning("Активных объявлений нет")
        return

    item_ids = [it["id"] for it in items]
    stats    = fetch_stats(token, item_ids)
    dataset  = build_dataset(items, stats)

    save_history(dataset)

    html = render_html(dataset)
    (OUTPUT_DIR / f"avito_report_{datetime.now():%Y-%m-%d}.html").write_text(html, encoding="utf-8")
    (OUTPUT_DIR / "latest.html").write_text(html, encoding="utf-8")

    log.info(f"✅ Готово. Объявлений: {len(dataset)}")


if __name__ == "__main__":
    main()
