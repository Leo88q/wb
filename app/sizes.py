"""Размерный ряд и замеры для б/у платьев.

Никаких внешних зависимостей. Таблица ориентировочная (типовая женская
размерная сетка), реальные замеры из CSV всегда имеют приоритет.
"""
from __future__ import annotations

import math
import re
from typing import Any

# RU -> (международный, EU, US, грудь, талия, бёдра) в см
SIZE_TABLE: dict[int, dict[str, Any]] = {
    38: {"intl": "XS", "eu": 34, "us": 2, "chest": 76, "waist": 56, "hips": 82},
    40: {"intl": "XS-S", "eu": 36, "us": 4, "chest": 80, "waist": 60, "hips": 86},
    42: {"intl": "S", "eu": 38, "us": 6, "chest": 84, "waist": 64, "hips": 90},
    44: {"intl": "M", "eu": 40, "us": 8, "chest": 88, "waist": 68, "hips": 94},
    46: {"intl": "M-L", "eu": 42, "us": 10, "chest": 92, "waist": 72, "hips": 98},
    48: {"intl": "L", "eu": 44, "us": 12, "chest": 96, "waist": 76, "hips": 102},
    50: {"intl": "L-XL", "eu": 46, "us": 14, "chest": 100, "waist": 80, "hips": 106},
    52: {"intl": "XL", "eu": 48, "us": 16, "chest": 104, "waist": 84, "hips": 110},
    54: {"intl": "XXL", "eu": 50, "us": 18, "chest": 108, "waist": 88, "hips": 114},
    56: {"intl": "3XL", "eu": 52, "us": 20, "chest": 112, "waist": 92, "hips": 118},
    58: {"intl": "4XL", "eu": 54, "us": 22, "chest": 116, "waist": 96, "hips": 122},
    60: {"intl": "5XL", "eu": 56, "us": 24, "chest": 120, "waist": 100, "hips": 126},
}

LETTER_TO_RU = {
    "XXS": 38, "XS": 40, "S": 42, "S/M": 44, "M": 44, "M/L": 46,
    "L": 48, "L/XL": 50, "XL": 50, "1XL": 50, "2XL": 52, "XXL": 52,
    "3XL": 54, "4XL": 56, "5XL": 58, "6XL": 60,
}

MEASURE_FIELDS = {
    "chest": ("Обхват груди", "см"),
    "waist": ("Обхват талии", "см"),
    "hips": ("Обхват бёдер", "см"),
    "length": ("Длина изделия", "см"),
    "sleeve": ("Длина рукава", "см"),
    "shoulders": ("Ширина плеч", "см"),
    "hem": ("Ширина по подолу", "см"),
}


def normalize_size(raw: Any) -> int | None:
    """Приводит '44', 'M', '44-46', 'р.46' к российскому размеру."""
    if raw is None:
        return None
    s = str(raw).strip().upper().replace("Ё", "Е")
    if not s or s in {"-", "НЕТ", "Б/Р", "ONE SIZE", "ОДИН РАЗМЕР"}:
        return None
    key = re.sub(r"\s+", "", s)
    if key in LETTER_TO_RU:
        return LETTER_TO_RU[key]
    key = re.sub(r"^(Р|РАЗМЕР|SIZE|Р-Р)\.?", "", key)
    key = key.replace(",", ".")
    nums = [int(float(n)) for n in re.findall(r"\d+(?:\.\d+)?", key)]
    if not nums:
        for letter in ("XXL", "XXXL", "XL", "XS", "S", "M", "L"):
            if letter in key:
                return LETTER_TO_RU.get(letter, 44)
        return None
    if len(nums) >= 2:  # диапазон 44-46 -> верхняя граница
        nums = [max(nums)]
    n = nums[0]
    if 20 <= n <= 32:  # EU/US conversion guess
        n = n + 6
    if 0 < n < 38:
        n += 4  # 2XS и т.п.
    if n < 38:
        n = 38
    if n > 60:
        n = 60
    return min(SIZE_TABLE, key=lambda x: abs(x - n))


def size_row(ru: int | None) -> dict[str, Any]:
    if not ru:
        return {"ru": None}
    row = SIZE_TABLE.get(ru)
    if not row:
        closest = min(SIZE_TABLE, key=lambda x: abs(x - ru))
        row = SIZE_TABLE[closest]
    return {"ru": ru, **row}


def parse_measurements(item: dict[str, Any]) -> dict[str, float]:
    """Собирает замеры из плоских полей CSV или из JSON-строки/словаря."""
    import json

    raw = item.get("measurements")
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    result: dict[str, float] = {}
    if isinstance(raw, dict):
        for key in MEASURE_FIELDS:
            if raw.get(key) not in (None, "", "-"):
                try:
                    result[key] = float(str(raw[key]).replace(",", "."))
                except ValueError:
                    pass
    for key in MEASURE_FIELDS:
        value = item.get(key)
        if value not in (None, "", "-"):
            try:
                result[key] = float(str(value).replace(",", "."))
            except ValueError:
                pass
    return result


def recommend_by_measurements(measurements: dict[str, float]) -> int | None:
    """Подбирает ближайший российский размер по груди/талии/бёдрам."""
    keys = ("chest", "waist", "hips")
    present = [k for k in keys if k in measurements]
    if not present:
        return None
    best, best_dist = None, math.inf
    for ru, row in SIZE_TABLE.items():
        dist = sum((measurements[k] - row[k]) ** 2 for k in present)
        if dist < best_dist:
            best, best_dist = ru, dist
    return best


def size_label(ru: int | None) -> str:
    row = size_row(ru)
    if not row.get("ru"):
        return "размер не указан"
    return f"{row['ru']} ({row['intl']}, EU {row['eu']}, US {row['us']})"


def measurements_text(measurements: dict[str, float], multiline: bool = True) -> str:
    parts = []
    for key, (label, unit) in MEASURE_FIELDS.items():
        if key in measurements:
            parts.append(f"{label}: {_fmt(measurements[key])} {unit}")
    sep = "\n" if multiline else "; "
    return sep.join(parts)


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def size_block(item: dict[str, Any]) -> str:
    """Готовый блок «Размер и замеры» для описания объявления."""
    ru = normalize_size(item.get("size") or item.get("size_ru"))
    row = size_row(ru)
    meas = parse_measurements(item)
    lines: list[str] = []
    if ru:
        lines.append(
            f"Размер на бирке: {ru} российский ({row['intl']}, EU {row['eu']}). "
            f"Ориентир по типовой сетке: грудь {row['chest']} / талия {row['waist']} / бёдра {row['hips']} см."
        )
    else:
        lines.append("Размер уточняйте по замерам ниже.")
    if meas:
        lines.append("Замеры изделия (по плоской поверхности, см):\n" + measurements_text(meas))
        rec = recommend_by_measurements(meas)
        if rec:
            lines.append(f"По замерам подойдёт примерно на {rec} российский размер.")
    lines.append("Сверьте замеры со своей вещью: у б/у одежды посадка важнее цифры на бирке.")
    return "\n".join(lines)


def size_grid_markdown(sizes: list[int]) -> str:
    """Таблица размерного ряда (для себя, для Excel или для вставки в описание)."""
    rows = ["| Российский | Междунар. | EU | US | Грудь, см | Талия, см | Бёдра, см |",
            "|---|---|---|---|---|---|---|"]
    for ru in sorted({int(s) for s in sizes if s}):
        r = size_row(ru)
        rows.append(f"| {ru} | {r['intl']} | {r['eu']} | {r['us']} | {r['chest']} | {r['waist']} | {r['hips']} |")
    return "\n".join(rows)
