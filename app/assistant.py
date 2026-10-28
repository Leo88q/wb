"""Ассистент продавца: отвечает на вопросы по процессу, готовит тексты и ответы покупателям."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from . import descriptions, sizes
from .db import Database
from .pricing import suggest_price
from .research import analyze, load_market_csv

HELP = {
    "start": (
        "Я помощник по ресейлу б/у платьев на Wildberries. Умею:\n"
        "• объяснить, что можно автоматизировать, а что нет;\n"
        "• собрать/переписать описание и заголовок;\n"
        "• посчитать размерный ряд и замеры;\n"
        "• посоветовать цену по состоянию и рынку;\n"
        "• составить план публикаций и «поднятий» на сегодня;\n"
        "• набросать вежливый ответ покупателю в чате объявления."
    ),
    "api": (
        "Официального API для частных объявлений WB Ресейл нет. Seller API (Content API) — "
        "это другой контур: карточки товаров для селлеров с ИП/самозанятостью, там нет метки «б/у». "
        "Поэтому полная автоматическая публикация «в один клик» по официальному API невозможна. "
        "Автоматизируем подготовку (фото → описание → размеры → цена → экспорт), "
        "а финальную публикацию ускоряем расширением для браузера или полуавтоматическим сценарием Playwright."
    ),
    "bump": (
        "Автоматического «поднять» у Ресейла нет. Работает редактирование объявления: "
        "срок публикации 2 месяца начинает отсчитываться заново, карточка считается свежей. "
        "Снижение цены дополнительно рассылает push тем, кто просил скидку через «Предложить цену». "
        "Я веду очередь: 10–15 объявлений в день, интервал 10 дней, без спама. "
        "Слишком частые правки могут выглядеть подозрительно для модерации — не злоупотребляйте."
    ),
    "photos": (
        "Складывайте фото в data/incoming/<артикул>/. Пайплайн приводит их к 900×1200 (3:4), "
        "чинит поворот, убирает метаданные, нумерует 01.jpg… и убирает дубликаты. "
        "Первое фото — обложка. Нужны: общий план, бирка, состав, замеры, дефекты крупным планом."
    ),
    "sizes": (
        "Размерный ряд считаю по типовой сетке: российский → INT/EU/US + грудь/талия/бёдра. "
        "Реальные замеры из CSV приоритетнее. На их основе подбираю «подойдёт на 44–46» "
        "и генерирую блок замеров в описание — это главный срез вопросов от покупателей."
    ),
    "rules": (
        "Ограничения WB Ресейл: нужна верификация WB Кошелька (Госуслуги), 100% предоплата, "
        "доставка через ПВЗ, объявление живёт 2 месяца, запрещены техника, ювелирка, лекарства, "
        "продукты, алкоголь, растения и 18+. Габариты до 60×40×40 см, лимит цены уточняйте в приложении. "
        "Комиссия порядка 5–6% с успешной сделки."
    ),
    "plan": (
        "План на 300 платьев: 1) снять фото по шаблону, 2) завести CSV со структурированными полями, "
        "3) обработать папки и сгенерировать описания пачкой, 4) выгрузить 20–30 объявлений в день "
        "(не все 300 сразу — модерация и лимиты), 5) через расширение заполнять форму за 40–60 секунд, "
        "6) включить очередь поднятий и отвечать покупателям по черновикам ассистента."
    ),
}

INTENTS: list[tuple[str, tuple[str, ...]]] = [
    ("api", ("api", "апи", "автоматич", "выгрузк", "массов", "пакетн", "скрипт", "парсер")),
    ("bump", ("поднят", "поднимат", "продвиж", "обновить карточ", "редактир", "срок публикац", "вверх", "топ")),
    ("photos", ("фото", "снимк", "картинк", "изображен", "jpeg", "jpg")),
    ("sizes", ("размер", "замер", "сетк", "обхват", " merk", "сантиметр")),
    ("rules", ("правил", "ограничен", "запрещ", "комисс", "верификац", "кошел", "пвз", "доставк")),
    ("plan", ("план", "с чего начать", "как начать", "стратег", "300", "парти")),
    ("price", ("цена", "цену", "стоим", "дорого", "дешевле", "рубл", "₽")),
]


def _detect_intent(message: str) -> str | None:
    m = message.lower()
    for intent, words in INTENTS:
        if any(w in m for w in words):
            return intent
    return None


def _listing_context(db: Database, listing_id: str | None) -> tuple[dict[str, Any] | None, str]:
    if not listing_id:
        return None, ""
    l = db.get_listing(listing_id)
    if not l:
        return None, ""
    return l, (f"\n\nКонтекст вещи: {l['vendor_code']} — {l['title']}, бренд {l['brand']}, "
               f"размер {l['size']}, состояние {l['condition']}, цена {l['price']} ₽. "
               f"Замеры: {sizes.measurements_text(l['measurements'], multiline=False) or 'не указаны'}. "
               f"Дефекты: {l['defects'] or 'нет'}.")


def _llm_answer(db: Database, message: str, context: str) -> str | None:
    from .descriptions import _chat, _llm_config  # переиспользуем конфиг

    cfg = _llm_config()
    if not cfg or os.getenv("ASSISTANT_LLM", "1") != "1":
        return None
    stats = db.stats()
    settings = db.settings()
    system = (
        "Ты — практичный ассистент продавца б/у одежды в разделе «Ресейл» Wildberries. "
        "Отвечай по-русски, коротко и по делу, без общих фраз. "
        "Факты: официального API для C2C-объявлений нет; публикация — через сайт/приложение; "
        "объявление живёт 2 месяца, редактирование продлевает срок; снижение цены пушует "
        "интересовавшихся; автоматизируем подготовку контента и планирование, а не обход защиты WB. "
        "Не советуй обходить капчу, использовать чужие аккаунты или спамить. "
        f"Сейчас в базе: {stats['total']} вещей, опубликовано {stats['by_status'].get('published', 0)}, "
        f"ждут {stats['bumps_due']} поднятий. Интервал поднятий: {settings['bump_interval_days']} дн., "
        f"по {settings['bump_per_day']} объявлений в день."
    )
    answer = _chat(cfg, [
        {"role": "system", "content": system},
        {"role": "user", "content": message + context},
    ], max_tokens=600)
    return answer.strip() if answer else None


def answer(db: Database, message: str, listing_id: str | None = None,
           mode: str = "seller", data_dir=None) -> dict[str, Any]:
    """mode: 'seller' (помощник) или 'buyer' (черновик ответа покупателю)."""
    message = (message or "").strip()
    l, context = _listing_context(db, listing_id)
    if not message:
        return {"reply": HELP["start"], "actions": [], "source": "offline"}

    if mode == "buyer":
        if not l:
            return {"reply": "Выберите вещь в карточке, чтобы я подготовил ответ покупателю.", "actions": [], "source": "offline"}
        result = descriptions.draft_buyer_reply(l, message)
        return {"reply": result["reply"], "source": result["source"],
                "actions": [{"label": "Скопировать ответ", "action": "copy", "payload": result["reply"]}]}

    lower = message.lower()
    # быстрые команды с данными из базы
    if re.search(r"(описан|заголовок|текст).*(перепиш|сгенерир|сделай|напиши)|перепиши описание", lower):
        if l:
            gen = descriptions.generate(l, variant=(l["variant"] or 0) + 1)
            db.update_listing(l["id"], {"title": gen["title"], "description": gen["description"],
                                       "keywords": gen["keywords"], "variant": (l["variant"] or 0) + 1})
            return {"reply": f"Готово, обновил карточку {l['vendor_code']} ({gen['source']}).\n\n{gen['description'][:600]}…",
                    "source": gen["source"], "actions": [{"label": "Открыть карточку", "action": "open_listing", "payload": l["id"]}]}
        return {"reply": "Выберите вещь слева, и я перепишу для неё описание.", "actions": [], "source": "offline"}

    if any(w in lower for w in ("что делать сегодня", "план на сегодня", "задачи на сегодня", "очередь")):
        stats = db.stats()
        tasks = db.bump_tasks(status="pending", due_before=None or "9999-12-31", limit=5)
        lines = [f"Сегодня: {stats['total']} вещей в базе, из них опубликовано {stats['by_status'].get('published', 0)}, "
                 f"готово к публикации {stats['by_status'].get('ready', 0)}, черновиков {stats['by_status'].get('draft', 0)}. "
                 f"Поднятий в очереди: {stats['bumps_pending']}."]
        if tasks:
            lines.append("Ближайшие задачи: " + ", ".join(t["vendor_code"] for t in tasks))
        else:
            lines.append("Очередь поднятий пуста — сгенерируйте её во вкладке «Поднятие».")
        lines.append("Рекомендую: 20–30 новых объявлений в день + 10–15 редактирований по очереди.")
        return {"reply": "\n".join(lines), "actions": [{"label": "Открыть очередь", "action": "tab", "payload": "bumps"}], "source": "offline"}

    if any(w in lower for w in ("рынок", "ресерч", "исследовани", "конкурент", "медиана")) and data_dir:
        items = load_market_csv(data_dir / "market.csv")
        if not items:
            return {"reply": "Данных о рынке пока нет. Загрузите CSV с объявлениями конкурентов во вкладке «Исследование» — посчитаю медиану и вилку цен.",
                    "actions": [{"label": "Открыть исследование", "action": "tab", "payload": "research"}], "source": "offline"}
        a = analyze(items)
        return {"reply": (f"В базе {a['total']} объявлений конкурентов. Медиана цены {a['median']} ₽, "
                          f"нижний квартиль {a['p25']} ₽, верхний {a['p75']} ₽, диапазон {a['min']}–{a['max']} ₽. "
                          "Частые слова: " + ", ".join(w["word"] for w in a["top_words"][:8]) + "."),
                "actions": [{"label": "Открыть исследование", "action": "tab", "payload": "research"}], "source": "offline"}

    if any(w in lower for w in ("цену посчитай", "посоветуй цену", "какая цена", "оцени цену")) and l:
        p = suggest_price(l)
        return {"reply": (f"Для {l['vendor_code']}: состояние «{p['condition']}», сегмент «{p['brand_tier']}». "
                          f"Рекомендую старт {p['price']} ₽ (вилка {p['min']}–{p['max']} ₽). "
                          f"Расчёт: {p['source']}. Текущая цена {l['price']} ₽."),
                "actions": [], "source": "offline"}

    llm = _llm_answer(db, message, context)
    if llm:
        return {"reply": llm, "actions": [], "source": "LLM"}

    intent = _detect_intent(message)
    if intent:
        return {"reply": HELP[intent] + context, "actions": _actions(intent), "source": "offline"}
    return {"reply": HELP["start"] + context, "actions": _actions("start"), "source": "offline"}


def _actions(intent: str) -> list[dict[str, Any]]:
    mapping = {
        "api": [{"label": "Как устроена выгрузка", "action": "tab", "payload": "content"}],
        "bump": [{"label": "Открыть очередь поднятий", "action": "tab", "payload": "bumps"}],
        "photos": [{"label": "Обработать фото", "action": "tab", "payload": "content"}],
        "sizes": [{"label": "Размерная сетка", "action": "tab", "payload": "listings"}],
        "plan": [{"label": "Обзор", "action": "tab", "payload": "overview"}],
    }
    return mapping.get(intent, [])
