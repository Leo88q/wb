#!/usr/bin/env python3
"""Опциональный ассистент в Telegram (только стандартная библиотека).

    TELEGRAM_BOT_TOKEN=... python3 tools/telegram_bot.py

Бот не хранит данные и не ходит в WB: он просто пересылает ваши сообщения
в локальный `/api/assistant`, который знает вашу базу вещей. Удобно, чтобы
готовить ответы покупателям прямо с телефона.

Команды: /start /stats /today /bumps /listing DR-001 /buyer DR-001
Префикс «покупатель: вопрос» — черновик ответа покупателю по выбранной вещи.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API = os.getenv("WB_LOCAL_API", "http://127.0.0.1:8000").rstrip("/")
TG = "https://api.telegram.org/bot" + TOKEN
_last_listing: dict[int, str] = {}


def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
    data = urllib.parse.urlencode({k: str(v) for k, v in params.items()}).encode()
    with urllib.request.urlopen(TG + "/" + method, data=data, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def local_get(path: str) -> dict[str, Any]:
    with urllib.request.urlopen(API + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def local_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(API + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def send(chat_id: int, text: str) -> None:
    for i in range(0, len(text), 3800):
        try:
            call("sendMessage", {"chat_id": chat_id, "text": text[i:i + 3800]})
        except Exception as e:
            print("send error:", e)


def handle(chat_id: int, text: str) -> None:
    text = (text or "").strip()
    if text.startswith("/start"):
        return send(chat_id, "Привет! Я ассистент по ресейлу платьев. Спросите «что делать сегодня», "
                             "«как поднять карточку», «посоветуй цену» или выберите вещь: /listing DR-001")
    if text.startswith("/stats"):
        s = local_get("/api/stats")
        return send(chat_id, f"Всего: {s['total']}, готово: {s['by_status'].get('ready',0)}, "
                             f"опубликовано: {s['by_status'].get('published',0)}, "
                             f"поднятий в очереди: {s['bumps_pending']}, сток: {s['stock_value']} ₽")
    if text.startswith("/today"):
        return send(chat_id, local_post("/api/assistant", {"message": "Что делать сегодня?"})["reply"])
    if text.startswith("/bumps"):
        tasks = local_get("/api/bumps/queue")["items"][:10]
        return send(chat_id, "Ближайшие поднятия:\n" + "\n".join(
            f"{t['due_at']} · {t['vendor_code']} · {t['title'][:40]}" for t in tasks) or "Очередь пуста")
    if text.startswith("/listing ") or text.startswith("/buyer "):
        code = text.split(" ", 1)[1].strip()
        _last_listing[chat_id] = code
        l = local_get("/api/listings/" + urllib.parse.quote(code))
        if "error" in l:
            return send(chat_id, "Карточка не найдена")
        return send(chat_id, f"Выбрана вещь {code}: {l['title']}\nЦена {l['price']} ₽, размер {l['size']}, "
                             f"состояние {l['condition']}.\nТеперь пишите вопросы — отвечу по карточке.")
    if text.lower().startswith("покупатель:"):
        code = _last_listing.get(chat_id)
        if not code:
            return send(chat_id, "Сначала выберите вещь: /listing DR-001")
        r = local_post("/api/assistant", {"message": text.split(":", 1)[1].strip(),
                                          "listing_id": code, "mode": "buyer"})
        return send(chat_id, r["reply"])
    r = local_post("/api/assistant", {"message": text, "listing_id": _last_listing.get(chat_id), "mode": "seller"})
    send(chat_id, r["reply"])


def main() -> None:
    if not TOKEN:
        print("Задайте TELEGRAM_BOT_TOKEN=... (токен от @BotFather)")
        return
    print("Бот запущен. Ctrl+C — остановить.")
    offset = 0
    while True:
        try:
            res = call("getUpdates", {"offset": offset, "timeout": 25})
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if msg.get("text"):
                    try:
                        handle(msg["chat"]["id"], msg["text"])
                    except Exception as e:
                        send(msg["chat"]["id"], f"Ошибка: {e}. Проверьте, что запущен python3 run.py")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("poll error:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
