#!/usr/bin/env python3
"""Подготовка «тяжёлых» фото к загрузке на Wildberries (Standalone, можно копировать один файл).

Что делает:
  1. Находит все фото в вашей папке (по умолчанию /Users/zlata/Desktop/Одежда).
  2. Группирует фото по платьям: подпапка = вещь; без подпапок — сортирует по имени
     и объединяет по 2 кадра (ваш случай; можно изменить --per-item).
  3. Делает из каждого фото вертикальный JPEG 3:4 (1200x1600), sRGB, без EXIF/GPS.
  4. Гарантированно укладывает файл в лимит WB (до 10 МБ; по умолчанию цель 9.5 МБ),
     сохраняя качество сжатия не ниже 65%.
  5. Раскладывает в `<выход>/DR-0001/01.jpg …` и создаёт `report.csv`, `manifest.json`
     и заготовку `inventory.csv` для импорта в WB Ресейл Ассистент.

Официальные требования WB: JPG/PNG/WEBP, минимум 700x900 px, качество >= 65%,
до 10 МБ на файл, до 30 фото на карточку.

Запуск на macOS (один раз поставьте Pillow):
    python3 -m pip install --user Pillow
    python3 tools/prepare_photos.py
    # или дважды кликните prepare_photos.command в корне проекта

Другие варианты:
    python3 tools/prepare_photos.py --input "/Users/zlata/Desktop/Одежда" --group flat --per-item 2
    python3 tools/prepare_photos.py --max-mb 3 --size 900x1200    # ещё легче
    python3 tools/prepare_photos.py --dry-run                      # только посмотреть план

Если Pillow недоступен, на macOS используется встроенная утилита `sips`:
    python3 tools/prepare_photos.py --engine sips
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageCms, ImageOps
    HAS_PIL = True
except ImportError:
    _deps = Path(__file__).resolve().parents[1] / ".deps"
    if _deps.exists() and str(_deps) not in sys.path:
        sys.path.insert(0, str(_deps))
    try:
        from PIL import Image, ImageCms, ImageOps
        HAS_PIL = True
    except ImportError:
        HAS_PIL = False

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif"}
HEIC = {".heic", ".heif"}
QUALITY_STEPS = [88, 85, 82, 78, 74, 70, 65]
MIN_WH = (700, 900)
BG = (250, 250, 250)
DEFAULT_INPUT = "/Users/zlata/Desktop/Одежда"
DEFAULT_OUTPUT = "/Users/zlata/Desktop/Одежда_WB"


# ---------- утилиты ----------
def natural(s: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", s))


def mb(n: int | float) -> str:
    return f"{n / 1024 / 1024:.2f} МБ"


def discover(root: Path, recursive: bool = True, exclude: Path | None = None) -> list[Path]:
    out: list[Path] = []
    if recursive:
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")
                       and (exclude is None or (Path(base) / d).resolve() != exclude.resolve())]
            for f in files:
                p = Path(base) / f
                if p.suffix.lower() in EXTS and not f.startswith("."):
                    out.append(p)
    else:
        out = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in EXTS]
    return sorted(out, key=lambda p: natural(str(p.relative_to(root))))


def chunk(items: list[Path], n: int) -> list[list[Path]]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def group_photos(root: Path, strategy: str, per_item: int, prefix: str,
                 keep_names: bool, exclude: Path | None = None) -> list[dict[str, Any]]:
    """Возвращает [{code, folder_name, files:[Path]}]."""
    files = discover(root, exclude=exclude)
    if not files:
        return []
    subdirs = {p.parent for p in files if p.parent != root}
    groups: list[dict[str, Any]] = []

    def code_for(name: str, idx: int) -> str:
        if keep_names:
            slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:32]
            if slug:
                return f"{prefix}-{slug}"
        return f"{prefix}-{idx:04d}"

    if strategy in ("auto", "folders") and subdirs:
        for idx, folder in enumerate(sorted(subdirs, key=lambda p: natural(str(p))), start=1):
            group = [p for p in files if p.parent == folder]
            groups.append({"code": code_for(folder.name, idx), "folder_name": str(folder.relative_to(root)),
                           "files": sorted(group, key=lambda p: natural(p.name))})
        loose = [p for p in files if p.parent == root]
        for i, part in enumerate(chunk(loose, per_item), start=len(groups) + 1):
            groups.append({"code": code_for(f"flat-{i}", i), "folder_name": "(файлы в корне)",
                           "files": part})
        return groups

    if strategy == "prefix":
        buckets: dict[str, list[Path]] = {}
        for p in files:
            stem = re.sub(r"[-_ ]*\d+$", "", p.stem)
            key = stem if len(stem) >= 3 else "misc"
            buckets.setdefault(key, []).append(p)
        for idx, (key, group) in enumerate(sorted(buckets.items(), key=lambda kv: natural(kv[0])), start=1):
            parts = chunk(sorted(group, key=lambda p: natural(p.name)), 30)
            for part_no, part in enumerate(parts, start=1):
                suffix = f"-{part_no}" if len(parts) > 1 else ""
                groups.append({"code": code_for(key + suffix, idx), "folder_name": key, "files": part})
        return groups

    # flat
    for idx, part in enumerate(chunk(files, per_item), start=1):
        groups.append({"code": code_for(f"item-{idx}", idx), "folder_name": "(пачка)", "files": part})
    return groups


# ---------- сжатие ----------
def heic_via_sips(src: Path) -> Path | None:
    import tempfile
    tmp = Path(tempfile.mkstemp(suffix=".jpg")[1])
    try:
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "92",
                        str(src), "--out", str(tmp)], check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return tmp if tmp.stat().st_size > 0 else None
    except Exception:
        tmp.unlink(missing_ok=True)
        return None


def open_image(src: Path) -> tuple[Any, Path | None]:
    """Открывает фото (в т.ч. HEIC), возвращает PIL-изображение и временный файл."""
    temp = None
    path = src
    if src.suffix.lower() in HEIC:
        if not HAS_PIL:
            converted = heic_via_sips(src)
            if converted:
                return Image.open(converted).convert("RGB"), converted
            raise RuntimeError("HEIC не прочитан: установите Pillow + pillow-heif или используйте --engine sips")
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            converted = heic_via_sips(src)
            if converted:
                return ImageOps.exif_transpose(Image.open(converted)).convert("RGB"), converted
            raise RuntimeError("для HEIC нужен pillow-heif или macOS sips")
    im = ImageOps.exif_transpose(Image.open(path))
    try:
        if im.info.get("icc_profile"):
            src_profile = ImageCms.ImageCmsProfile(io.BytesIO(im.info["icc_profile"]))
            im = ImageCms.profileToProfile(im, src_profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
    except Exception:
        pass
    return im.convert("RGB"), temp


def compress_pillow(src: Path, dst: Path, target: tuple[int, int], max_bytes: int) -> dict[str, Any]:
    im, temp = open_image(src)
    try:
        original_size = list(im.size)
        canvas = Image.new("RGB", target, BG)
        ratio = min(target[0] / im.width, target[1] / im.height)
        new_size = (max(1, int(im.width * ratio)), max(1, int(im.height * ratio)))
        scaled = im.resize(new_size, Image.LANCZOS)
        canvas.paste(scaled, ((target[0] - new_size[0]) // 2, (target[1] - new_size[1]) // 2))
        warnings = []
        if im.width < MIN_WH[0] or im.height < MIN_WH[1]:
            warnings.append(f"исходник {im.width}x{im.height} меньше 700x900 — увеличено до 3:4")
        width, height = canvas.size
        scale = 1.0
        for _ in range(6):
            if scale < 1.0:
                canvas = canvas.resize((max(MIN_WH[0], int(width * scale)),
                                        max(MIN_WH[1], int(height * scale))), Image.LANCZOS)
            for q in QUALITY_STEPS:
                buf = io.BytesIO()
                canvas.save(buf, "JPEG", quality=q, optimize=True, progressive=True, dpi=(72, 72))
                data = buf.getvalue()
                if len(data) <= max_bytes:
                    dst.write_bytes(data)
                    return {"ok": True, "bytes": len(data), "quality": q,
                            "width": canvas.width, "height": canvas.height,
                            "original_size": original_size, "warnings": warnings}
            if canvas.width <= MIN_WH[0] or canvas.height <= MIN_WH[1]:
                break
            scale *= 0.82
        return {"ok": False, "bytes": 0, "warnings": warnings + ["не удалось уложиться в лимит"]}
    finally:
        if temp:
            temp.unlink(missing_ok=True)


def compress_sips(src: Path, dst: Path, target: tuple[int, int], max_bytes: int) -> dict[str, Any]:
    """Fallback для macOS без Pillow: sips ( HEIC тоже умеет )."""
    if not shutil.which("sips"):
        return {"ok": False, "bytes": 0, "warnings": ["sips доступен только на macOS"]}
    long_side = max(target)
    for q in (85, 80, 75, 70, 65):
        for side in (long_side, int(long_side * 0.8), 1200):
            try:
                subprocess.run([
                    "sips", "-s", "format", "jpeg", "-s", "formatOptions", str(q),
                    "--resampleHeightWidthMax", str(side), str(src), "--out", str(dst)],
                    check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                return {"ok": False, "bytes": 0, "warnings": [f"sips: {e}"]}
            if dst.exists() and dst.stat().st_size <= max_bytes:
                try:
                    with Image.open(dst) as im:  # если Pillow всё же есть
                        size = list(im.size)
                    with open(dst, "rb") as f:
                        head = f.read(2)
                    del head
                except Exception:
                    size = [side, int(side * target[1] / target[0])]
                return {"ok": True, "bytes": dst.stat().st_size, "quality": q,
                        "width": size[0], "height": size[1], "original_size": None,
                        "warnings": ["режим sips: без приведения к 3:4, WB добавит поля сам"]}
    return {"ok": False, "bytes": 0, "warnings": ["sips не смог уложиться в лимит"]}


# ---------- основной сценарий ----------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=DEFAULT_INPUT, help="папка с исходными фото")
    ap.add_argument("--output", default=DEFAULT_OUTPUT, help="куда положить готовые фото")
    ap.add_argument("--group", choices=["auto", "folders", "flat", "prefix"], default="auto",
                    help="auto: подпапки = платья, иначе пары по N фото; flat: всё в ряд; prefix: по имени файла")
    ap.add_argument("--per-item", type=int, default=2, help="сколько фото в одном платье при группировке пачками (у вас — 2)")
    ap.add_argument("--prefix", default="DR", help="префикс артикулов (DR-0001 …)")
    ap.add_argument("--keep-folder-names", action="store_true", help="использовать имена папок как артикулы")
    ap.add_argument("--max-mb", type=float, default=9.5, help="максимум МБ на файл (лимит WB — 10)")
    ap.add_argument("--size", default="1200x1600", help="размер холста 3:4, например 900x1200")
    ap.add_argument("--engine", choices=["auto", "pillow", "sips"], default="auto")
    ap.add_argument("--dry-run", action="store_true", help="только показать план, ничего не писать")
    ap.add_argument("--limit", type=int, default=0, help="обработать только первые N платьев")
    ap.add_argument("--no-recursive", action="store_true")
    args = ap.parse_args()

    root = Path(args.input).expanduser()
    out = Path(args.output).expanduser()
    if not root.exists():
        print(f"❌ Папка не найдена: {root}\n   Укажите свою: --input \"/путь/к/папке\"")
        return 1
    try:
        w, h = (int(x) for x in args.size.lower().split("x"))
        assert w >= 700 and h >= 900, "минимум 700x900"
    except Exception:
        print("❌ --size должен быть вида 1200x1600 (минимум 700x900)")
        return 1
    if args.max_mb > 10:
        print("⚠️  Лимит WB — 10 МБ, принудительно ставим 9.5 МБ с запасом")
        args.max_mb = 9.5
    max_bytes = int(args.max_mb * 1024 * 1024)
    engine = args.engine
    if engine == "auto":
        engine = "pillow" if HAS_PIL else ("sips" if shutil.which("sips") else "none")
    if engine == "none":
        print("❌ Нужен Pillow: python3 -m pip install --user Pillow\n"
              "   Либо на macOS: --engine sips")
        return 1

    files = discover(root, recursive=not args.no_recursive, exclude=out)
    if not files:
        print(f"❌ В папке {root} не найдено изображений ({', '.join(sorted(EXTS))})")
        return 1
    groups = group_photos(root, args.group, max(1, args.per_item), args.prefix,
                             args.keep_folder_names, exclude=out)
    if args.limit:
        groups = groups[:args.limit]

    total_before = sum(p.stat().st_size for p in files)
    over = [p for p in files if p.stat().st_size > max_bytes]
    print(f"📁 Вход:  {root}")
    print(f"📦 Выход: {out}")
    print(f"🔎 Найдено фото: {len(files)} ({mb(total_before)}), из них больше {args.max_mb} МБ: {len(over)}")
    print(f"👗 Платьев (групп): {len(groups)} · режим: {args.group} · движок: {engine} · холст: {w}x{h}")
    if args.dry_run:
        for g in groups[:15]:
            print(f"   {g['code']:<26} {len(g['files']):>2} фото  {mb(sum(p.stat().st_size for p in g['files']))}")
        if len(groups) > 15:
            print(f"   … и ещё {len(groups) - 15} групп")
        print("\nDRY-RUN: файлы не изменены. Уберите --dry-run для запуска.")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    total_after = 0
    max_file = 0
    failures = 0

    for gi, g in enumerate(groups, start=1):
        dest = out / g["code"]
        dest.mkdir(parents=True, exist_ok=True)
        for old in dest.glob("[0-9][0-9].jpg"):
            old.unlink()
        hashes: set[str] = set()
        kept = 0
        for p in g["files"][:30]:
            digest = hashlib.md5(p.read_bytes()).hexdigest()
            if digest in hashes:
                rows.append({"item": g["code"], "source_folder": g["folder_name"],
                             "original": str(p.relative_to(root)), "original_mb": round(p.stat().st_size/1048576, 2),
                             "output": "", "output_mb": "", "size": "", "quality": "", "status": "дубль"})
                continue
            hashes.add(digest)
            dst = dest / f"{kept + 1:02d}.jpg"
            try:
                r = compress_pillow(p, dst, (w, h), max_bytes) if engine == "pillow" \
                    else compress_sips(p, dst, (w, h), max_bytes)
            except Exception as e:
                r = {"ok": False, "bytes": 0, "warnings": [str(e)]}
            if r["ok"]:
                kept += 1
                total_after += r["bytes"]
                max_file = max(max_file, r["bytes"])
                status = "OK"
            else:
                failures += 1
                status = "ОШИБКА: " + "; ".join(r.get("warnings", []))
            rows.append({
                "item": g["code"], "source_folder": g["folder_name"],
                "original": str(p.relative_to(root)),
                "original_mb": round(p.stat().st_size / 1048576, 2),
                "output": str(dst.relative_to(out)) if r["ok"] else "",
                "output_mb": round(r["bytes"] / 1048576, 3) if r["ok"] else "",
                "size": f"{r.get('width')}x{r.get('height')}" if r["ok"] else "",
                "quality": r.get("quality", ""),
                "status": status,
            })
        (dest / "manifest.json").write_text(json.dumps({
            "code": g["code"], "source_folder": g["folder_name"], "photos": kept,
            "count": kept, "limit_bytes": max_bytes,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        summary.append({"code": g["code"], "source_folder": g["folder_name"], "photos": kept,
                        "bytes": sum(int(r["output_mb"] * 1048576) for r in rows
                                     if r["item"] == g["code"] and r["output_mb"] != "")})
        if gi % 10 == 0 or gi == len(groups):
            print(f"   обработано {gi}/{len(groups)} платьев…", flush=True)

    # отчёты
    with (out / "report.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["item"], delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    with (out / "inventory.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["vendor_code", "source_folder", "photo_count", "brand", "type", "color",
                         "material", "size", "condition", "defects", "chest", "waist", "hips",
                         "length", "sleeve", "price", "season", "style"])
        for s in summary:
            writer.writerow([s["code"], s["source_folder"], s["photos"], "", "платье", "", "",
                             "", "отличное", "", "", "", "", "", "", "", "", ""])
    (out / "manifest.json").write_text(json.dumps({
        "input": str(root), "output": str(out), "engine": engine,
        "target": [w, h], "max_bytes": max_bytes, "groups": len(groups),
        "files": len(files), "bytes_before": total_before, "bytes_after": total_after,
        "max_file_bytes": max_file, "failures": failures, "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "README.txt").write_text(
        "ГОТОВО К ЗАГРУЗКЕ НА WILDBERRIES\n"
        f"Исходники: {root}\nГотовые фото: {out}\n\n"
        "Каждая папка DR-0001, DR-0002 … — одно платье. Внутри 01.jpg, 02.jpg … —\n"
        "вертикальные кадры 3:4, sRGB JPEG, каждый файл не больше "
        f"{args.max_mb} МБ (лимит WB — 10 МБ).\n"
        "01.jpg — обложка. Минимум 2 фото на объявление, максимум 30.\n\n"
        "Дальше:\n"
        "1) Откройте report.csv — там вес «до/после» и статус каждого файла.\n"
        "2) Заполните inventory.csv (бренд, цвет, размер, замеры, цена) в Excel.\n"
        "3) Скопируйте папки DR-* в data/incoming/ проекта WB Ресейл Ассистент\n"
        "   (или укажите --output прямо в data/incoming).\n"
        "4) Импортируйте inventory.csv во вкладке «Контент и фото».\n"
        "5) Публикуйте по 20–30 объявлений в день через расширение Chrome.\n\n"
        "HEIC: если часть файлов не обработалась, установите pillow-heif\n"
        "(python3 -m pip install --user pillow-heif) или запустите --engine sips на macOS.\n",
        encoding="utf-8")

    saved = (1 - total_after / max(1, total_before)) * 100
    print("\n✅ Готово!")
    print(f"   Платьев: {len(groups)} · файлов на выходе: {sum(s['photos'] for s in summary)}")
    print(f"   Вес до: {mb(total_before)} → после: {mb(total_after)} (экономия {saved:.0f}%)")
    print(f"   Максимальный файл: {mb(max_file)} (лимит {args.max_mb} МБ) · ошибок: {failures}")
    print(f"   Отчёты: {out/'report.csv'}, {out/'inventory.csv'}, {out/'README.txt'}")
    if failures:
        print("   ⚠️ Часть файлов не обработана — смотрите колонку status в report.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
