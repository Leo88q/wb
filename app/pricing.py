"""Рекомендации по цене и шаги снижения для «поднятия» объявлений."""
from __future__ import annotations

from typing import Any

CONDITION_FACTORS = {
    "новое с биркой": 1.25,
    "новое": 1.15,
    "отличное": 1.0,
    "хорошее": 0.82,
    "удовлетворительное": 0.6,
    "есть дефекты": 0.45,
}

BRAND_TIERS = {
    "премиум": 1.6,
    "средний": 1.15,
    "масс-маркет": 1.0,
    "no name": 0.8,
}

PREMIUM_BRANDS = {"MAX MARA", "HUGO BOSS", "ARMANI", "MICHAEL KORS", "TED BAKER", "REISS", "MASSIMO DUTTI", "COS", "SANDRO", "MAJE", "DIOR", "CHANEL", "GUCCI", "PRADA", "BURBERRY", "RALPH LAUREN", "TOMMY HILFIGER", "CALVIN KLEIN"}
MID_BRANDS = {"ZARA", "MANGO", "H&M", "BEFREE", "LIME", "LOVE REPUBLIC", "SELA", "O'STIN", "MEXX", "BENETTON", "S.OLIVER", "ESPRIT", "TOM TAILOR", "MOHITO", "BERSHKA", "STRADIVARIUS", "PULL&BEAR", "INCITY", "FAMILIA", "12 STOREEZ", "LICHI", "BEFREE", "ZARINA"}


def brand_tier(brand: str | None) -> str:
    b = (brand or "").strip().upper()
    if not b:
        return "no name"
    if any(p in b for p in PREMIUM_BRANDS):
        return "премиум"
    if any(m == b or m in b for m in MID_BRANDS):
        return "средний"
    if b in {"НОРД", "NO NAME", "БЕЗ БРЕНДА", "-"}:
        return "no name"
    return "масс-маркет"


def normalize_condition(value: str | None) -> str:
    c = (value or "").strip().lower()
    for key in CONDITION_FACTORS:
        if key in c:
            return key
    if any(w in c for w in ("пятн", "дыр", "затяж", "дефект", "потерт", "брак")):
        return "есть дефекты"
    if "удовл" in c:
        return "удовлетворительное"
    if "хор" in c:
        return "хорошее"
    if "отлич" in c or "как нов" in c:
        return "отличное"
    return "отличное"


def round_price(value: float) -> int:
    """Округление до «красивой» цены, кратной 50 ₽."""
    v = max(50, float(value))
    if v < 1000:
        return int(round(v / 10.0) * 10)
    return int(round(v / 50.0) * 50)


def suggest_price(item: dict[str, Any], market_median: float | None = None) -> dict[str, Any]:
    """Оценка цены: от рыночной медианы (если есть) или от базовой вилки."""
    condition = normalize_condition(item.get("condition"))
    tier = brand_tier(item.get("brand"))
    factor = CONDITION_FACTORS[condition] * BRAND_TIERS[tier]

    if market_median and market_median > 0:
        base = market_median * factor
        source = "медиана researched-объявлений"
    else:
        base = 1400 * factor  # базовая цена б/у платья в «отличном» состоянии, масс-маркет
        source = "базовая вилка (без данных о рынке)"

    if item.get("price"):
        try:
            listed = float(str(item["price"]).replace(" ", "").replace(",", "."))
            if listed > 0:
                # слегка учитываем уже поставленную цену, но не даём ей доминировать
                base = 0.75 * base + 0.25 * listed
        except (ValueError, TypeError):
            pass

    price = round_price(base)
    floor = round_price(price * 0.75)
    return {
        "price": price,
        "min": floor,
        "max": round_price(price * 1.3),
        "condition": condition,
        "brand_tier": tier,
        "factor": round(factor, 2),
        "source": source,
        "floor": floor,  # ниже этой цены снижать не рекомендуем
    }


def next_price(current: int, pct: float = 4.0, floor: int | None = None) -> int:
    """Следующая цена для «поднятия»: небольшое снижение пушит интересовавшихся."""
    current = int(current or 0)
    if current <= 50:
        return current
    low = int(floor) if floor is not None else 50
    if current <= low:  # ниже заданного минимума уже некуда
        return current
    new = max(round_price(float(current) * (1 - pct / 100.0)), low)
    # цена должна реально измениться, иначе редактирование может не обновить карточку
    if new >= current:
        new = max(low, current - 50)
    return max(50, min(new, current - 1))
