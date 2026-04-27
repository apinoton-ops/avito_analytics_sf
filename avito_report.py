#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Avito Analytics — ежедневный отчёт для GitHub Actions.
Поддержка двух аккаунтов в одном отчёте.
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests

BASE_DIR      = Path(__file__).resolve().parent
API_BASE      = "https://api.avito.ru"
OUTPUT_DIR    = BASE_DIR / "reports"
HISTORY_FILE  = BASE_DIR / "history.json"
DETAILS_CACHE = BASE_DIR / "items_cache.json"
TEMPLATE_FILE = BASE_DIR / "template.html"
DAYS_BACK     = 90

OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("avito")

# ── Конфигурация аккаунтов ──
ACCOUNTS = []
for suffix, label in [("", "СтройФит 1"), ("_2", "СтройФит 2")]:
    cid = os.environ.get(f"AVITO_CLIENT_ID{suffix}")
    sec = os.environ.get(f"AVITO_CLIENT_SECRET{suffix}")
    uid = os.environ.get(f"AVITO_USER_ID{suffix}")
    if cid and sec and uid:
        ACCOUNTS.append({"client_id": cid, "client_secret": sec, "user_id": uid, "label": label})

# ─────────── Словарь коротких названий ───────────
TITLE_SHORT_MAP = [
    ("каменный ковёр",                    "Каменный ковёр"),
    ("каменный ковер",                    "Каменный ковёр"),
    ("покрытие из резиновой крошки",      "Наборы"),
    ("резиновая плитка",                  "Плитка"),
    ("резиновая крошка от производителя", "Крошка"),
    ("грязезащит",                        "Грязезащита"),
    ("искусственная трава",               "Газон"),
    ("газон",                             "Газон"),
    ("бетонные работы",                   "Бетон"),
]

def shorten_title(full_title):
    if not full_title:
        return "—"
    low = full_title.lower()
    for needle, short in TITLE_SHORT_MAP:
        if needle in low:
            return short
    return full_title

# ─────────── Города ───────────
KNOWN_CITIES = [
    "Новосибирск", "Барнаул", "Красноярск", "Новокузнецк",
    "Омск", "Томск", "Кемерово", "Бийск", "Якутск", "Горно-Алтайск",
]

def shorten_city(full_address):
    if not full_address:
        return "—"
    for city in KNOWN_CITIES:
        if city in full_address:
            return city
    return full_address

# ─────────── Авторизация ───────────
def get_access_token(account):
    log.info(f"[{account['label']}] Получаем access_token…")
    resp = requests.post(
        f"{API_BASE}/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     account["client_id"],
            "client_secret": account["client_secret"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    log.info(f"[{account['label']}] Токен получен ✓")
    return token

# ─────────── Список объявлений ───────────
def fetch_all_items(token, account):
    log.info(f"[{account['label']}] Запрашиваем список активных объявлений…")
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
    log.info(f"[{account['label']}] Всего: {len(items)} объявлений")
    return items

# ─────────── Кеш (даты + VAS) ───────────
def load_cache():
    if not DETAILS_CACHE.exists():
        return {}
    try:
        return json.loads(DETAILS_CACHE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"Кеш повреждён, начинаем заново: {e}")
        return {}

def save_cache(cache):
    DETAILS_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch_item_details(token, items, cache, account):
    log.info(f"[{account['label']}] Получаем детали по объявлениям (даты + VAS)…")
    new_count = 0
    uid = account["user_id"]
    for it in items:
        item_id = str(it["id"])
        if item_id in cache:
            continue
        try:
            resp = requests.get(
                f"{API_BASE}/core/v1/accounts/{uid}/items/{item_id}/",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            resp.raise_for_status()
            info = resp.json()
            cache[item_id] = {
                "created_at": info.get("start_time") or info.get("startTime") or info.get("created_at"),
                "vas":        info.get("vas", []),
            }
            new_count += 1
            time.sleep(0.2)
        except requests.HTTPError as e:
            log.warning(f"  {item_id}: HTTP {e.response.status_code}, пропускаю")
            cache[item_id] = {"created_at": None, "vas": []}
        except Exception as e:
            log.warning(f"  {item_id}: {e}, пропускаю")
            cache[item_id] = {"created_at": None, "vas": []}
    log.info(f"[{account['label']}] Обновлено записей в кеше: {new_count}")
    save_cache(cache)
    return cache

def days_on_avito(created_at_str):
    if not created_at_str:
        return None
    try:
        if isinstance(created_at_str, (int, float)):
            dt = datetime.fromtimestamp(created_at_str)
        else:
            s = str(created_at_str).replace("Z", "+00:00").split(".")[0].split("+")[0]
            dt = datetime.fromisoformat(s)
        return (datetime.now() - dt.replace(tzinfo=None)).days
    except Exception as e:
        log.warning(f"Не удалось распарсить дату '{created_at_str}': {e}")
        return None

def format_vas(vas_list):
    if not vas_list:
        return None
    labels = {
        "x2_1": "x2/1д", "x2_7": "x2/7д",
        "x5_1": "x5/1д", "x5_7": "x5/7д",
        "x10_1": "x10/1д", "x10_7": "x10/7д",
        "x15_1": "x15/1д", "x15_7": "x15/7д",
        "x20_1": "x20/1д", "x20_7": "x20/7д",
        "highlight": "Выделение", "xl": "XL",
        "premium": "Premium", "raise": "Поднятие",
    }
    parts = []
    for v in vas_list:
        slug = v if isinstance(v, str) else v.get("slug", "") if isinstance(v, dict) else ""
        parts.append(labels.get(slug, slug))
    return ", ".join(parts) if parts else None

# ─────────── Stats v1 ───────────
def fetch_stats(token, item_ids, account, days_back=DAYS_BACK):
    log.info(f"[{account['label']}] Запрашиваем статистику v1 за {days_back} дней…")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    uid = account["user_id"]

    stats_by_item = {}
    BATCH = 200
    for i in range(0, len(item_ids), BATCH):
        batch = item_ids[i:i + BATCH]
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{API_BASE}/stats/v1/accounts/{uid}/items",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={
                        "dateFrom": date_from, "dateTo": date_to,
                        "fields": ["uniqViews", "views", "contacts", "favorites"],
                        "itemIds": batch, "periodGrouping": "day",
                    },
                    timeout=180,
                )
                resp.raise_for_status()
                for row in resp.json().get("result", {}).get("items", []):
                    stats_by_item[row["itemId"]] = row.get("stats", [])
                log.info(f"  батч {i}–{i+len(batch)}: ✓")
                break
            except requests.exceptions.Timeout:
                if attempt < 2:
                    wait = 10 * (attempt + 1)
                    log.warning(f"  батч {i}: таймаут, повтор через {wait}с…")
                    time.sleep(wait)
                else:
                    log.error(f"  батч {i}: все 3 попытки исчерпаны")
        time.sleep(1)
    return stats_by_item

# ─────────── Stats v2 ───────────
def fetch_stats_v2(token, item_ids, account, days_back=DAYS_BACK):
    log.info(f"[{account['label']}] Запрашиваем статистику v2…")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    uid = account["user_id"]

    metrics = [
        "impressions", "impressionsToViewsConversion", "viewsToContactsConversion",
        "contactsShowPhone", "contactsMessenger", "clickPackages", "allSpending",
    ]

    result = {}
    BATCH = 200
    for i in range(0, len(item_ids), BATCH):
        batch = item_ids[i:i + BATCH]
        try:
            resp = requests.post(
                f"{API_BASE}/stats/v2/accounts/{uid}/items",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "dateFrom": date_from, "dateTo": date_to,
                    "itemIds": batch, "grouping": "item",
                    "metrics": metrics, "limit": len(batch), "offset": 0,
                },
                timeout=120,
            )
            resp.raise_for_status()
            for group in resp.json().get("result", {}).get("groupings", []):
                if group.get("type") != "items":
                    continue
                iid = group.get("id")
                result[iid] = {m["slug"]: m["value"] for m in group.get("metrics", [])}
            log.info(f"  Stats v2 батч {i}–{i+len(batch)}: ✓ ({len(result)} объявлений)")
        except requests.exceptions.Timeout:
            log.warning(f"  Stats v2 батч {i}: таймаут, пропускаю")
        except Exception as e:
            log.warning(f"  Stats v2 батч {i}: {e}, пропускаю")

        if i + BATCH < len(item_ids):
            log.info("  Пауза 60с (лимит Stats v2)…")
            time.sleep(60)

    return result

# ─────────── Звонки ───────────
def fetch_calls_stats(token, item_ids, account, days_back=DAYS_BACK):
    log.info(f"[{account['label']}] Запрашиваем статистику звонков…")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    uid = account["user_id"]

    calls_by_item = {}
    try:
        resp = requests.post(
            f"{API_BASE}/core/v1/accounts/{uid}/calls/stats/",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"dateFrom": date_from, "dateTo": date_to, "itemIds": item_ids},
            timeout=60,
        )
        resp.raise_for_status()
        for row in resp.json().get("result", {}).get("items", []):
            iid = row.get("itemId", 0)
            if iid == 0:
                continue
            total = answered = 0
            for day in row.get("days", []):
                total    += day.get("calls", 0)
                answered += day.get("answered", 0)
            calls_by_item[iid] = {
                "calls_total": total, "calls_answered": answered,
                "calls_missed": total - answered,
            }
        log.info(f"  Звонки получены для {len(calls_by_item)} объявлений ✓")
    except Exception as e:
        log.warning(f"Не удалось получить статистику звонков: {e}")
    return calls_by_item

# ─────────── Сборка датасета ───────────
def build_dataset(items, stats_v1, stats_v2, calls, cache, account):
    today     = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    dataset = []
    for it in items:
        item_id    = it["id"]
        item_stats = stats_v1.get(item_id, [])
        details    = cache.get(str(item_id), {})
        v2         = stats_v2.get(item_id, {})
        call_data  = calls.get(item_id, {})

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

        spending_kopecks = v2.get("allSpending")
        spending_rub = round(spending_kopecks / 100, 2) if spending_kopecks else None

        dataset.append({
            "id":           item_id,
            "account":      account["label"],   # ← метка аккаунта
            "title":        shorten_title(it.get("title", "")),
            "titleFull":    it.get("title", ""),
            "city":         shorten_city(it.get("address", "")),
            "url":          it.get("url", ""),
            "price":        it.get("price", 0),
            "viewsToday":   views_today,
            "viewsDelta":   views_today - views_yesterday,
            "views90d":     views_total,
            "contacts90d":  contacts_total,
            "favorites90d": favorites_total,
            "daysOnAvito":  days_on_avito(details.get("created_at")),
            "clickPackages":                v2.get("clickPackages"),
            "impressions":                  v2.get("impressions"),
            "impressionsToViewsConversion": v2.get("impressionsToViewsConversion"),
            "viewsToContactsConversion":    v2.get("viewsToContactsConversion"),
            "contactsShowPhone":            v2.get("contactsShowPhone"),
            "contactsMessenger":            v2.get("contactsMessenger"),
            "spendingRub":  spending_rub,
            "vas":          format_vas(details.get("vas", [])),
            "callsTotal":    call_data.get("calls_total"),
            "callsAnswered": call_data.get("calls_answered"),
            "callsMissed":   call_data.get("calls_missed"),
            "status": it.get("status", ""),
        })
    return dataset

# ─────────── История ───────────
def save_history(dataset):
    history = {}
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = {}
    today = datetime.now().strftime("%Y-%m-%d")
    history[today] = [
        {"id": r["id"], "account": r["account"], "title": r["title"],
         "city": r["city"], "views": r["viewsToday"], "contacts": r["contacts90d"]}
        for r in dataset
    ]
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    history = {d: v for d, v in history.items() if d >= cutoff}
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"История обновлена: {len(history)} дней")

# ─────────── Рендер HTML ───────────
def render_html(dataset):
    tpl = TEMPLATE_FILE.read_text(encoding="utf-8")
    tpl = tpl.replace("/*__DATA__*/", json.dumps(dataset, ensure_ascii=False, indent=2))
    tpl = tpl.replace("__GENERATED_AT__", datetime.now().strftime("%d.%m.%Y %H:%M"))
    return tpl

# ─────────── Main ───────────
def main():
    if not ACCOUNTS:
        log.error("Не найдено ни одного аккаунта! Проверьте секреты AVITO_CLIENT_ID / AVITO_CLIENT_SECRET / AVITO_USER_ID")
        sys.exit(1)

    log.info(f"Аккаунтов для обработки: {len(ACCOUNTS)}")

    cache = load_cache()
    full_dataset = []

    for account in ACCOUNTS:
        log.info(f"=== Обрабатываем: {account['label']} (user_id={account['user_id']}) ===")

        token    = get_access_token(account)
        items    = fetch_all_items(token, account)
        if not items:
            log.warning(f"[{account['label']}] Активных объявлений нет, пропускаем")
            continue

        item_ids = [it["id"] for it in items]

        cache    = fetch_item_details(token, items, cache, account)
        stats_v1 = fetch_stats(token, item_ids, account)
        stats_v2 = fetch_stats_v2(token, item_ids, account)
        calls    = fetch_calls_stats(token, item_ids, account)

        dataset  = build_dataset(items, stats_v1, stats_v2, calls, cache, account)
        full_dataset.extend(dataset)

        log.info(f"[{account['label']}] Объявлений добавлено: {len(dataset)}")

    if not full_dataset:
        log.warning("Нет данных ни по одному аккаунту")
        return

    save_history(full_dataset)

    html = render_html(full_dataset)
    (OUTPUT_DIR / f"avito_report_{datetime.now():%Y-%m-%d}.html").write_text(html, encoding="utf-8")
    (OUTPUT_DIR / "latest.html").write_text(html, encoding="utf-8")

    log.info(f"✅ Готово. Всего объявлений: {len(full_dataset)}")

    for acc_label in [a["label"] for a in ACCOUNTS]:
        acc_data = [r for r in full_dataset if r["account"] == acc_label]
        with_v2  = sum(1 for r in acc_data if r["impressions"] is not None)
        with_vas = sum(1 for r in acc_data if r["vas"])
        log.info(f"  {acc_label}: {len(acc_data)} объявлений | Stats v2: {with_v2}/{len(acc_data)} | VAS: {with_vas}/{len(acc_data)}")


if __name__ == "__main__":
    main()
