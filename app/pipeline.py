"""Сборка листингов: CSV + папки фото -> карточки, экспорт, демо-данные."""
from __future__ import annotations

import csv
import io
import json
import random
import re
import zipfile
from pathlib import Path
from typing import Any

from . import descriptions, photos, sizes
from .db import Database, now, today
from .pricing import round_price, suggest_price
from .research import market_median_for

# колонка -> возможные имена в CSV
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "vendor_code": ("vendor_code", "артикул", "sku", "id", "код", "номер"),
    "brand": ("brand", "бренд", "производитель", "фирма"),
    "type": ("type", "тип", "категория", "вид", "наименование"),
    "color": ("color", "цвет", "оттенок"),
    "print": ("print", "принт", "рисунок", "узор"),
    "material": ("material", "материал", "состав", "ткань"),
    "size": ("size", "размер", "size_ru", "рос размер", "р"),
    "condition": ("condition", "состояние", "степень", "grade"),
    "defects": ("defects", "дефекты", "повреждения", "нюансы", "описание дефектов"),
    "chest": ("chest", "грудь", "обхват груди", "ог"),
    "waist": ("waist", "талия", "обхват талии", "от"),
    "hips": ("hips", "бедра", "бёдра", "обхват бёдер", "об"),
    "length": ("length", "длина", "длина изделия", "длина платья"),
    "sleeve": ("sleeve", "рукав", "длина рукава"),
    "shoulders": ("shoulders", "плечи", "ширина плеч"),
    "season": ("season", "сезон"),
    "style": ("style", "стиль", "фасон", "назначение"),
    "price": ("price", "цена", "стоимость", "price_rub"),
    "source": ("source", "source_folder", "источник", "откуда", "поставщик", "папка"),
    "notes": ("notes", "комментарий", "примечание", "заметки"),
    "photos": ("photos", "фото", "папка", "photo_dir", "папка фото"),
}

MEASURE_KEYS = ("chest", "waist", "hips", "length", "sleeve", "shoulders")


def _norm(s: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]", "", s.strip().lower())


def parse_inventory_csv(text: str) -> list[dict[str, Any]]:
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return []
    mapping = {_norm(f): f for f in reader.fieldnames if f}
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(reader, start=1):
        item: dict[str, Any] = {}
        for key, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                if _norm(alias) in mapping:
                    value = (row.get(mapping[_norm(alias)]) or "").strip()
                    if value:
                        item[key] = value
                    break
        if not any(v for v in item.values()):
            continue
        item.setdefault("vendor_code", f"DR-{i:03d}")
        item.setdefault("type", "платье")
        rows.append(item)
    return rows


def _flatten_csv_text(text: str) -> str:
    return text


def build_listing(row: dict[str, Any], data_dir: Path, db: Database | None = None,
                  use_llm: bool = True, vision: bool = False,
                  market_median: float | None = None,
                  process_photos: bool = True) -> dict[str, Any]:
    vendor = str(row.get("vendor_code") or "DR-000").strip()
    item = dict(row)
    item["vendor_code"] = vendor
    item["id"] = vendor
    measurements = sizes.parse_measurements(item)
    if measurements:
        guessed = sizes.recommend_by_measurements(measurements)
        if not item.get("size") and guessed:
            item["size"] = str(guessed)
    size_ru = sizes.normalize_size(item.get("size"))
    item["size"] = str(size_ru) if size_ru else (item.get("size") or "")

    incoming = data_dir / "incoming" / vendor
    out_dir = data_dir / "out" / vendor
    photo_report: dict[str, Any] = {"warnings": []}
    if process_photos and incoming.exists():
        photo_report = photos.process_folder(incoming, out_dir)
        if vision and photo_report.get("photos"):
            ai = descriptions.describe_from_photo(out_dir / photo_report["photos"][0]["file"])
            if ai:
                for k in ("type", "color", "material", "style", "print"):
                    if not item.get(k) and ai.get(k):
                        item[k] = ai[k]
                if not item.get("condition") and ai.get("condition"):
                    item["condition"] = ai["condition"]
                if not item.get("defects") and ai.get("defects"):
                    item["defects"] = ai["defects"]
                item["ai_confidence"] = ai.get("confidence")
    photo_files = photos.list_photos(out_dir)

    price_info = suggest_price(item, market_median=market_median)
    explicit_price = str(item.get("price", "")).replace(" ", "").replace(",", ".")
    if explicit_price:
        try:  # цена из CSV — воля продавца, рекомендация остаётся в min/max
            price_info["price"] = round_price(float(explicit_price))
            price_info["min"] = min(price_info["min"], price_info["price"] * 0.8)
        except ValueError:
            pass
    generated = descriptions.generate(item, use_llm=use_llm)
    status = "ready" if photo_files and generated["description"] else "draft"
    return {
        "id": vendor,
        "vendor_code": vendor,
        "title": generated["title"],
        "description": generated["description"],
        "keywords": generated["keywords"],
        "brand": item.get("brand", ""),
        "type": item.get("type", "платье"),
        "color": item.get("color", ""),
        "print": item.get("print", ""),
        "material": item.get("material", ""),
        "size": item.get("size", ""),
        "condition": item.get("condition", ""),
        "defects": item.get("defects", ""),
        "measurements": measurements,
        "season": item.get("season", ""),
        "style": item.get("style", ""),
        "source": item.get("source", ""),
        "price": price_info["price"],
        "price_start": price_info["price"],
        "min_price": price_info["min"],
        "photos": photo_files,
        "photo_dir": str(out_dir),
        "status": status,
        "variant": 0,
        "notes": (item.get("notes", "") + " | Источник контента: " + generated["source"]).strip(" |"),
        "created_at": now(),
        "updated_at": now(),
        "photo_warnings": photo_report.get("warnings", []),
        "content_source": generated["source"],
    }


def import_inventory(db: Database, text: str, data_dir: Path, use_llm: bool = True,
                     vision: bool = False, process_photos: bool = True) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    rows = parse_inventory_csv(text)
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:  # дубли артикулов: последняя строка побеждает
        unique[str(row.get("vendor_code"))] = row
    rows = list(unique.values())
    market_median = market_median_for(data_dir / "market.csv")
    created, updated, warnings = 0, 0, []

    def build_one(row: dict[str, Any]) -> dict[str, Any]:
        return build_listing(row, data_dir, db=db, use_llm=use_llm, vision=vision,
                             market_median=market_median, process_photos=process_photos)

    # 300 вещей: параллелим фото + LLM (I/O-bound), в БД пишем последовательно
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(rows)))) as pool:
        built = list(pool.map(build_one, rows)) if rows else []

    for listing in built:
        existing = db.get_listing(listing["id"])
        if existing:
            # не затираем вручную исправленные поля и даты публикации
            for k in ("status", "published_at", "last_bump_at", "next_bump_at", "bump_count", "wb_url", "views"):
                listing[k] = existing[k]
            if not listing["photos"] and existing["photos"]:
                listing["photos"] = existing["photos"]
                listing["photo_dir"] = existing["photo_dir"]
        listing.pop("photo_warnings", None)
        listing.pop("content_source", None)
        db.upsert_listing(listing)
        if existing:
            updated += 1
        else:
            created += 1
        if not listing["photos"]:
            warnings.append(f"{listing['id']}: нет фото в data/incoming/{listing['id']}/")
    return {"rows": len(rows), "created": created, "updated": updated,
            "warnings": warnings[:20], "warnings_total": len(warnings),
            "market_median": market_median}


def reprocess_photos(db: Database, data_dir: Path, only_id: str = "") -> dict[str, Any]:
    targets = [db.get_listing(only_id)] if only_id else db.all_listings()
    done, missing = 0, 0
    for l in [t for t in targets if t]:
        src = data_dir / "incoming" / l["vendor_code"]
        dst = data_dir / "out" / l["vendor_code"]
        if not src.exists():
            missing += 1
            continue
        report = photos.process_folder(src, dst)
        db.update_listing(l["id"], {"photos": photos.list_photos(dst), "photo_dir": str(dst)})
        done += 1
    return {"processed": done, "missing_folders": missing}


def export_payload(db: Database, status: str = "") -> list[dict[str, Any]]:
    items = db.list_listings(status=status, limit=10000)
    out = []
    for l in items:
        out.append({
            "vendor_code": l["vendor_code"],
            "title": l["title"],
            "description": l["description"],
            "keywords": l["keywords"],
            "brand": l["brand"], "type": l["type"], "color": l["color"],
            "material": l["material"], "size": l["size"],
            "size_label": sizes.size_label(sizes.normalize_size(l["size"])),
            "size_grid": sizes.size_grid_markdown([sizes.normalize_size(l["size"])]),
            "measurements": l["measurements"],
            "condition": l["condition"], "defects": l["defects"],
            "price": l["price"], "min_price": l["min_price"],
            "photos": [f"data/out/{l['vendor_code']}/{p}" for p in l["photos"]],
            "status": l["status"], "wb_url": l["wb_url"],
        })
    return out


def export_csv(db: Database, status: str = "") -> str:
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL)
    w.writerow(["Артикул", "Заголовок", "Описание", "Ключевые слова", "Бренд", "Цвет",
                "Размер (RU)", "Размер (полный)", "Состояние", "Дефекты", "Грудь", "Талия",
                "Бёдра", "Длина", "Цена, ₽", "Мин. цена, ₽", "Фото (файлы)", "Статус", "Ссылка"])
    for l in export_payload(db, status):
        w.writerow([
            l["vendor_code"], l["title"], l["description"], ", ".join(l["keywords"]),
            l["brand"], l["color"], l["size"], l["size_label"], l["condition"], l["defects"],
            l["measurements"].get("chest", ""), l["measurements"].get("waist", ""),
            l["measurements"].get("hips", ""), l["measurements"].get("length", ""),
            l["price"], l["min_price"], "; ".join(l["photos"]), l["status"], l["wb_url"],
        ])
    return "\ufeff" + buf.getvalue()


def export_zip(db: Database, data_dir: Path, target: Path, status: str = "") -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("listings.json", json.dumps(export_payload(db, status), ensure_ascii=False, indent=2))
        z.writestr("listings.csv", export_csv(db, status))
        z.writestr("form-selectors.example.json", (data_dir.parent / "extension" / "selectors.example.json").read_text(encoding="utf-8")
                   if (data_dir.parent / "extension" / "selectors.example.json").exists() else "{}")
        for l in db.list_listings(status=status, limit=10000):
            folder = data_dir / "out" / l["vendor_code"]
            for p in photos.list_photos(folder):
                z.write(folder / p, f"photos/{l['vendor_code']}/{p}")
    return target


DEMO_BRANDS = ["ZARA", "MANGO", "H&M", "LIME", "BEFREE", "LOVE REPUBLIC", "SELA", "COS", "MASSIMO DUTTI", "O'STIN", "no name", "INCITY"]
DEMO_COLORS = ["чёрный", "бежевый", "синий", "красный", "зелёный", "белый", "серый", "коричневый", "розовый", "фиолетовый"]
DEMO_MATERIALS = ["вискоза", "хлопок", "полиэстер", "лён", "шёлк", "трикотаж", "атлас", "denim"]
DEMO_STYLES = ["повседневное", "офисное", "вечернее", "летнее", "демисезонное", "свадебное"]
DEMO_TYPES = ["платье миди", "платье макси", "платье-футляр", "платье-рубашка", "платье-сарафан", "платье мини"]
DEMO_CONDITIONS = ["отличное", "хорошее", "отличное", "новое с биркой", "хорошее", "удовлетворительное"]
DEMO_DEFECTS = ["", "", "", "небольшая затяжка на подоле (фото 5)", "", "лёгкая потёртость на рукаве"]


def seed_demo(db: Database, data_dir: Path, count: int = 300, with_photos: int = 12) -> dict[str, Any]:
    """Создаёт демонстрационную партию из `count` платьев (помечены is_demo=1)."""
    rnd = random.Random(42)
    from datetime import date, timedelta
    for i in range(1, count + 1):
        vendor = f"DR-{i:03d}"
        if db.get_listing(vendor):
            continue
        size = rnd.choice([40, 42, 42, 44, 44, 46, 46, 48, 50, 52])
        base = sizes.size_row(size)
        row = {
            "vendor_code": vendor,
            "brand": rnd.choice(DEMO_BRANDS),
            "type": rnd.choice(DEMO_TYPES),
            "color": rnd.choice(DEMO_COLORS),
            "material": rnd.choice(DEMO_MATERIALS),
            "size": str(size),
            "condition": rnd.choice(DEMO_CONDITIONS),
            "defects": rnd.choice(DEMO_DEFECTS),
            "chest": base["chest"] + rnd.choice([-2, 0, 0, 2]),
            "waist": base["waist"] + rnd.choice([-2, 0, 0, 2]),
            "hips": base["hips"] + rnd.choice([-2, 0, 0, 2]),
            "length": rnd.choice([92, 100, 108, 115, 120, 130]),
            "sleeve": rnd.choice([0, 18, 25, 40, 58]),
            "season": rnd.choice(["лето", "демисезон", "круглый год", "зима"]),
            "style": rnd.choice(DEMO_STYLES),
            "price": rnd.choice([900, 1200, 1500, 1900, 2300, 2700, 3200, 3900]),
            "source": "демо-гардероб",
        }
        # фото только первым N — иначе интерфейс «пустой» для остальных
        out_dir = data_dir / "out" / vendor
        if i <= with_photos:
            photos.make_demo_photos(vendor, out_dir, count=rnd.choice([4, 5, 6]))
        item = build_listing(row, data_dir, use_llm=False, process_photos=False)
        item["is_demo"] = 1
        item["photos"] = photos.list_photos(out_dir)
        item["photo_dir"] = str(out_dir)
        published = i % 3 != 0  # 2/3 «уже опубликованы»
        if published:
            days_ago = rnd.randint(1, 45)
            pub = date.today() - timedelta(days=days_ago)
            item["status"] = "published"
            item["published_at"] = pub.isoformat()
            last = pub + timedelta(days=rnd.randint(0, min(days_ago, 20)))
            item["last_bump_at"] = last.isoformat()
            item["next_bump_at"] = (last + timedelta(days=rnd.choice([7, 10, 12]))).isoformat()
            item["bump_count"] = rnd.randint(0, 3)
            item["views"] = rnd.randint(10, 400)
            item["wb_url"] = f"https://www.wildberries.ru/catalog/0/demo-{vendor.lower()}.aspx"
        elif item["photos"]:
            item["status"] = "ready"
        else:
            item["status"] = "draft"
        db.upsert_listing(item)
    return {"seeded": count, "with_photos": with_photos}
