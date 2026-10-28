#!/usr/bin/env python3
"""ПОЛУАВТОМАТИЧЕСКАЯ публикация/редактирование объявлений WB Ресейл через браузер.

⚠️ ДИСКЛЕЙМЕР
У раздела «Ресейл» нет публичного API. Этот сценарий управляет ВАШИМ браузером
через Playwright, используя вашу авторизацию. Автоматизация действий в личном
кабинете может противоречить правилам Wildberries и привести к ограничениям
аккаунта. Скрипт НЕ обходит капчу, НЕ использует чужие аккаунты и по умолчанию
ничего не отправляет: включите --no-dry-run осознанно, с лимитом 10–20 вещей
в день и ручным контролем первого прохода.

Установка:
    pip install playwright && python -m playwright install chromium
Запуск (сначала поднимите локальный сервер): python3 run.py
    python3 tools/autopublish.py --mode publish --limit 10 --dry-run
    python3 tools/autopublish.py --mode publish --limit 10 --no-dry-run
    python3 tools/autopublish.py --mode bump --limit 15 --no-dry-run

Селекторы формы настраиваются в extension/selectors.json (тот же формат).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTORS_FILE = ROOT / "extension" / "selectors.json"
API = "http://127.0.0.1:8000"


def api_get(path: str):
    with urllib.request.urlopen(API + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def api_post(path: str, payload: dict):
    req = urllib.request.Request(API + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def load_selectors() -> dict:
    if SELECTORS_FILE.exists():
        try:
            return json.loads(SELECTORS_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def set_value(page, selectors: list[str], value: str) -> bool:
    for sel in selectors or []:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=700):
                loc.fill(str(value), timeout=3000)
                return True
        except Exception:
            continue
    return False


def main() -> int:
    global API
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["publish", "bump"], default="publish")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--create-url", default="", help="URL формы создания объявления (узнайте в браузере)")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    ap.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    ap.add_argument("--delay", type=float, default=12.0, help="пауза между объявлениями, сек")
    args = ap.parse_args()
    API = args.api.rstrip("/")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Нужен Playwright: pip install playwright && python -m playwright install chromium")
        return 1

    sel = load_selectors()
    create_url = args.create_url or sel.get("create_url") or "https://www.wildberries.ru/"

    if args.mode == "publish":
        items = [i for i in api_get("/api/export/json?status=ready") if i["photos"]][:args.limit]
        print(f"[publish] готово к публикации: {len(items)} (лимит {args.limit})")
    else:
        tasks = api_get("/api/bumps/queue")["items"][:args.limit]
        print(f"[bump] задач в очереди: {len(tasks)} (лимит {args.limit})")
        items = tasks

    if args.dry_run:
        for i in items[:5]:
            print("  •", i.get("vendor_code"), "|", (i.get("title") or "")[:70], "|", i.get("price"), "₽")
        print("\nDRY-RUN: браузер не запускался. Добавьте --no-dry-run для реального прохода.")
        return 0

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(ROOT / ".playwright-profile"), headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.wildberries.ru/", wait_until="domcontentloaded", timeout=60000)
        print("\n>>> Войдите в аккаунт WB в открытом браузере, откройте раздел «Ресейл»")
        print(">>> и форму создания объявления. Затем нажмите Enter в этом терминале.")
        try:
            input()
        except EOFError:
            print("Нет интерактивного ввода — завершаюсь. Запускайте из терминала.")
            ctx.close()
            return 1

        for n, item in enumerate(items, 1):
            try:
                if args.mode == "publish":
                    url = create_url if create_url != "https://www.wildberries.ru/" else page.url
                    if create_url and create_url != page.url:
                        page.goto(create_url, wait_until="domcontentloaded", timeout=60000)
                    filled, missed = [], []
                    for key, value in (("title", item["title"]), ("description", item["description"]),
                                       ("price", item["price"]), ("size", item["size"])):
                        if value and set_value(page, sel.get(key, []), value):
                            filled.append(key)
                        elif value:
                            missed.append(key)
                    photos = [str(ROOT / p) for p in item["photos"]]
                    try:
                        file_input = page.locator("input[type='file']").first
                        if file_input.count():
                            file_input.set_input_files(photos[:30], timeout=15000)
                            filled.append("photos")
                    except Exception as e:
                        missed.append(f"photos ({e.__class__.__name__})")
                    print(f"[{n}/{len(items)}] {item['vendor_code']}: заполнено {filled}; вручную: {missed}")
                    print("    Проверьте форму и нажмите «Опубликовать» сами. После этого нажмите Enter здесь.")
                    input()
                    api_post(f"/api/listings/{item['vendor_code']}", {
                        "status": "published",
                        "published_at": time.strftime("%Y-%m-%d"),
                        "next_bump_at": time.strftime("%Y-%m-%d", time.localtime(time.time() + 10*86400)),
                    })
                else:
                    # режим bump: открываем объявление, обновляем текст/цену, сохраняем
                    listing = api_get("/api/listings/" + item["listing_id"])
                    if not listing.get("wb_url"):
                        print(f"[{n}] у {item['listing_id']} нет ссылки wb_url — пропуск")
                        continue
                    page.goto(listing["wb_url"], wait_until="domcontentloaded", timeout=60000)
                    print(f"[{n}/{len(items)}] {item['listing_id']}: откройте редактирование, вставьте новый текст:")
                    print("    ", (item["suggested_description"] or "")[:120].replace("\n", " "), "…")
                    print("    Новый текст скопирован в буфер обмена (если поддерживается). После сохранения нажмите Enter.")
                    try:
                        page.evaluate("t => navigator.clipboard && navigator.clipboard.writeText(t)", item["suggested_description"])
                    except Exception:
                        pass
                    input()
                    api_post("/api/bumps/complete", {"id": item["id"], "apply_price": False, "apply_description": True})
                time.sleep(args.delay)
            except KeyboardInterrupt:
                print("\nОстановлено пользователем")
                break
            except Exception as e:
                print(f"[{n}] ошибка: {e}. Пропускаем карточку.")
                continue
        print("Готово. Закройте браузер или оставьте профиль для следующего раза.")
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
