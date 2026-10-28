#!/usr/bin/env python3
"""Точка входа: python3 run.py --port 8000

При первом запуске сам ставит Pillow в локальную папку `.deps/` (не в систему),
если библиотека ещё не установлена. Это нужно и для Docker/превью, и для Mac.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEPS = ROOT / ".deps"


def _pillow_ok() -> bool:
    import importlib
    importlib.invalidate_caches()  # папка .deps могла появиться после первой проверки
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def ensure_pillow() -> bool:
    if str(DEPS) not in sys.path:
        sys.path.insert(0, str(DEPS))
    if _pillow_ok():
        return True
    print("[setup] Ставлю Pillow в .deps/ (один раз, ~10 МБ)…", flush=True)
    DEPS.mkdir(exist_ok=True)
    base = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
            "--upgrade", "--target", str(DEPS), "Pillow>=9.0"]
    for extra in (["--break-system-packages"], []):
        try:
            subprocess.check_call(base + extra, timeout=300)
            if _pillow_ok():
                print("[setup] Pillow готов")
                return True
        except Exception:
            continue
    print("[setup] Не удалось поставить Pillow. Фото будут копироваться без сжатия.")
    print("[setup] Решение: python3 -m pip install --user -r requirements.txt")
    return False


ensure_pillow()

from app.server import main  # noqa: E402

if __name__ == "__main__":
    main()
