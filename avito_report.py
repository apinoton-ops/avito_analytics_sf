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
IMPRESSIONS_DAYS_BACK = 30

OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("avito")

def item_key(value):
    return str(value)

# ── Конфигурация аккаунтов ──
ACCOUNTS = []
for suffix, label in [("", "СтройФит"), ("_2", "КаучПол")]:
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
    ("набор для укладки резинового",      "Наборы"),
    ("резиновая плитка",                  "Плитка"),
    ("резиновая крошка от производителя", "Крошка"),
    ("резиновая крошка для покрытий",     "Крошка"),
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
    for attempt in range(3):
        try:
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
        except Exception as e:
            if attempt < 2:
                log.warning(f"[{account['label']}] Ошибка токена (попытка {attempt+1}/3): {e}, повтор через 5с…")
                time.sleep(5)
            else:
                raise

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

# ─────────── Кеш дат публикации ───────────
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
    log.info(f"[{account['label']}] Получаем детали по объявлениям (даты публикации)…")
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
            }
            new_count += 1
            time.sleep(0.2)
        except requests.HTTPError as e:
            log.warning(f"  {item_id}: HTTP {e.response.status_code}, пропускаю")
            cache[item_id] = {"created_at": None}
        except Exception as e:
            log.warning(f"  {item_id}: {e}, пропускаю")
            cache[item_id] = {"created_at": None}
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

def format_date_short(value):
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%d.%m")
    except Exception:
        return None

def format_promotion_services(services):
    if not services:
        return None
    parts = []
    seen = set()
    for service in services:
        if isinstance(service, str):
            name = service
            end_date = None
        elif isinstance(service, dict):
            name = service.get("name") or service.get("slug") or ""
            end_date = format_date_short(service.get("endDate") or service.get("end_date"))
        else:
            continue

        if not name:
            continue
        label = f"{name} до {end_date}" if end_date else name
        if label not in seen:
            seen.add(label)
            parts.append(label)
    return ", ".join(parts) if parts else None

def fetch_promotion_services(token, item_ids, account):
    log.info(f"[{account['label']}] Запрашиваем активные услуги продвижения…")
    promotions_by_item = {}
    BATCH = 100

    for i in range(0, len(item_ids), BATCH):
        batch = item_ids[i:i + BATCH]
        try:
            resp = requests.post(
                f"{API_BASE}/promotion/v1/items/services/get",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"itemIds": batch},
                timeout=60,
            )
            resp.raise_for_status()
            payload = resp.json()
            payload = payload.get("result", payload) if isinstance(payload, dict) else {}
            rows = payload.get("items", [])
            for row in rows:
                item_id = row.get("itemId") or row.get("item_id")
                services = row.get("services") or []
                if item_id:
                    promotions_by_item[item_key(item_id)] = services

            errors = payload.get("errors") or []
            if errors:
                log.warning(f"  продвижение батч {i}: ошибок API: {len(errors)}")
            log.info(f"  продвижение батч {i}–{i+len(batch)}: ✓")
        except Exception as e:
            log.warning(f"  продвижение батч {i}: {e}, пропускаю")
        time.sleep(0.7)

    active_count = sum(1 for services in promotions_by_item.values() if services)
    log.info(f"[{account['label']}] Активное продвижение найдено у {active_count} объявлений")
    return promotions_by_item

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
                        "fields": ["uniqViews", "uniqContacts", "uniqFavorites"],
                        "itemIds": batch, "periodGrouping": "day",
                    },
                    timeout=180,
                )
                resp.raise_for_status()
                for row in resp.json().get("result", {}).get("items", []):
                    stats_by_item[item_key(row["itemId"])] = row.get("stats", [])
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
def fetch_account_totals(token, account, days_back=DAYS_BACK):
    log.info(f"[{account['label']}] Запрашиваем агрегаты v2 за {days_back} дней по всем объявлениям…")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    uid = account["user_id"]

    metrics = ["impressions", "views", "contacts", "favorites", "allSpending"]

    def group_date(group):
        value = group.get("date") or group.get("id")
        if isinstance(value, str):
            if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
                return value[:10]
            try:
                value = int(value)
            except ValueError:
                return None
        if isinstance(value, (int, float)):
            timestamp = value / 1000 if value > 10_000_000_000 else value
            try:
                return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
            except Exception:
                return None
        return None

    resp = requests.post(
        f"{API_BASE}/stats/v2/accounts/{uid}/items",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "dateFrom": date_from,
            "dateTo": date_to,
            "grouping": "day",
            "metrics": metrics,
            "limit": 1000,
            "offset": 0,
        },
        timeout=120,
    )
    resp.raise_for_status()

    totals = {}
    today_totals = {}
    for group in resp.json().get("result", {}).get("groupings", []):
        is_today = group_date(group) == date_to
        for metric in group.get("metrics", []):
            slug = metric.get("slug")
            if not slug:
                continue
            value = metric.get("value") or 0
            totals[slug] = totals.get(slug, 0) + value
            if is_today:
                today_totals[slug] = today_totals.get(slug, 0) + value

    spending_kopecks = totals.get("allSpending")
    spending_rub = round(spending_kopecks / 100, 2) if spending_kopecks else 0
    contacts = totals.get("contacts", 0) or 0
    cpl = round(spending_rub / contacts, 2) if contacts > 0 else None
    spending_today_kopecks = today_totals.get("allSpending")
    spending_today_rub = round(spending_today_kopecks / 100, 2) if spending_today_kopecks else 0
    contacts_today = today_totals.get("contacts", 0) or 0
    cpl_today = round(spending_today_rub / contacts_today, 2) if contacts_today > 0 else None

    return {
        "account": account["label"],
        "views90d": totals.get("views", 0) or 0,
        "contacts90d": contacts,
        "favorites90d": totals.get("favorites", 0) or 0,
        "impressions": totals.get("impressions", 0) or 0,
        "spendingRub": spending_rub,
        "cpl": cpl,
        "viewsToday": today_totals.get("views", 0) or 0,
        "contactsToday": contacts_today,
        "favoritesToday": today_totals.get("favorites", 0) or 0,
        "impressionsToday": today_totals.get("impressions", 0) or 0,
        "spendingTodayRub": spending_today_rub,
        "cplToday": cpl_today,
    }

def fetch_stats_v2(token, item_ids, account, days_back=IMPRESSIONS_DAYS_BACK):
    log.info(f"[{account['label']}] Запрашиваем статистику v2 за {days_back} дней…")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    uid = account["user_id"]
    target_keys = {item_key(iid) for iid in item_ids}

    metrics = ["impressions"]

    result = {}
    limit = 1000
    offset = 0
    while True:
        try:
            resp = requests.post(
                f"{API_BASE}/stats/v2/accounts/{uid}/items",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "dateFrom": date_from, "dateTo": date_to,
                    "grouping": "item",
                    "metrics": metrics, "limit": limit, "offset": offset,
                },
                timeout=120,
            )
            resp.raise_for_status()
            payload = resp.json().get("result", {})
            groupings = payload.get("groupings", [])
            for group in payload.get("groupings", []):
                iid = group.get("id") or group.get("itemId") or group.get("item_id")
                if iid is None:
                    continue
                key = item_key(iid)
                if key in target_keys:
                    result[key] = {m["slug"]: m["value"] for m in group.get("metrics", [])}

            total_count = payload.get("dataTotalCount") or payload.get("total") or 0
            log.info(f"  Stats v2 offset {offset}: ✓ ({len(result)}/{len(target_keys)} активных объявлений найдено)")
            if len(result) >= len(target_keys):
                break
            if not groupings or len(groupings) < limit:
                break
            offset += limit
            if total_count and offset >= total_count:
                break
            log.info("  Пауза 60с (лимит Stats v2)…")
            time.sleep(60)
        except requests.exceptions.Timeout:
            log.warning(f"  Stats v2 offset {offset}: таймаут, пропускаю")
            break
        except Exception as e:
            log.warning(f"  Stats v2 offset {offset}: {e}, пропускаю")
            break

    return result

def fetch_stats_v2_today(token, item_ids, account):
    log.info(f"[{account['label']}] Запрашиваем контакты и расходы v2 за сегодня…")
    today = datetime.now().strftime("%Y-%m-%d")
    uid = account["user_id"]
    target_keys = {item_key(iid) for iid in item_ids}
    result = {}
    limit = 1000
    offset = 0

    while True:
        try:
            resp = requests.post(
                f"{API_BASE}/stats/v2/accounts/{uid}/items",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "dateFrom": today, "dateTo": today,
                    "grouping": "item",
                    "metrics": ["contactsShowPhone", "contactsMessenger", "allSpending"],
                    "limit": limit, "offset": offset,
                },
                timeout=120,
            )
            resp.raise_for_status()
            payload = resp.json().get("result", {})
            groupings = payload.get("groupings", [])
            for group in groupings:
                iid = group.get("id") or group.get("itemId") or group.get("item_id")
                if iid is None:
                    continue
                key = item_key(iid)
                if key in target_keys:
                    result[key] = {m["slug"]: m["value"] for m in group.get("metrics", [])}

            total_count = payload.get("dataTotalCount") or payload.get("total") or 0
            log.info(f"  Stats v2 сегодня offset {offset}: ✓ ({len(result)}/{len(target_keys)} активных объявлений найдено)")
            if len(result) >= len(target_keys):
                break
            if not groupings or len(groupings) < limit:
                break
            offset += limit
            if total_count and offset >= total_count:
                break
            log.info("  Пауза 60с (лимит Stats v2)…")
            time.sleep(60)
        except requests.exceptions.Timeout:
            log.warning(f"  Stats v2 сегодня offset {offset}: таймаут, пропускаю")
            break
        except Exception as e:
            log.warning(f"  Stats v2 сегодня offset {offset}: {e}, пропускаю")
            break

    return result

# ─────────── Сборка датасета ───────────
def build_dataset(items, stats_v1, stats_v2, stats_v2_today, promotions, cache, account):
    today      = datetime.now().strftime("%Y-%m-%d")
    yesterday  = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    day_before = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")

    dataset = []
    for it in items:
        item_id    = it["id"]
        key        = item_key(item_id)
        item_stats = stats_v1.get(key, [])
        details    = cache.get(str(item_id), {})
        v2         = stats_v2.get(key, {})
        v2_today   = stats_v2_today.get(key, {})
        services   = promotions.get(key, [])

        views_today = views_yesterday = 0
        contacts_today = contacts_yesterday = 0
        favorites_today = 0
        views_total = contacts_total = favorites_total = 0

        # Тренд просмотров за последние 7 дней
        days_map = {}
        for day in item_stats:
            v = day.get("uniqViews", day.get("views", 0)) or 0
            c = day.get("uniqContacts", day.get("contacts", 0)) or 0
            f = day.get("uniqFavorites", day.get("favorites", 0)) or 0
            views_total     += v
            contacts_total  += c
            favorites_total += f
            d = day.get("date", "")
            days_map[d] = {"v": v, "c": c}
            if d == today:
                views_today    = v
                contacts_today = c
                favorites_today = f
            elif d == yesterday:
                views_yesterday    = v
                contacts_yesterday = c

        # Тренд — просмотры за последние 7 дней (список, старые→новые)
        trend_7d = []
        for i in range(6, -1, -1):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            trend_7d.append(days_map.get(d, {}).get("v", 0))

        spending_kopecks = v2_today.get("allSpending")
        spending_rub = round(spending_kopecks / 100, 2) if spending_kopecks is not None else 0

        # CPL в таблице: сегодняшние расходы / сегодняшние контакты
        cpl = None
        if spending_rub is not None and contacts_today > 0:
            cpl = round(spending_rub / contacts_today, 2)

        dataset.append({
            "id":           item_id,
            "account":      account["label"],
            "title":        shorten_title(it.get("title", "")),
            "titleFull":    it.get("title", ""),
            "city":         shorten_city(it.get("address", "")),
            "url":          it.get("url", ""),
            "price":        it.get("price", 0),
            "viewsToday":   views_today,
            "contactsToday": contacts_today,
            "favoritesToday": favorites_today,
            "viewsDelta":   views_today - views_yesterday,
            "views90d":     views_total,
            "contacts90d":  contacts_total,
            "contactsDelta": contacts_today - contacts_yesterday,  # дельта контактов
            "favorites90d": favorites_total,
            "daysOnAvito":  days_on_avito(details.get("created_at")),
            "trend7d":      trend_7d,                              # тренд за 7 дней
            "impressions30d":               v2.get("impressions"),
            "contactsShowPhoneToday":       v2_today.get("contactsShowPhone"),
            "contactsMessengerToday":       v2_today.get("contactsMessenger"),
            "spendingRub":  spending_rub,
            "cpl":          cpl,                                   # цена лида
            "vas":          format_promotion_services(services),
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
def render_html(dataset, summary):
    history = {}
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = {}
    tpl = TEMPLATE_FILE.read_text(encoding="utf-8")
    tpl = tpl.replace("/*__DATA__*/", json.dumps(dataset, ensure_ascii=False, indent=2))
    tpl = tpl.replace("/*__SUMMARY__*/", json.dumps(summary, ensure_ascii=False, indent=2))
    tpl = tpl.replace("/*__HISTORY__*/", json.dumps(history, ensure_ascii=False, indent=2))
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
    full_summary = []

    for account in ACCOUNTS:
        log.info(f"=== Обрабатываем: {account['label']} (user_id={account['user_id']}) ===")
        try:
            token = get_access_token(account)
        except Exception as e:
            log.error(f"[{account['label']}] Не удалось получить токен: {e} — пропускаем аккаунт")
            continue

        totals_fetched_at = None
        try:
            full_summary.append(fetch_account_totals(token, account))
            totals_fetched_at = time.time()
        except Exception as e:
            log.warning(f"[{account['label']}] Ошибка агрегатов Stats v2: {e}")

        try:
            items = fetch_all_items(token, account)
        except Exception as e:
            log.error(f"[{account['label']}] Не удалось получить список объявлений: {e} — пропускаем аккаунт")
            continue

        if not items:
            log.warning(f"[{account['label']}] Активных объявлений нет, детализацию пропускаем")
            continue

        item_ids = [it["id"] for it in items]

        try:
            cache = fetch_item_details(token, items, cache, account)
        except Exception as e:
            log.warning(f"[{account['label']}] Ошибка получения деталей: {e}")

        try:
            stats_v1 = fetch_stats(token, item_ids, account)
        except Exception as e:
            log.warning(f"[{account['label']}] Ошибка Stats v1: {e}")
            stats_v1 = {}

        try:
            if totals_fetched_at:
                elapsed = time.time() - totals_fetched_at
                if elapsed < 60:
                    wait = int(60 - elapsed) + 1
                    log.info(f"[{account['label']}] Пауза {wait}с перед детализацией Stats v2 (лимит 1 RPM)…")
                    time.sleep(wait)
            stats_v2 = fetch_stats_v2(token, item_ids, account)
        except Exception as e:
            log.warning(f"[{account['label']}] Ошибка Stats v2: {e}")
            stats_v2 = {}

        try:
            log.info(f"[{account['label']}] Пауза 60с перед расходами за сегодня Stats v2 (лимит 1 RPM)…")
            time.sleep(60)
            stats_v2_today = fetch_stats_v2_today(token, item_ids, account)
        except Exception as e:
            log.warning(f"[{account['label']}] Ошибка Stats v2 за сегодня: {e}")
            stats_v2_today = {}

        try:
            promotions = fetch_promotion_services(token, item_ids, account)
        except Exception as e:
            log.warning(f"[{account['label']}] Ошибка продвижения: {e}")
            promotions = {}

        dataset = build_dataset(items, stats_v1, stats_v2, stats_v2_today, promotions, cache, account)
        full_dataset.extend(dataset)

        log.info(f"[{account['label']}] Объявлений добавлено: {len(dataset)}")

    if not full_dataset and not full_summary:
        log.warning("Нет данных ни по одному аккаунту")
        return

    if full_dataset:
        save_history(full_dataset)

    html = render_html(full_dataset, full_summary)
    (OUTPUT_DIR / f"avito_report_{datetime.now():%Y-%m-%d}.html").write_text(html, encoding="utf-8")
    (OUTPUT_DIR / "latest.html").write_text(html, encoding="utf-8")

    log.info(f"✅ Готово. Всего объявлений: {len(full_dataset)}")

    for acc_label in [a["label"] for a in ACCOUNTS]:
        acc_data = [r for r in full_dataset if r["account"] == acc_label]
        with_v2    = sum(1 for r in acc_data if r["impressions30d"] is not None)
        with_promo = sum(1 for r in acc_data if r["vas"])
        log.info(f"  {acc_label}: {len(acc_data)} объявлений | Stats v2: {with_v2}/{len(acc_data)} | Продвижение: {with_promo}/{len(acc_data)}")


if __name__ == "__main__":
    main()
