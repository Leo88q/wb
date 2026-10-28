"""Пайплайн фотографий: папка на платье -> готовые к загрузке кадры 3:4.

Требования WB (официальная инструкция «Фото товаров»):
  JPG/PNG/WEBP, минимум 700x900 px, качество сжатия >= 65%,
  до 10 МБ на файл, до 30 фото на карточку, минимум 2 фото для поиска.

Здесь: EXIF-поворот -> sRGB -> вписывание в 3:4 (1200x1600 по умолчанию) ->
адаптивное сжатие JPEG до заданного лимита байт (по умолчанию 9.5 МБ с запасом).
Метаданные (включая GPS) удаляются — это важно для домашней съёмки.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import sys

try:
    from PIL import Image, ImageCms, ImageEnhance, ImageOps
    HAS_PIL = True
except ImportError:
    _deps = Path(__file__).resolve().parents[1] / ".deps"
    if _deps.exists() and str(_deps) not in sys.path:
        sys.path.insert(0, str(_deps))
    try:
        from PIL import Image, ImageCms, ImageEnhance, ImageOps
        HAS_PIL = True
    except ImportError:
        HAS_PIL = False

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".heif", ".tif", ".tiff"}
HEIC_EXTS = {".heic", ".heif"}
TARGET = (900, 1200)          # совместимость со старыми вызовами
RECOMMENDED_TARGET = (1200, 1600)  # 3:4, запас по детализации
MIN_SIZE = (700, 900)         # официальный минимум WB
MAX_BYTES = int(9.5 * 1024 * 1024)  # 9.5 МБ < 10 МБ (MB vs MiB запас)
MAX_PHOTOS = 30
QUALITY_STEPS = [88, 85, 82, 78, 74, 70, 65]
BG = (250, 250, 250)


def _natural_key(path: Path) -> tuple:
    import re
    return tuple(int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", path.name))


def _register_heif() -> bool:
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        return True
    except Exception:
        return False


def _heic_to_jpeg(src: Path) -> Path | None:
    """Конвертирует HEIC во временный JPEG: pillow-heif или macOS sips."""
    if _register_heif():
        try:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                tmp = Path(tempfile.mkstemp(suffix=".jpg")[1])
                im.save(tmp, "JPEG", quality=92)
                return tmp
        except Exception:
            pass
    if os.name == "posix" and shutil.which("sips"):
        tmp = Path(tempfile.mkstemp(suffix=".jpg")[1])
        try:
            subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "92",
                            str(src), "--out", str(tmp)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            return tmp if tmp.stat().st_size > 0 else None
        except Exception:
            tmp.unlink(missing_ok=True)
    return None


def _to_srgb(im: "Image.Image") -> "Image.Image":
    """Приводит цветовой профиль к sRGB, если он есть и отличается."""
    profile = im.info.get("icc_profile")
    if not profile:
        return im
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(profile))
        dst = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(im, src, dst, outputMode="RGB")
    except Exception:
        return im.convert("RGB")


def _fit_canvas(im: "Image.Image", target: tuple[int, int]) -> "Image.Image":
    """Вписывает фото в холст 3:4 без обрезки (поля нейтрального цвета)."""
    canvas = Image.new("RGB", target, BG)
    ratio = min(target[0] / im.width, target[1] / im.height)
    new_size = (max(1, int(im.width * ratio)), max(1, int(im.height * ratio)))
    scaled = im.resize(new_size, Image.LANCZOS) if new_size != im.size else im
    canvas.paste(scaled, ((target[0] - new_size[0]) // 2, (target[1] - new_size[1]) // 2))
    return canvas


def compress_image(src: Path, dst: Path, target: tuple[int, int] = RECOMMENDED_TARGET,
                   max_bytes: int = MAX_BYTES, min_quality: int = 65) -> dict[str, Any]:
    """Сжимает ОДНО фото в JPEG, гарантированно укладываясь в `max_bytes`.

    Возвращает отчёт: размеры до/после, байты до/после, качество, предупреждения.
    """
    src, dst = Path(src), Path(dst)
    info: dict[str, Any] = {
        "source": src.name, "file": dst.name, "ok": False,
        "original_bytes": src.stat().st_size, "bytes": 0,
        "original_size": None, "width": None, "height": None,
        "quality": None, "warnings": [],
    }
    if not HAS_PIL:
        info["warnings"].append("Pillow не установлен — без сжатия не гарантируем лимит 10 МБ")
        shutil.copy2(src, dst)
        info["bytes"] = dst.stat().st_size
        info["ok"] = dst.stat().st_size <= max_bytes
        return info

    work_path = src
    temp: Path | None = None
    if src.suffix.lower() in HEIC_EXTS:
        converted = _heic_to_jpeg(src)
        if converted is None:
            info["warnings"].append("HEIC не прочитан: установите pillow-heif или запустите на macOS (sips)")
            return info
        temp = work_path = converted

    try:
        with Image.open(work_path) as raw:
            info["original_size"] = list(raw.size)
            im = ImageOps.exif_transpose(raw)
            im = _to_srgb(im).convert("RGB")
            # WB: максимум 8000 px по стороне — наш холст и так меньше
            if max(im.size) > 8000:
                im.thumbnail((8000, 8000), Image.LANCZOS)
            if im.width < MIN_SIZE[0] or im.height < MIN_SIZE[1]:
                info["warnings"].append(
                    f"исходник {im.width}x{im.height} меньше минимума 700x900 — увеличили до 3:4, "
                    "детализация от этого не вырастет")
            im = ImageEnhance.Contrast(im).enhance(1.03)
            canvas = _fit_canvas(im, target)

        width, height = canvas.size
        scale = 1.0
        for attempt in range(6):
            if scale < 1.0:
                canvas = canvas.resize((max(MIN_SIZE[0], int(width * scale)),
                                        max(MIN_SIZE[1], int(height * scale))), Image.LANCZOS)
            for q in QUALITY_STEPS:
                if q < min_quality:
                    continue
                buf = io.BytesIO()
                canvas.save(buf, "JPEG", quality=q, optimize=True, progressive=True,
                            subsampling=0 if q >= 85 else 2, dpi=(72, 72))
                data = buf.getvalue()
                if len(data) <= max_bytes:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(data)  # без EXIF/GPS — приватность
                    info.update({"ok": True, "bytes": len(data), "quality": q,
                                 "width": canvas.width, "height": canvas.height})
                    return info
            if canvas.width <= MIN_SIZE[0] or canvas.height <= MIN_SIZE[1]:
                break
            scale *= 0.82
        info["warnings"].append(f"не удалось уложиться в {max_bytes} байт при качестве >= {min_quality}")
        return info
    except Exception as exc:
        info["warnings"].append(f"{src.name}: {exc}")
        return info
    finally:
        if temp:
            temp.unlink(missing_ok=True)


def process_folder(src: Path, dst: Path, max_photos: int = MAX_PHOTOS,
                   target: tuple[int, int] = RECOMMENDED_TARGET,
                   max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    """Обрабатывает все фото из src -> dst/01.jpg, 02.jpg ... с гарантией <= max_bytes."""
    src, dst = Path(src), Path(dst)
    result: dict[str, Any] = {
        "source": str(src), "output": str(dst), "photos": [], "warnings": [],
        "skipped": 0, "total_bytes_before": 0, "total_bytes_after": 0,
        "max_photo_bytes": 0, "limit_bytes": max_bytes,
    }
    if not src.exists():
        result["warnings"].append(f"Папка не найдена: {src}")
        return result
    files = sorted([p for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS], key=_natural_key)
    if not files:
        result["warnings"].append("Нет изображений в папке")
        return result
    dst.mkdir(parents=True, exist_ok=True)
    for old in dst.glob("[0-9][0-9].jpg"):
        old.unlink()

    seen: set[str] = set()
    for f in files:
        if len(result["photos"]) >= max_photos:
            result["warnings"].append(f"Взято первые {max_photos} фото, остальные проигнорированы")
            break
        digest = hashlib.md5(f.read_bytes()).hexdigest()
        if digest in seen:
            result["skipped"] += 1
            continue
        seen.add(digest)
        out = dst / f"{len(result['photos']) + 1:02d}.jpg"
        report = compress_image(f, out, target=target, max_bytes=max_bytes)
        result["total_bytes_before"] += report["original_bytes"]
        if report["ok"]:
            result["total_bytes_after"] += report["bytes"]
            result["max_photo_bytes"] = max(result["max_photo_bytes"], report["bytes"])
            result["photos"].append({
                "file": out.name, "width": report["width"], "height": report["height"],
                "bytes": report["bytes"], "source": f.name, "quality": report["quality"],
                "original_bytes": report["original_bytes"],
            })
        else:
            result["skipped"] += 1
        result["warnings"].extend(f"{f.name}: {w}" for w in report["warnings"])

    if not HAS_PIL:
        result["warnings"].append("Pillow не установлен — фото скопированы без обработки (pip install Pillow)")
    result["ok"] = bool(result["photos"]) and result["max_photo_bytes"] <= max_bytes
    (dst / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def list_photos(folder: Path) -> list[str]:
    p = Path(folder)
    if not p.exists():
        return []
    return [x.name for x in sorted(p.iterdir(), key=_natural_key)
            if x.is_file() and x.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]


def human_mb(n: int | float) -> str:
    return f"{n / 1024 / 1024:.2f} МБ"


def make_demo_photos(vendor_code: str, dst: Path, count: int = 5) -> list[str]:
    """Демо-заглушки, чтобы интерфейс можно было потрогать без своих фото."""
    if not HAS_PIL:
        return []
    from PIL import ImageDraw

    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    palette = [(198, 186, 214), (220, 204, 196), (190, 205, 214), (214, 200, 190), (196, 210, 198)]
    paths = []
    for i in range(count):
        im = Image.new("RGB", TARGET, palette[i % len(palette)])
        d = ImageDraw.Draw(im)
        d.polygon([(300, 260), (450, 360), (600, 260), (540, 470), (620, 900), (280, 900), (360, 470)],
                  fill=tuple(max(0, c - 35) for c in palette[i % len(palette)]))
        d.text((40, 60), f"DEMO / {vendor_code} / {i + 1:02d}", fill=(60, 55, 70))
        d.text((40, 1130), "Photo placeholder - replace with real item photos", fill=(80, 75, 95))
        p = dst / f"{i + 1:02d}.jpg"
        im.save(p, "JPEG", quality=85)
        paths.append(p.name)
    (dst / "manifest.json").write_text(json.dumps({"demo": True, "photos": paths}, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths
