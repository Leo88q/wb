"""Планировщик «поднятия» ресейл-объявлений.

Важно: у WB Ресейл нет публичной кнопки/API «Поднять», как на Авито.
Рабочий легальный аналог — периодическое редактирование объявления: срок
публикации (2 месяца) отсчитывается заново с момента редактирования, а
снижение цены дополнительно пушит тех, кто просил скидку. Модуль готовит ежедневную
очередь: что именно отредактировать, какой текст и какую цену поставить.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from . import descriptions
from .db import Database, today
from .pricing import next_price


def _note(kind: str, price_from: int, price_to: int) -> str:
    base = ("Откройте объявление → «Редактировать» → обновите описание"
            "(вставьте новый текст из карточки задачи) → Сохраните. "
            "Срок публикации обновится и начнёт отсчитываться заново.")
    if kind == "price_drop":
        return (f"{base} Дополнительно снизьте цену {price_from} → {price_to} ₽: "
                "WB пришлёт push всем, кто просил скидку, и карточка получит свежий импульс.")
    return base


def generate_queue(db: Database, target_date: str | None = None, per_day: int | None = None,
                   include_ready: bool = False) -> dict[str, Any]:
    """Создаёт задачи на «поднятие» для просроченных/плановых объявлений."""
    settings = db.settings()
    target = target_date or today()
    limit = int(per_day or settings["bump_per_day"])
    interval = int(settings["bump_interval_days"])
    statuses = {"published", "ready"} if include_ready else {"published"}

    candidates = [
        l for l in db.all_listings()
        if l["status"] in statuses and (not l["next_bump_at"] or l["next_bump_at"] <= target)
    ]
    # сначала самые «залежавшиеся», затем по артикулу
    candidates.sort(key=lambda l: (l["last_bump_at"] or "0000-00-00", l["vendor_code"]))

    existing = Counter(t["listing_id"] for t in db.bump_tasks(status="pending", limit=10000))
    created: list[dict[str, Any]] = []
    for l in candidates:
        if len(created) >= limit:
            break
        if existing[l["vendor_code"]] > 0:
            continue
        drop = bool(settings["bump_auto_price_drop"]) and (l["bump_count"] or 0) >= 2
        floor = l["min_price"] or int(settings["min_price"])
        new_price = next_price(int(l["price"] or 1000), float(settings["bump_price_drop_pct"]), floor) if drop else int(l["price"] or 0)
        kind = "price_drop" if drop and new_price < (l["price"] or 0) else "edit"
        variant = (l["variant"] or 0) + 1
        fresh_description = descriptions.build_description(l | {"price": new_price}, variant=variant)
        note = _note(kind, int(l["price"] or 0), new_price)
        task_id = db.add_bump_task(l["vendor_code"], target, kind, new_price, fresh_description, note)
        created.append({"task_id": task_id, "vendor_code": l["vendor_code"], "title": l["title"],
                        "kind": kind, "price_from": l["price"], "price_to": new_price})
    return {
        "date": target,
        "created": len(created),
        "candidates": len(candidates),
        "limit": limit,
        "interval_days": interval,
        "items": created,
        "hint": "Отметьте задачу выполненной после сохранения изменений в объявлении на WB.",
    }


def calendar(db: Database, days: int = 21) -> list[dict[str, Any]]:
    """Сколько объявлений по графику нужно обновлять в ближайшие дни."""
    start = date.today()
    counts: dict[str, int] = {}
    for l in db.all_listings():
        if l["status"] != "published":
            continue
        raw = l["next_bump_at"]
        try:
            d = date.fromisoformat(raw) if raw else start
        except ValueError:
            d = start
        if d < start:
            d = start
        key = d.isoformat()
        counts[key] = counts.get(key, 0) + 1
    tasks = db.bump_tasks(status="pending", limit=10000)
    for t in tasks:
        counts[t["due_at"]] = counts.get(t["due_at"], 0)
    result = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        result.append({"date": d, "planned": counts.get(d, 0)})
    return result


def complete(db: Database, task_id: int, apply_price: bool = True,
             apply_description: bool = True, custom_price: int | None = None) -> dict[str, Any] | None:
    task = next((t for t in db.bump_tasks(limit=10000) if t["id"] == task_id), None)
    if not task:
        return None
    settings = db.settings()
    price = custom_price if custom_price is not None else (task["suggested_price"] if apply_price and task["kind"] == "price_drop" else None)
    desc = task["suggested_description"] if apply_description else None
    result = db.complete_bump_task(task_id, int(settings["bump_interval_days"]), price, desc)
    return result
