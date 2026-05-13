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
from zoneinfo import ZoneInfo

import requests

BASE_DIR      = Path(__file__).resolve().parent
API_BASE      = "https://api.avito.ru"
OUTPUT_DIR    = BASE_DIR / "reports"
HISTORY_FILE  = BASE_DIR / "history.json"
DETAILS_CACHE = BASE_DIR / "items_cache.json"
TEMPLATE_FILE = BASE_DIR / "template.html"
CHATGPT_FILE  = OUTPUT_DIR / "chatgpt_context.md"
CITIES_FILE   = BASE_DIR / "config" / "cities.json"
REPORT_TZ     = ZoneInfo(os.environ.get("REPORT_TIMEZONE", "Asia/Novosibirsk"))
REPORT_DATE_ENV = os.environ.get("REPORT_DATE", "").strip()
REPORT_DATE_ROLLOVER_HOUR = int(os.environ.get("REPORT_DATE_ROLLOVER_HOUR", "18"))
DAYS_BACK     = 90
# Avito Stats v1 limits depth to 270 calendar days. dateFrom/dateTo are inclusive,
# so today minus 269 days gives the maximum safe request window.
TABLE_TOTAL_DAYS_BACK = 269
IMPRESSIONS_DAYS_BACK = 30
STATS_V2_MIN_INTERVAL = int(os.environ.get("AVITO_STATS_V2_INTERVAL", "120"))
STATS_V2_TIMEOUT = int(os.environ.get("AVITO_STATS_V2_TIMEOUT", "300"))
STATS_V2_MAX_ATTEMPTS = int(os.environ.get("AVITO_STATS_V2_MAX_ATTEMPTS", "3"))
STATS_V2_RETRY_DELAY = int(os.environ.get("AVITO_STATS_V2_RETRY_DELAY", "180"))

OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("avito")

LAST_STATS_V2_FINISHED_AT = 0.0

try:
    from src.enrich_avito_report import (
        build_external_context_for_report,
        build_external_context_payload_for_report,
        save_enriched_avito_rows,
    )
except Exception as e:
    build_external_context_for_report = None
    build_external_context_payload_for_report = None
    save_enriched_avito_rows = None
    EXTERNAL_CONTEXT_IMPORT_ERROR = e
else:
    EXTERNAL_CONTEXT_IMPORT_ERROR = None

def item_key(value):
    return str(value)


def now_local():
    return datetime.now(REPORT_TZ).replace(tzinfo=None)


def resolve_report_date_base():
    if not 0 <= REPORT_DATE_ROLLOVER_HOUR <= 23:
        raise ValueError("REPORT_DATE_ROLLOVER_HOUR должен быть числом от 0 до 23")
    if REPORT_DATE_ENV:
        try:
            return datetime.strptime(REPORT_DATE_ENV, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError("REPORT_DATE должен быть в формате YYYY-MM-DD") from e
    current = now_local()
    report_day = current.date()
    if current.hour < REPORT_DATE_ROLLOVER_HOUR:
        report_day = report_day - timedelta(days=1)
    return datetime.combine(report_day, datetime.min.time())


REPORT_DATE_BASE = resolve_report_date_base()


def date_str(days_delta=0):
    return (REPORT_DATE_BASE + timedelta(days=days_delta)).strftime("%Y-%m-%d")


def generated_at_str():
    return now_local().strftime("%d.%m.%Y %H:%M")


def report_date_display():
    return REPORT_DATE_BASE.strftime("%d.%m.%Y")

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
def load_known_cities():
    fallback = [
        "Новосибирск", "Барнаул", "Красноярск", "Новокузнецк",
        "Омск", "Томск", "Кемерово", "Бийск", "Якутск", "Горно-Алтайск",
    ]
    try:
        cities = json.loads(CITIES_FILE.read_text(encoding="utf-8"))
        if isinstance(cities, dict) and cities:
            return sorted(cities.keys(), key=len, reverse=True)
    except Exception as e:
        log.warning(f"Не удалось прочитать config/cities.json, используем fallback: {e}")
    return sorted(fallback, key=len, reverse=True)


KNOWN_CITIES = load_known_cities()

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
        cached = cache.get(item_id)
        if isinstance(cached, dict) and cached.get("created_at"):
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

def stats_money_to_rub(value):
    if value is None:
        return None
    try:
        return round(float(value) / 100, 2)
    except (TypeError, ValueError):
        return None

def days_on_avito(created_at_str):
    if not created_at_str:
        return None
    try:
        if isinstance(created_at_str, (int, float)):
            dt = datetime.fromtimestamp(created_at_str)
        else:
            s = str(created_at_str).replace("Z", "+00:00").split(".")[0].split("+")[0]
            dt = datetime.fromisoformat(s)
        return (REPORT_DATE_BASE - dt.replace(tzinfo=None)).days
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
def fetch_stats(token, item_ids, account, days_back=TABLE_TOTAL_DAYS_BACK):
    log.info(f"[{account['label']}] Запрашиваем статистику v1 за {days_back} дней…")
    date_to   = date_str()
    date_from = date_str(-days_back)
    uid = account["user_id"]

    stats_by_item = {}
    BATCH = 200
    for i in range(0, len(item_ids), BATCH):
        batch = item_ids[i:i + BATCH]
        batch_ok = False
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
                batch_ok = True
                break
            except requests.exceptions.Timeout as e:
                if attempt < 2:
                    wait = 10 * (attempt + 1)
                    log.warning(f"  батч {i}: таймаут, повтор через {wait}с…")
                    time.sleep(wait)
                else:
                    raise RuntimeError(f"Stats v1 батч {i}: все 3 попытки исчерпаны") from e
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status in (429, 500, 502, 503, 504) and attempt < 2:
                    wait = 10 * (attempt + 1)
                    log.warning(f"  батч {i}: HTTP {status}, повтор через {wait}с…")
                    time.sleep(wait)
                else:
                    raise
        if not batch_ok:
            raise RuntimeError(f"Stats v1 батч {i}: данные не получены")
        time.sleep(1)
    return stats_by_item

# ─────────── Stats v2 ───────────
def retry_after_seconds(resp):
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None

def wait_for_stats_v2_slot(context):
    global LAST_STATS_V2_FINISHED_AT
    if LAST_STATS_V2_FINISHED_AT <= 0:
        return
    elapsed = time.monotonic() - LAST_STATS_V2_FINISHED_AT
    wait = STATS_V2_MIN_INTERVAL - elapsed
    if wait > 0:
        wait = int(wait) + 1
        log.info(f"  Пауза {wait}с перед Stats v2 ({context})…")
        time.sleep(wait)

def post_stats_v2(token, uid, payload, context):
    global LAST_STATS_V2_FINISHED_AT
    url = f"{API_BASE}/stats/v2/accounts/{uid}/items"
    for attempt in range(1, STATS_V2_MAX_ATTEMPTS + 1):
        wait_for_stats_v2_slot(context)
        retry_wait = None
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=STATS_V2_TIMEOUT,
            )
            if resp.status_code == 429 and attempt < STATS_V2_MAX_ATTEMPTS:
                retry_wait = max(retry_after_seconds(resp) or 0, STATS_V2_RETRY_DELAY)
                log.warning(f"  Stats v2 ({context}): 429 Too Many Requests, попытка {attempt}/{STATS_V2_MAX_ATTEMPTS}, повтор через {retry_wait}с…")
            else:
                resp.raise_for_status()
                return resp.json()
        except requests.exceptions.Timeout:
            if attempt >= STATS_V2_MAX_ATTEMPTS:
                raise
            retry_wait = STATS_V2_RETRY_DELAY
            log.warning(f"  Stats v2 ({context}): таймаут {STATS_V2_TIMEOUT}с, попытка {attempt}/{STATS_V2_MAX_ATTEMPTS}, повтор через {retry_wait}с…")
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (429, 500, 502, 503, 504) and attempt < STATS_V2_MAX_ATTEMPTS:
                retry_after = retry_after_seconds(e.response) if e.response is not None else None
                retry_wait = max(retry_after or 0, STATS_V2_RETRY_DELAY)
                log.warning(f"  Stats v2 ({context}): HTTP {status}, попытка {attempt}/{STATS_V2_MAX_ATTEMPTS}, повтор через {retry_wait}с…")
            else:
                raise
        except requests.exceptions.RequestException as e:
            if attempt >= STATS_V2_MAX_ATTEMPTS:
                raise
            retry_wait = STATS_V2_RETRY_DELAY
            log.warning(f"  Stats v2 ({context}): {e}, попытка {attempt}/{STATS_V2_MAX_ATTEMPTS}, повтор через {retry_wait}с…")
        finally:
            LAST_STATS_V2_FINISHED_AT = time.monotonic()
        if retry_wait is not None:
            time.sleep(retry_wait)
            LAST_STATS_V2_FINISHED_AT = time.monotonic() - STATS_V2_MIN_INTERVAL
    raise RuntimeError(f"Stats v2 ({context}): не удалось получить данные после {STATS_V2_MAX_ATTEMPTS} попыток")

def fetch_account_totals(token, account, days_back=DAYS_BACK):
    log.info(f"[{account['label']}] Запрашиваем агрегаты v2 за {days_back} дней по всем объявлениям…")
    date_to   = date_str()
    date_from = date_str(-days_back)
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
                return datetime.fromtimestamp(timestamp, REPORT_TZ).strftime("%Y-%m-%d")
            except Exception:
                return None
        return None

    data = post_stats_v2(
        token,
        uid,
        {
            "dateFrom": date_from,
            "dateTo": date_to,
            "grouping": "day",
            "metrics": metrics,
            "limit": 1000,
            "offset": 0,
        },
        f"{account['label']} агрегаты {days_back} дней",
    )

    totals = {}
    today_totals = {}
    for group in data.get("result", {}).get("groupings", []):
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
    date_to   = date_str()
    date_from = date_str(-days_back)
    uid = account["user_id"]
    target_keys = {item_key(iid) for iid in item_ids}

    metrics = ["impressions"]

    result = {}
    limit = 1000
    offset = 0
    while True:
        try:
            data = post_stats_v2(
                token,
                uid,
                {
                    "dateFrom": date_from, "dateTo": date_to,
                    "grouping": "item",
                    "metrics": metrics, "limit": limit, "offset": offset,
                },
                f"{account['label']} детализация {days_back} дней offset {offset}",
            )
            payload = data.get("result", {})
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
        except requests.exceptions.Timeout:
            log.warning(f"  Stats v2 offset {offset}: таймаут, пропускаю")
            break
        except Exception as e:
            log.warning(f"  Stats v2 offset {offset}: {e}, пропускаю")
            break

    return result

def fetch_stats_v2_today(token, item_ids, account):
    log.info(f"[{account['label']}] Запрашиваем контакты и расходы v2 за сегодня…")
    today = date_str()
    uid = account["user_id"]
    target_keys = {item_key(iid) for iid in item_ids}
    result = {}
    limit = 1000
    offset = 0

    while True:
        try:
            data = post_stats_v2(
                token,
                uid,
                {
                    "dateFrom": today, "dateTo": today,
                    "grouping": "item",
                    "metrics": [
                        "contactsShowPhone",
                        "contactsMessenger",
                        "allSpending",
                        "averageViewCost",
                        "averageContactCost",
                    ],
                    "limit": limit, "offset": offset,
                },
                f"{account['label']} сегодня offset {offset}",
            )
            payload = data.get("result", {})
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
        except requests.exceptions.Timeout:
            log.warning(f"  Stats v2 сегодня offset {offset}: таймаут, пропускаю")
            break
        except Exception as e:
            log.warning(f"  Stats v2 сегодня offset {offset}: {e}, пропускаю")
            break

    return result

# ─────────── Сборка датасета ───────────
def build_dataset(items, stats_v1, stats_v2, stats_v2_today, promotions, cache, account, stats_v1_status="ok"):
    today      = date_str()
    yesterday  = date_str(-1)
    cutoff_90  = date_str(-DAYS_BACK)
    data_quality = "ok" if stats_v1_status == "ok" else "partial"

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
        favorites_today = favorites_yesterday = 0
        views_total = contacts_total = favorites_total = 0
        views_90d = contacts_90d = favorites_90d = 0

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
            if d >= cutoff_90:
                views_90d     += v
                contacts_90d  += c
                favorites_90d += f
            if d == today:
                views_today    = v
                contacts_today = c
                favorites_today = f
            elif d == yesterday:
                views_yesterday    = v
                contacts_yesterday = c
                favorites_yesterday = f

        # Тренд — просмотры за последние 7 дней (список, старые→новые)
        trend_7d = []
        for i in range(6, -1, -1):
            d = date_str(-i)
            trend_7d.append(days_map.get(d, {}).get("v", 0))

        spending_kopecks = v2_today.get("allSpending")
        spending_rub = round(spending_kopecks / 100, 2) if spending_kopecks is not None else 0
        average_view_cost_rub = stats_money_to_rub(v2_today.get("averageViewCost"))
        average_contact_cost_rub = stats_money_to_rub(v2_today.get("averageContactCost"))

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
            "viewsTotal":   views_total,
            "contactsTotal": contacts_total,
            "favoritesTotal": favorites_total,
            "views90d":     views_90d,
            "contacts90d":  contacts_90d,
            "contactsDelta": contacts_today - contacts_yesterday,  # дельта контактов
            "favorites90d": favorites_90d,
            "favoritesDelta": favorites_today - favorites_yesterday,
            "daysOnAvito":  days_on_avito(details.get("created_at")),
            "trend7d":      trend_7d,                              # тренд за 7 дней
            "impressions30d":               v2.get("impressions"),
            "contactsShowPhoneToday":       v2_today.get("contactsShowPhone", 0) or 0,
            "contactsMessengerToday":       v2_today.get("contactsMessenger", 0) or 0,
            "spendingRub":  spending_rub,
            "cpl":          cpl,                                   # цена лида
            "averageViewCostRub": average_view_cost_rub,
            "averageContactCostRub": average_contact_cost_rub,
            "vas":          format_promotion_services(services),
            "status": it.get("status", ""),
            "dataQuality": data_quality,
            "statsV1Status": stats_v1_status,
        })
    return dataset

# ─────────── История ───────────
def load_history():
    if not HISTORY_FILE.exists():
        return {}
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_history(dataset):
    history = load_history()
    today = date_str()
    history[today] = [
        {"id": r["id"], "account": r["account"], "title": r["title"],
         "city": r["city"], "views": r["viewsToday"], "contacts": r["contacts90d"]}
        for r in dataset
    ]
    cutoff = date_str(-90)
    history = {d: v for d, v in history.items() if d >= cutoff}
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"История обновлена: {len(history)} дней")

# ─────────── Рендер HTML ───────────
def render_html(dataset, summary, external_context=None, history=None):
    history = load_history() if history is None else history
    tpl = TEMPLATE_FILE.read_text(encoding="utf-8")
    tpl = tpl.replace("/*__DATA__*/", json.dumps(dataset, ensure_ascii=False, indent=2))
    tpl = tpl.replace("/*__SUMMARY__*/", json.dumps(summary, ensure_ascii=False, indent=2))
    tpl = tpl.replace("/*__HISTORY__*/", json.dumps(history, ensure_ascii=False, indent=2))
    tpl = tpl.replace("/*__EXTERNAL_CONTEXT__*/", json.dumps(external_context or {}, ensure_ascii=False, indent=2))
    tpl = tpl.replace("__GENERATED_AT__", generated_at_str())
    tpl = tpl.replace("__REPORT_DATE__", report_date_display())
    return tpl


MD_DATA_FIELDS = [
    "id",
    "account",
    "title",
    "titleFull",
    "city",
    "url",
    "price",
    "viewsToday",
    "viewsDelta",
    "views90d",
    "viewsTotal",
    "trend7d",
    "contactsToday",
    "contactsDelta",
    "contacts90d",
    "contactsTotal",
    "contactsShowPhoneToday",
    "contactsMessengerToday",
    "favoritesToday",
    "favoritesDelta",
    "favorites90d",
    "favoritesTotal",
    "impressions30d",
    "spendingRub",
    "cpl",
    "averageViewCostRub",
    "averageContactCostRub",
    "daysOnAvito",
    "vas",
    "status",
    "dataQuality",
    "statsV1Status",
]
MD_SUMMARY_FIELDS = [
    "account",
    "views90d",
    "contacts90d",
    "favorites90d",
    "impressions",
    "spendingRub",
    "cpl",
    "viewsToday",
    "contactsToday",
    "favoritesToday",
    "impressionsToday",
    "spendingTodayRub",
    "cplToday",
]
MD_CALENDAR_FIELDS = [
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
MD_WEATHER_FIELDS = [
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
MD_EXTERNAL_TOP_LEVEL_FIELDS = ["calendar", "weatherByCity", "missingWeatherCities", "fieldNotes"]
MD_FIELD_NOTES_FIELDS = ["calendar", "weather"]
MD_HISTORY_ROW_FIELDS = ["id", "account", "title", "city", "views", "contacts"]


def md_cell(value):
    if value is None:
        return "—"
    text = str(value).replace("\n", " ").replace("|", "\\|").strip()
    return text if text else "—"


def md_num(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return md_cell(value)
    if isinstance(value, (int, float)):
        return str(value)
    return md_cell(value)


def md_list(values):
    if values is None:
        return "—"
    if not isinstance(values, (list, tuple)):
        return md_cell(values)
    if not values:
        return "—"
    return md_cell(", ".join(md_num(value) for value in values))


def collect_dict_fields(rows):
    fields = set()
    for row in rows or []:
        if isinstance(row, dict):
            fields.update(row.keys())
    return fields


def collect_weather_fields(weather_by_city):
    fields = set()
    for row in (weather_by_city or {}).values():
        if isinstance(row, dict):
            fields.update(row.keys())
    return fields


def collect_history_row_fields(history):
    fields = set()
    for rows in (history or {}).values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                fields.update(row.keys())
    return fields


def add_missing_fields(missing, scope, actual_fields, output_fields, ignored_fields=None):
    ignored_fields = set(ignored_fields or [])
    missing_fields = sorted(set(actual_fields) - set(output_fields) - ignored_fields)
    missing.extend(f"{scope}.{field}" for field in missing_fields)


def find_md_completeness_gaps(dataset, summary, external_context, history):
    external_context = external_context or {}
    missing = []

    add_missing_fields(missing, "data", collect_dict_fields(dataset), MD_DATA_FIELDS)
    add_missing_fields(missing, "summary", collect_dict_fields(summary), MD_SUMMARY_FIELDS)

    if isinstance(external_context, dict):
        add_missing_fields(missing, "externalContext", external_context.keys(), MD_EXTERNAL_TOP_LEVEL_FIELDS)
        calendar = external_context.get("calendar") or {}
        weather_by_city = external_context.get("weatherByCity") or {}
        field_notes = external_context.get("fieldNotes") or {}
        if isinstance(calendar, dict):
            add_missing_fields(missing, "externalContext.calendar", calendar.keys(), MD_CALENDAR_FIELDS)
        if isinstance(weather_by_city, dict):
            add_missing_fields(missing, "externalContext.weatherByCity", collect_weather_fields(weather_by_city), MD_WEATHER_FIELDS)
        if isinstance(field_notes, dict):
            add_missing_fields(missing, "externalContext.fieldNotes", field_notes.keys(), MD_FIELD_NOTES_FIELDS)
    else:
        missing.append("externalContext")

    if isinstance(history, dict):
        add_missing_fields(missing, "history", collect_history_row_fields(history), MD_HISTORY_ROW_FIELDS)
    else:
        missing.append("history")

    return missing


def iter_history_rows(history):
    if not isinstance(history, dict):
        return
    for date in sorted(history.keys()):
        rows = history.get(date) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                yield date, row


def render_chatgpt_context(dataset, summary, external_context=None, history=None):
    external_context = external_context or {}
    history = load_history() if history is None else history
    lines = [
        "# Avito Analytics: данные для ChatGPT",
        "",
        f"Дата отчета: {report_date_display()}",
        f"Сгенерировано: {generated_at_str()}",
        f"Часовой пояс: {REPORT_TZ.key}",
        f"Объявлений в отчете: {len(dataset)}",
        "",
        "## Как читать данные",
        "",
        "- `views90d`, `contacts90d`, `favorites90d` — накопленные метрики за 90 дней.",
        "- `viewsToday`, `contactsToday`, `favoritesToday` — метрики за текущий день отчета.",
        "- `viewsDelta`, `contactsDelta`, `favoritesDelta` — дельта к предыдущему дню.",
        "- `impressions30d` — показы за 30 дней из Stats v2 по объявлению.",
        "- `summary.impressions` — показы за период сводки аккаунта; в HTML при фильтрах пересчитывается из `impressions30d` видимых объявлений.",
        "- `spendingRub`, `cpl` — расходы и CPL за текущий день по объявлению.",
        "- `dataQuality=partial` означает, что часть статистики за запуск была получена не полностью.",
        "",
    ]

    if summary:
        lines.extend([
            "## Сводка по аккаунтам",
            "",
            "| Аккаунт | Просмотры 90д | Контакты 90д | Избранное 90д | Показы, период отчета | Расходы 90д, ₽ | CPL 90д, ₽ | Просмотры сегодня | Контакты сегодня | Избранное сегодня | Показы сегодня | Расходы сегодня, ₽ | CPL сегодня, ₽ |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for row in summary:
            lines.append(
                "| {account} | {views90d} | {contacts90d} | {favorites90d} | {impressions} | {spendingRub} | {cpl} | {viewsToday} | {contactsToday} | {favoritesToday} | {impressionsToday} | {spendingTodayRub} | {cplToday} |".format(
                    account=md_cell(row.get("account")),
                    views90d=md_num(row.get("views90d")),
                    contacts90d=md_num(row.get("contacts90d")),
                    favorites90d=md_num(row.get("favorites90d")),
                    impressions=md_num(row.get("impressions")),
                    spendingRub=md_num(row.get("spendingRub")),
                    cpl=md_num(row.get("cpl")),
                    viewsToday=md_num(row.get("viewsToday")),
                    contactsToday=md_num(row.get("contactsToday")),
                    favoritesToday=md_num(row.get("favoritesToday")),
                    impressionsToday=md_num(row.get("impressionsToday")),
                    spendingTodayRub=md_num(row.get("spendingTodayRub")),
                    cplToday=md_num(row.get("cplToday")),
                )
            )
        lines.append("")

    calendar = external_context.get("calendar") or {}
    if calendar:
        lines.extend([
            "## Календарь дня",
            "",
            f"- Тип дня: `{md_cell(calendar.get('calendar_day_type'))}`",
            f"- День недели: {md_cell(calendar.get('weekday_name'))}",
            f"- Рабочий день: {md_cell(calendar.get('is_working_day'))}",
            f"- Выходной: {md_cell(calendar.get('is_weekend'))}",
            f"- Праздник: {md_cell(calendar.get('is_public_holiday'))}",
            f"- Название праздника: {md_cell(calendar.get('holiday_name'))}",
            f"- Длинные выходные: {md_cell(calendar.get('is_long_weekend'))}",
            f"- Предпраздничный день: {md_cell(calendar.get('is_preholiday'))}",
            f"- Межпраздничный период: {md_cell(calendar.get('is_between_holidays'))}",
            f"- Первый рабочий после праздников: {md_cell(calendar.get('is_first_workday_after_holidays'))}",
            "",
        ])

    weather_by_city = external_context.get("weatherByCity") or {}
    if weather_by_city:
        lines.extend([
            "## Погода по городам",
            "",
            "| Город | Средняя °C | Мин °C | Макс °C | Осадки мм | Дождь мм | Снег см | Ветер м/с | Порывы м/с | Код | Дождь | Снег | Холодно | Плохая погода | Хорошо для стройки |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---|",
        ])
        for city, row in weather_by_city.items():
            lines.append(
                "| {city} | {mean} | {min_temp} | {max_temp} | {precipitation} | {rain} | {snow} | {wind} | {gusts} | {code} | {is_rainy} | {is_snowy} | {is_cold_day} | {is_bad_weather} | {is_good_weather} |".format(
                    city=md_cell(city),
                    mean=md_num(row.get("temperature_2m_mean")),
                    min_temp=md_num(row.get("temperature_2m_min")),
                    max_temp=md_num(row.get("temperature_2m_max")),
                    precipitation=md_num(row.get("precipitation_sum")),
                    rain=md_num(row.get("rain_sum")),
                    snow=md_num(row.get("snowfall_sum")),
                    wind=md_num(row.get("wind_speed_10m_max")),
                    gusts=md_num(row.get("wind_gusts_10m_max")),
                    code=md_num(row.get("weather_code")),
                    is_rainy=md_cell(row.get("is_rainy")),
                    is_snowy=md_cell(row.get("is_snowy")),
                    is_cold_day=md_cell(row.get("is_cold_day")),
                    is_bad_weather=md_cell(row.get("is_bad_weather")),
                    is_good_weather=md_cell(row.get("is_good_weather_for_construction")),
                )
            )
        lines.append("")

    missing_weather = external_context.get("missingWeatherCities") or []
    field_notes = external_context.get("fieldNotes") or {}
    lines.extend([
        "## Контроль полноты внешних данных",
        "",
        f"- Города без погоды: {', '.join(md_cell(city) for city in missing_weather) if missing_weather else 'нет'}",
        f"- fieldNotes.calendar: {md_cell(field_notes.get('calendar'))}",
        f"- fieldNotes.weather: {md_cell(field_notes.get('weather'))}",
        "",
    ])

    lines.extend([
        "## Объявления",
        "",
        "| ID | Аккаунт | Тема | Название | Город | URL | Цена, ₽ | Просмотры сегодня | Δ просмотров | Просмотры 90д | Просмотры всего | Тренд 7д | Контакты сегодня | Δ контактов | Контакты 90д | Контакты всего | Телефон сегодня | Чат сегодня | Избранное сегодня | Δ избранного | Избранное 90д | Избранное всего | Показы 30д | Расходы сегодня, ₽ | CPL сегодня, ₽ | Средняя цена просмотра, ₽ | Средняя цена контакта, ₽ | Дней на Авито | Продвижение | Статус | Качество данных | Stats v1 |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|",
    ])
    for row in dataset:
        lines.append(
            "| {id} | {account} | {topic} | {title} | {city} | {url} | {price} | {views_today} | {views_delta} | {views_90d} | {views_total} | {trend_7d} | {contacts_today} | {contacts_delta} | {contacts_90d} | {contacts_total} | {phone_today} | {chat_today} | {favorites_today} | {favorites_delta} | {favorites_90d} | {favorites_total} | {impressions_30d} | {spending} | {cpl} | {avg_view_cost} | {avg_contact_cost} | {days} | {vas} | {status} | {quality} | {stats_v1_status} |".format(
                id=md_cell(row.get("id")),
                account=md_cell(row.get("account")),
                topic=md_cell(row.get("title")),
                title=md_cell(row.get("titleFull")),
                city=md_cell(row.get("city")),
                url=md_cell(row.get("url")),
                price=md_num(row.get("price")),
                views_today=md_num(row.get("viewsToday")),
                views_delta=md_num(row.get("viewsDelta")),
                views_90d=md_num(row.get("views90d")),
                views_total=md_num(row.get("viewsTotal")),
                trend_7d=md_list(row.get("trend7d")),
                contacts_today=md_num(row.get("contactsToday")),
                contacts_delta=md_num(row.get("contactsDelta")),
                contacts_90d=md_num(row.get("contacts90d")),
                contacts_total=md_num(row.get("contactsTotal")),
                phone_today=md_num(row.get("contactsShowPhoneToday")),
                chat_today=md_num(row.get("contactsMessengerToday")),
                favorites_today=md_num(row.get("favoritesToday")),
                favorites_delta=md_num(row.get("favoritesDelta")),
                favorites_90d=md_num(row.get("favorites90d")),
                favorites_total=md_num(row.get("favoritesTotal")),
                impressions_30d=md_num(row.get("impressions30d")),
                spending=md_num(row.get("spendingRub")),
                cpl=md_num(row.get("cpl")),
                avg_view_cost=md_num(row.get("averageViewCostRub")),
                avg_contact_cost=md_num(row.get("averageContactCostRub")),
                days=md_num(row.get("daysOnAvito")),
                vas=md_cell(row.get("vas")),
                status=md_cell(row.get("status")),
                quality=md_cell(row.get("dataQuality")),
                stats_v1_status=md_cell(row.get("statsV1Status")),
            )
        )

    lines.extend([
        "",
        "## История по дням",
        "",
        "| Дата | ID | Аккаунт | Тема | Город | Просмотры | Контакты |",
        "|---|---:|---|---|---|---:|---:|",
    ])
    history_rows_written = 0
    for history_date, row in iter_history_rows(history):
        lines.append(
            "| {date} | {id} | {account} | {title} | {city} | {views} | {contacts} |".format(
                date=md_cell(history_date),
                id=md_cell(row.get("id")),
                account=md_cell(row.get("account")),
                title=md_cell(row.get("title")),
                city=md_cell(row.get("city")),
                views=md_num(row.get("views")),
                contacts=md_num(row.get("contacts")),
            )
        )
        history_rows_written += 1
    if history_rows_written == 0:
        lines.append("| — | — | — | — | — | — | — |")

    lines.extend([
        "",
        "## Дополнительные файлы",
        "",
        "- `data_exports/avito_daily_enriched.csv` — накопленные строки объявлений с погодой и календарем.",
        "- `data_exports/weather_daily.csv` — накопленная погода по городам.",
        "- `data_exports/calendar_daily.csv` — накопленный календарь.",
        "",
    ])
    completeness_gaps = find_md_completeness_gaps(dataset, summary, external_context, history)
    lines.extend([
        "## Проверка полноты данных",
        "",
    ])
    if completeness_gaps:
        lines.extend([
            "- Статус: есть отсутствующие поля",
            f"- Отсутствующие поля: {', '.join(completeness_gaps)}",
        ])
    else:
        lines.extend([
            "- Статус: OK",
            "- Все поля из HTML-выгрузки отражены в MD-отчете.",
        ])
    return "\n".join(lines)


def validate_report_outputs(dataset):
    latest = OUTPUT_DIR / "latest.html"
    if not dataset:
        raise RuntimeError("Нет строк объявлений: latest.html не должен выгружаться")
    if not latest.exists() or latest.stat().st_size < 1000:
        raise RuntimeError("reports/latest.html не создан или слишком мал")
    html = latest.read_text(encoding="utf-8")
    required = ["const data", "const externalContext", "Avito Analytics"]
    missing = [marker for marker in required if marker not in html]
    if missing:
        raise RuntimeError(f"reports/latest.html не прошел проверку, нет маркеров: {', '.join(missing)}")
    if "/*__DATA__*/" in html or "__GENERATED_AT__" in html or "__REPORT_DATE__" in html:
        raise RuntimeError("reports/latest.html содержит незамененные шаблонные маркеры")
    if not CHATGPT_FILE.exists() or CHATGPT_FILE.stat().st_size < 500:
        raise RuntimeError("reports/chatgpt_context.md не создан или слишком мал")


def update_external_context(dataset):
    if not dataset:
        return {}
    if (
        build_external_context_for_report is None
        or build_external_context_payload_for_report is None
        or save_enriched_avito_rows is None
    ):
        log.warning(f"Внешний контекст не подключен: {EXTERNAL_CONTEXT_IMPORT_ERROR}")
        return {}

    report_date = date_str()
    cities = sorted({row.get("city") for row in dataset if row.get("city")})
    try:
        build_external_context_for_report(report_date, cities)
    except Exception as e:
        log.warning(f"Внешний контекст не обновлен, основной отчет продолжаем: {e}")

    rows_for_storage = [row for row in dataset if row.get("dataQuality") == "ok"]
    skipped = len(dataset) - len(rows_for_storage)
    if skipped:
        log.warning(f"Обогащенные строки с неполной статистикой не сохраняем в SQLite: {skipped}")

    try:
        save_enriched_avito_rows(rows_for_storage, report_date, ensure_context=False)
    except Exception as e:
        log.warning(f"Обогащенные Avito-строки не сохранены, основной отчет продолжаем: {e}")

    try:
        return build_external_context_payload_for_report(report_date, cities, ensure_context=False)
    except Exception as e:
        log.warning(f"Внешний контекст не добавлен в HTML, основной отчет продолжаем: {e}")
        return {}

# ─────────── Main ───────────
def main():
    if not ACCOUNTS:
        log.error("Не найдено ни одного аккаунта! Проверьте секреты AVITO_CLIENT_ID / AVITO_CLIENT_SECRET / AVITO_USER_ID")
        sys.exit(1)

    log.info(f"Аккаунтов для обработки: {len(ACCOUNTS)}")
    log.info(
        "Дата отчета: %s; сгенерировано: %s; авто-порог даты: %02d:00 %s",
        date_str(),
        generated_at_str(),
        REPORT_DATE_ROLLOVER_HOUR,
        REPORT_TZ.key,
    )
    log.info(
        "Stats v2 режим: интервал %sс, timeout %sс, попыток %s, пауза после ошибок %sс",
        STATS_V2_MIN_INTERVAL,
        STATS_V2_TIMEOUT,
        STATS_V2_MAX_ATTEMPTS,
        STATS_V2_RETRY_DELAY,
    )

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

        try:
            full_summary.append(fetch_account_totals(token, account))
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
            stats_v1_status = "ok"
        except Exception as e:
            log.warning(f"[{account['label']}] Ошибка Stats v1: {e}")
            stats_v1 = {}
            stats_v1_status = "failed"

        try:
            stats_v2 = fetch_stats_v2(token, item_ids, account)
        except Exception as e:
            log.warning(f"[{account['label']}] Ошибка Stats v2: {e}")
            stats_v2 = {}

        try:
            stats_v2_today = fetch_stats_v2_today(token, item_ids, account)
        except Exception as e:
            log.warning(f"[{account['label']}] Ошибка Stats v2 за сегодня: {e}")
            stats_v2_today = {}

        try:
            promotions = fetch_promotion_services(token, item_ids, account)
        except Exception as e:
            log.warning(f"[{account['label']}] Ошибка продвижения: {e}")
            promotions = {}

        dataset = build_dataset(
            items,
            stats_v1,
            stats_v2,
            stats_v2_today,
            promotions,
            cache,
            account,
            stats_v1_status=stats_v1_status,
        )
        full_dataset.extend(dataset)

        log.info(f"[{account['label']}] Объявлений добавлено: {len(dataset)}")

    if not full_dataset:
        log.error("Нет строк объявлений ни по одному аккаунту, отчет не будет выгружен")
        sys.exit(1)

    external_context = {}
    if full_dataset:
        save_history(full_dataset)
        external_context = update_external_context(full_dataset)

    html = render_html(full_dataset, full_summary, external_context)
    (OUTPUT_DIR / f"avito_report_{date_str()}.html").write_text(html, encoding="utf-8")
    (OUTPUT_DIR / "latest.html").write_text(html, encoding="utf-8")
    CHATGPT_FILE.write_text(render_chatgpt_context(full_dataset, full_summary, external_context), encoding="utf-8-sig")
    validate_report_outputs(full_dataset)

    log.info(f"✅ Готово. Всего объявлений: {len(full_dataset)}")

    for acc_label in [a["label"] for a in ACCOUNTS]:
        acc_data = [r for r in full_dataset if r["account"] == acc_label]
        with_v2    = sum(1 for r in acc_data if r["impressions30d"] is not None)
        with_promo = sum(1 for r in acc_data if r["vas"])
        log.info(f"  {acc_label}: {len(acc_data)} объявлений | Stats v2: {with_v2}/{len(acc_data)} | Продвижение: {with_promo}/{len(acc_data)}")


if __name__ == "__main__":
    main()
