"""Исследование (ресерч) рынка б/у платьев по выгрузке конкурентов.

Никакого парсинга WB внутри проекта: вы загружаете CSV с объявлениями
(вручную, из выгрузки сервиса аналитики или из легального парсера), а модуль
считает вилку цен, медиану, частотные слова и рекомендованную цену.
"""
from __future__ import annotations

import csv
import io
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

STOPWORDS = set(
    "и в на с по для из а не или же бы р р-р размер платье платья женское женский "
    "б/у бу новый новое состояние отличное хорошее шт шт. см см. товар цена "
    "wildberries wb ярмарке avito авито".split()
)

PRICE_KEYS = ("price", "цена", "стоимость", "price_rub", "current_price", "sale_price")
TITLE_KEYS = ("name", "title", "название", "наименование", "заголовок", "product_name")
COND_KEYS = ("condition", "состояние", "grade", "state")


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str:
    normalized = {re.sub(r"[^a-zа-яё0-9]", "", k.lower()): v for k, v in row.items() if k}
    for key in keys:
        k = re.sub(r"[^a-zа-яё0-9]", "", key.lower())
        if k in normalized:
            return (normalized[k] or "").strip()
    return ""


def _to_float(value: str) -> float | None:
    v = re.sub(r"[^\d.,]", "", value or "")
    if not v:
        return None
    v = v.replace("\u00a0", "").replace(" ", "")
    if "," in v and "." in v:
        v = v.replace(",", "")
    v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def parse_market_csv(text: str) -> list[dict[str, Any]]:
    """Читает CSV конкурентов с гибкими названиями колонок."""
    if text.startswith("\ufeff"):
        text = text[1:]
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    items = []
    for row in reader:
        price = _to_float(_pick(row, PRICE_KEYS))
        title = _pick(row, TITLE_KEYS)
        if not title and price is None:
            continue
        items.append({
            "title": title,
            "price": price,
            "condition": _pick(row, COND_KEYS),
            "url": _pick(row, ("url", "ссылка", "link")),
            "raw": {k: v for k, v in row.items() if k},
        })
    return items


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def analyze(items: list[dict[str, Any]], query: str = "") -> dict[str, Any]:
    prices = [i["price"] for i in items if i.get("price") and i["price"] > 0]
    words: Counter[str] = Counter()
    for i in items:
        for w in re.findall(r"[а-яёa-z]{3,}", (i.get("title") or "").lower()):
            if w not in STOPWORDS:
                words[w] += 1
    by_condition: dict[str, list[float]] = {}
    for i in items:
        if i.get("price"):
            key = (i.get("condition") or "не указано").strip().lower()[:40]
            by_condition.setdefault(key, []).append(float(i["price"]))

    filtered = items
    if query:
        q = query.lower()
        filtered = [i for i in items if q in (i.get("title") or "").lower()]
    f_prices = [i["price"] for i in filtered if i.get("price") and i["price"] > 0]

    return {
        "total": len(items),
        "with_price": len(prices),
        "min": round(min(prices)) if prices else None,
        "p25": round(_percentile(prices, 0.25)) if prices else None,
        "median": round(statistics.median(prices)) if prices else None,
        "p75": round(_percentile(prices, 0.75)) if prices else None,
        "max": round(max(prices)) if prices else None,
        "mean": round(statistics.mean(prices)) if prices else None,
        "top_words": [{"word": w, "count": c} for w, c in words.most_common(25)],
        "by_condition": {
            k: {"count": len(v), "median": round(statistics.median(v))}
            for k, v in by_condition.items()
        },
        "query": query,
        "query_count": len(filtered),
        "query_median": round(statistics.median(f_prices)) if f_prices else None,
        "query_p25": round(_percentile(f_prices, 0.25)) if f_prices else None,
        "query_p75": round(_percentile(f_prices, 0.75)) if f_prices else None,
        "sample": [
            {"title": i.get("title"), "price": i.get("price"), "condition": i.get("condition"), "url": i.get("url")}
            for i in sorted([x for x in filtered if x.get("price")], key=lambda x: x["price"])[:10]
        ],
    }


def save_market_csv(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["title", "price", "condition", "url"])
        for i in items:
            w.writerow([i.get("title", ""), i.get("price", ""), i.get("condition", ""), i.get("url", "")])


def load_market_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return parse_market_csv(path.read_text(encoding="utf-8-sig"))


def market_median_for(path: Path, query: str = "") -> float | None:
    items = load_market_csv(path)
    if not items:
        return None
    result = analyze(items, query=query)
    return result.get("query_median") or result.get("median")
