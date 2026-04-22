#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Avito Analytics — ежедневный отчёт для GitHub Actions.

Реализовано:
- Сокращение заголовков по словарю
- Сокращение адреса до города
- Статистика за 90 дней
- Колонка "Дней на Авито" (через getItemInfo, с кешем)
- Попытка достать лимит контактов (анализируем все поля ответа)
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
DETAILS_CACHE = BASE_DIR / "items_cache.json"   # кеш дат публикации
TEMPLATE_FILE = BASE_DIR / "template.html"

DAYS_BACK = 90   # окно статистики — 90 дней

OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("avito")


# ─────────── Словарь коротких названий (ТЗ №1) ───────────
# Ключ — подстрока в оригинальном заголовке (в нижнем регистре).
# Порядок важен: проверяем по первому совпадению.
TITLE_SHORT_MAP = [
    ("каменный ковёр",                 "Каменный ковёр"),
    ("каменный ковер",                 "Каменный ковёр"),   # на случай без ё
    ("покрытие из резиновой крошки",   "Наборы"),
    ("резиновая плитка",               "Плитка"),
    ("резиновая крошка от производителя", "Крошка"),
    ("грязезащит",                     "Грязезащита"),
    ("искусственная трава",            "Газон"),
    ("газон",                          "Газон"),
    ("бетонные работы",                "Бетон"),
]


def shorten_title(full_title):
    """Сокращает заголовок по словарю. Если не нашли — оставляем как есть."""
    if not full_title:
        return "—"
    low = full_title.lower()
    for needle, short in TITLE_SHORT_MAP:
        if needle in low:
            return short
    return full_title


# ─────────── Известные города (ТЗ №2) ───────────
KNOWN_CITIES = [
    "Новосибирск", "Барнаул", "Красноярск", "Новокузнецк",
    "Омск", "Томск", "Кемерово", "Бийск",
    "Якутск", "Горно-Алтайск",
]


def shorten_city(full_address):
    """Извлекает название города из полного адреса. Если ничего не нашли — возвращаем оригинал."""
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


# ─────────── Детальная информация (для дат публикации и лимита контактов) ───────────
def load_cache():
    """Читаем кеш с датами публикации. Формат: {item_id: {...}}."""
    if not DETAILS_CACHE.exists():
        return {}
    try:
        return json.loads(DETAILS_CACHE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"Кеш items_cache.json повреждён, начинаем заново: {e}")
        return {}


def save_cache(cache):
    DETAILS_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_item_details(token, items, cache):
    """
    Забирает расширенную инфу по каждому объявлению:
    - дату создания/публикации (для "дней на Авито")
    - пытается найти поле "лимит контактов"
    Использует кеш — уже известные объявления не запрашивает повторно.
    """
    log.info("Получаем детали по объявлениям (для дат и лимита контактов)…")

    new_count = 0
    probe_logged = False  # флаг, чтобы один раз залогировать все доступные поля

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

            # Единоразово логируем все поля первого ответа — чтобы понять, есть ли
            # в API поле "лимит контактов" и как оно называется
            if not probe_logged:
                log.info("=== PROBE: все поля ответа getItemInfo ===")
                log.info(json.dumps(info, ensure_ascii=False, indent=2)[:2000])
                log.info("=== /PROBE ===")
                probe_logged = True

            # Сохраняем интересующие нас поля в кеш
            cache[item_id] = {
                "created_at":     info.get("start_time") or info.get("startTime") or info.get("created_at"),
                "url":            info.get("url"),
                "contacts_limit": _extract_contacts_limit(info),
                "_raw_keys":      list(info.keys()),
            }
            new_count += 1
            time.sleep(0.2)
        except requests.HTTPError as e:
            log.warning(f"  {item_id}: HTTP {e.response.status_code}, пропускаю")
            cache[item_id] = {"created_at": None, "url": None, "contacts_limit": None}
        except Exception as e:
            log.warning(f"  {item_id}: {e}, пропускаю")
            cache[item_id] = {"created_at": None, "url": None, "contacts_limit": None}

    log.info(f"Обновлено записей в кеше: {new_count}")
    save_cache(cache)
    return cache


def _extract_contacts_limit(info):
    """
    Ищем поле с лимитом контактов в ответе API.
    Пробуем разные возможные имена поля.
    """
    candidates = [
        "contacts_limit", "contactsLimit",
        "contacts_balance", "contactsBalance",
        "contact_limit", "contactLimit",
        "limit", "contacts_left",
    ]
    for key in candidates:
        if key in info:
            return info[key]

    # Возможно, вложено в объект "package" или "vas"
    for container in ("package", "vas", "services", "tariff"):
        if container in info and isinstance(info[container], dict):
            for key in candidates:
                if key in info[container]:
                    return info[container][key]
    return None


def days_on_avito(created_at_str):
    """Считаем, сколько дней назад опубликовано объявление."""
    if not created_at_str:
        return None
    # Формат в API Авито обычно ISO 8601, но может быть и unix-timestamp
    try:
        if isinstance(created_at_str, (int, float)):
            dt = datetime.fromtimestamp(created_at_str)
        else:
            # Срезаем миллисекунды/таймзоны если есть
            s = str(created_at_str).replace("Z", "+00:00").split(".")[0].split("+")[0]
            dt = datetime.fromisoformat(s)
        return (datetime.now() - dt.replace(tzinfo=None)).days
    except Exception as e:
        log.warning(f"Не удалось распарсить дату '{created_at_str}': {e}")
        return None


# ─────────── Статистика ───────────
def fetch_stats(token, item_ids, days_back=DAYS_BACK):
    log.info(f"Запрашиваем статистику за последние {days_back} дней…")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    stats_by_item = {}
    BATCH = 200
    for i in range(0, len(item_ids), BATCH):
        batch = item_ids[i:i + BATCH]

        # Пробуем до 3 раз — статистика за 90 дней может отвечать медленно
        for attempt in range(3):
            try:
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
                    timeout=180,  # 3 минуты — за 90 дней API отвечает медленно
                )
                resp.raise_for_status()
                for row in resp.json().get("result", {}).get("items", []):
                    stats_by_item[row["itemId"]] = row.get("stats", [])
                log.info(f"  батч {i}–{i+len(batch)}: ✓")
                break  # успех — выходим из retry-цикла
            except requests.exceptions.Timeout:
                if attempt < 2:
                    wait = 10 * (attempt + 1)
                    log.warning(f"  батч {i}: таймаут, повтор через {wait}с (попытка {attempt+2}/3)…")
                    time.sleep(wait)
                else:
                    log.error(f"  батч {i}: все 3 попытки исчерпаны, пропускаю батч")
        time.sleep(1)  # пауза между батчами увеличена до 1с
    return stats_by_item


# ─────────── Сборка данных ───────────
def build_dataset(items, stats, cache):
    today     = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    dataset = []
    for it in items:
        item_id    = it["id"]
        item_stats = stats.get(item_id, [])
        details    = cache.get(str(item_id), {})

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
            "id":             item_id,
            "title":          shorten_title(it.get("title", "")),   # ТЗ №1
            "titleFull":      it.get("title", ""),                   # полный для подсказки
            "city":           shorten_city(it.get("address", "")),   # ТЗ №2
            "url":            it.get("url", ""),
            "price":          it.get("price", 0),
            "viewsToday":     views_today,
            "viewsDelta":     views_today - views_yesterday,
            "views90d":       views_total,                            # ТЗ №3
            "contacts90d":    contacts_total,
            "favorites90d":   favorites_total,
            "daysOnAvito":    days_on_avito(details.get("created_at")),  # ТЗ №4
            "contactsLimit":  details.get("contacts_limit"),             # ТЗ №5
            "status":         it.get("status", ""),
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

    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"История обновлена: {len(history)} дней")


# ─────────── Рендер ───────────
def render_html(dataset):
    tpl = TEMPLATE_FILE.read_text(encoding="utf-8")
    data_json = json.dumps(dataset, ensure_ascii=False, indent=2)
    tpl = tpl.replace("/*__DATA__*/", data_json)
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

    cache    = load_cache()
    cache    = fetch_item_details(token, items, cache)

    item_ids = [it["id"] for it in items]
    stats    = fetch_stats(token, item_ids)
    dataset  = build_dataset(items, stats, cache)

    save_history(dataset)

    html = render_html(dataset)
    (OUTPUT_DIR / f"avito_report_{datetime.now():%Y-%m-%d}.html").write_text(html, encoding="utf-8")
    (OUTPUT_DIR / "latest.html").write_text(html, encoding="utf-8")

    log.info(f"✅ Готово. Объявлений: {len(dataset)}")

    # Итоговый отчёт по полю "лимит контактов"
    with_limit = sum(1 for r in dataset if r["contactsLimit"] is not None)
    log.info(f"Лимит контактов найден у {with_limit} из {len(dataset)} объявлений")


if __name__ == "__main__":
    main()
