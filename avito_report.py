#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Avito Analytics — ежедневный отчёт для GitHub Actions.

ТЗ v2 реализовано:
№1  Сокращение заголовков по словарю
№2  Сокращение адреса до города
№3  Статистика за 90 дней
№4  Колонка "Дней на Авито" (кеш)
№5  Целевые просмотры clickPackages (Stats v2)
№6  Воронка: показы, конверсии показы→просмотры, просмотры→контакты,
    contactsShowPhone, contactsMessenger
№7  Расходы на объявление (Stats v2 spendings)
№8  VAS — активные услуги продвижения
№9  Статистика звонков: всего / отвечено / пропущено
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
CLIENT_ID     = os.environ.get("AVITO_CLIENT_ID")
CLIENT_SECRET = os.environ.get("AVITO_CLIENT_SECRET")
USER_ID       = os.environ.get("AVITO_USER_ID")

API_BASE      = "https://api.avito.ru"
OUTPUT_DIR    = BASE_DIR / "reports"
HISTORY_FILE  = BASE_DIR / "history.json"
DETAILS_CACHE = BASE_DIR / "items_cache.json"
TEMPLATE_FILE = BASE_DIR / "template.html"

DAYS_BACK = 90

OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("avito")


# ─────────── ТЗ №1: Словарь коротких названий ───────────
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


# ─────────── ТЗ №2: Сокращение адреса ───────────
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


# ─────────── Список объявлений ───────────
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


# ─────────── ТЗ №4: Кеш с датами публикации + ТЗ №8: VAS ───────────
def load_cache():
    if not DETAILS_CACHE.exists():
        return {}
    try:
        return json.loads(DETAILS_CACHE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"Кеш повреждён, начинаем заново: {e}")
        return {}

def save_cache(cache):
    DETAILS_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def fetch_item_details(token, items, cache):
    """Дата публикации + VAS (услуги продвижения). Кешируется."""
    log.info("Получаем детали по объявлениям (даты + VAS)…")
    new_count = 0
    for it in items:
        item_id = str(it["id"])
        if item_id in cache:
            continue
        try:
            resp = requests.get(
                f"{API_BASE}/core/v1/accounts/{USER_ID}/items/{item_id}/",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            resp.raise_for_status()
            info = resp.json()
            cache[item_id] = {
                "created_at": info.get("start_time") or info.get("startTime") or info.get("created_at"),
                "vas":        info.get("vas", []),   # ТЗ №8: список активных услуг
            }
            new_count += 1
            time.sleep(0.2)
        except requests.HTTPError as e:
            log.warning(f"  {item_id}: HTTP {e.response.status_code}, пропускаю")
            cache[item_id] = {"created_at": None, "vas": []}
        except Exception as e:
            log.warning(f"  {item_id}: {e}, пропускаю")
            cache[item_id] = {"created_at": None, "vas": []}

    log.info(f"Обновлено записей в кеше: {new_count}")
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
    """ТЗ №8: Формируем читаемую строку активных услуг продвижения."""
    if not vas_list:
        return None
    labels = {
        "x2_1": "x2/1д", "x2_7": "x2/7д",
        "x5_1": "x5/1д", "x5_7": "x5/7д",
        "x10_1": "x10/1д", "x10_7": "x10/7д",
        "x15_1": "x15/1д", "x15_7": "x15/7д",
        "x20_1": "x20/1д", "x20_7": "x20/7д",
        "highlight": "Выделение",
        "xl": "XL",
        "premium": "Premium",
        "raise": "Поднятие",
    }
    parts = []
    for v in vas_list:
        slug = v if isinstance(v, str) else v.get("slug", "") if isinstance(v, dict) else ""
        parts.append(labels.get(slug, slug))
    return ", ".join(parts) if parts else None


# ─────────── ТЗ №3: Статистика v1 (просмотры, контакты, избранное) ───────────
def fetch_stats(token, item_ids, days_back=DAYS_BACK):
    log.info(f"Запрашиваем статистику v1 за последние {days_back} дней…")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    stats_by_item = {}
    BATCH = 200
    for i in range(0, len(item_ids), BATCH):
        batch = item_ids[i:i + BATCH]
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{API_BASE}/stats/v1/accounts/{USER_ID}/items",
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
                    log.warning(f"  батч {i}: таймаут, повтор через {wait}с (попытка {attempt+2}/3)…")
                    time.sleep(wait)
                else:
                    log.error(f"  батч {i}: все 3 попытки исчерпаны, пропускаю")
        time.sleep(1)
    return stats_by_item


# ─────────── ТЗ №5 + №6 + №7: Stats v2 ───────────
def fetch_stats_v2(token, item_ids, days_back=DAYS_BACK):
    """
    Stats v2: clickPackages, impressions, конверсии, детали контактов, расходы.
    Лимит API: 1 запрос в минуту — делаем один запрос на все объявления.
    """
    log.info("Запрашиваем статистику v2 (показы, конверсии, расходы, звонки)…")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    # Stats v2: структура запроса по документации Swagger
    # metrics — список показателей (не fields!)
    # grouping — единственное число, тип группировки
    metrics = [
        "impressions",                  # ТЗ №6: показы
        "impressionsToViewsConversion", # ТЗ №6: конверсия показы→просмотры
        "viewsToContactsConversion",    # ТЗ №6: конверсия просмотры→контакты
        "contactsShowPhone",            # ТЗ №6: посмотрели телефон
        "contactsMessenger",            # ТЗ №6: написали в чат
        "clickPackages",                # ТЗ №5: целевые просмотры из тарифа
        "allSpending",                  # ТЗ №7: все расходы
    ]

    result = {}
    BATCH = 200
    for i in range(0, len(item_ids), BATCH):
        batch = item_ids[i:i + BATCH]
        try:
            resp = requests.post(
                f"{API_BASE}/stats/v2/accounts/{USER_ID}/items",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "dateFrom": date_from,
                    "dateTo":   date_to,
                    "itemIds":  batch,
                    "grouping": "item",   # единственное число! группировка по объявлениям
                    "metrics":  metrics,  # не fields!
                    "limit":    len(batch),
                    "offset":   0,
                },
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()

            # Логируем сырой ответ для диагностики (первый батч)
            if i == 0:
                log.info("=== PROBE Stats v2 raw response ===")
                log.info(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
                log.info("=== /PROBE ===")

            # Ответ v2: result.groupings[] — массив, каждый элемент = одно объявление
            # type="item", id=itemId, metrics=[{slug, value}, ...]
            for group in data.get("result", {}).get("groupings", []):
                if group.get("type") != "item":
                    continue
                iid = group.get("id")
                item_metrics = {}
                for m in group.get("metrics", []):
                    item_metrics[m["slug"]] = m["value"]
                result[iid] = item_metrics
            log.info(f"  Stats v2 батч {i}–{i+len(batch)}: ✓ ({len(result)} объявлений)")
        except requests.exceptions.Timeout:
            log.warning(f"  Stats v2 батч {i}: таймаут, пропускаю")
        except Exception as e:
            log.warning(f"  Stats v2 батч {i}: {e}, пропускаю")

        # Лимит API v2: 1 запрос в минуту — ждём если есть следующий батч
        if i + BATCH < len(item_ids):
            log.info("  Пауза 60с (лимит Stats v2)…")
            time.sleep(60)

    return result


# ─────────── ТЗ №9: Статистика звонков ───────────
def fetch_calls_stats(token, item_ids, days_back=DAYS_BACK):
    """Звонки по каждому объявлению: всего / отвечено / пропущено."""
    log.info("Запрашиваем статистику звонков…")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    calls_by_item = {}
    try:
        resp = requests.post(
            f"{API_BASE}/core/v1/accounts/{USER_ID}/calls/stats/",
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
                "calls_total":    total,
                "calls_answered": answered,
                "calls_missed":   total - answered,
            }
        log.info(f"  Звонки получены для {len(calls_by_item)} объявлений ✓")
    except Exception as e:
        log.warning(f"Не удалось получить статистику звонков: {e}")

    return calls_by_item


# ─────────── Сборка датасета ───────────
def build_dataset(items, stats_v1, stats_v2, calls, cache):
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

        # Расходы из v2 — в копейках, переводим в рубли
        spending_kopecks = v2.get("allSpending")
        spending_rub = round(spending_kopecks / 100, 2) if spending_kopecks else None

        dataset.append({
            "id":           item_id,
            "title":        shorten_title(it.get("title", "")),
            "titleFull":    it.get("title", ""),
            "city":         shorten_city(it.get("address", "")),
            "url":          it.get("url", ""),
            "price":        it.get("price", 0),

            # ТЗ №3
            "viewsToday":   views_today,
            "viewsDelta":   views_today - views_yesterday,
            "views90d":     views_total,
            "contacts90d":  contacts_total,
            "favorites90d": favorites_total,

            # ТЗ №4
            "daysOnAvito":  days_on_avito(details.get("created_at")),

            # ТЗ №5
            "clickPackages": v2.get("clickPackages"),

            # ТЗ №6
            "impressions":                  v2.get("impressions"),
            "impressionsToViewsConversion": v2.get("impressionsToViewsConversion"),
            "viewsToContactsConversion":    v2.get("viewsToContactsConversion"),
            "contactsShowPhone":            v2.get("contactsShowPhone"),
            "contactsMessenger":            v2.get("contactsMessenger"),

            # ТЗ №7
            "spendingRub": spending_rub,

            # ТЗ №8
            "vas": format_vas(details.get("vas", [])),

            # ТЗ №9
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
        {"id": r["id"], "title": r["title"], "city": r["city"],
         "views": r["viewsToday"], "contacts": r["contacts90d"]}
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
    if not all([CLIENT_ID, CLIENT_SECRET, USER_ID]):
        log.error("Не хватает секретов AVITO_CLIENT_ID / AVITO_CLIENT_SECRET / AVITO_USER_ID")
        sys.exit(1)

    token    = get_access_token()
    items    = fetch_all_items(token)
    if not items:
        log.warning("Активных объявлений нет")
        return

    item_ids = [it["id"] for it in items]

    # Кеш: даты публикации + VAS
    cache = load_cache()
    cache = fetch_item_details(token, items, cache)

    # Статистика v1: просмотры / контакты / избранное за 90 дней
    stats_v1 = fetch_stats(token, item_ids)

    # Статистика v2: показы, конверсии, расходы, целевые просмотры
    stats_v2 = fetch_stats_v2(token, item_ids)

    # Звонки
    calls = fetch_calls_stats(token, item_ids)

    # Сборка датасета
    dataset = build_dataset(items, stats_v1, stats_v2, calls, cache)

    save_history(dataset)

    html = render_html(dataset)
    (OUTPUT_DIR / f"avito_report_{datetime.now():%Y-%m-%d}.html").write_text(html, encoding="utf-8")
    (OUTPUT_DIR / "latest.html").write_text(html, encoding="utf-8")

    log.info(f"✅ Готово. Объявлений: {len(dataset)}")

    # Диагностика
    with_v2    = sum(1 for r in dataset if r["impressions"] is not None)
    with_calls = sum(1 for r in dataset if r["callsTotal"] is not None)
    with_vas   = sum(1 for r in dataset if r["vas"])
    log.info(f"Stats v2: {with_v2}/{len(dataset)} | Звонки: {with_calls}/{len(dataset)} | VAS: {with_vas}/{len(dataset)}")


if __name__ == "__main__":
    main()
