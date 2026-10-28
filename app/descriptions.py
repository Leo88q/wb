"""Генерация заголовков, описаний и ключевых слов для б/у платьев.

Работает БЕЗ внешних сервисов (шаблоны с ротацией формулировок), а при
наличии OPENAI-совместимого API (переменные LLM_*) автоматически использует
LLM, в т.ч. vision-модель для распознавания вещи по фото.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import sizes
from .pricing import normalize_condition

TYPE_WORDS = ["платье", "платье-сарафан", "платье миди", "платье макси", "платье мини", "платье-рубашка", "платье-футляр", "вечернее платье"]
INTROS = [
    "Продаю {type_word} {brand_part}.",
    "{type_word_cap} {brand_part} — из личного гардероба.",
    "Отдам в хорошие руки {type_word} {brand_part}.",
    "{type_word_cap} {brand_part}: подробности и честные фото ниже.",
    "Б/у {type_word} {brand_part} в наличии, готово к отправке.",
]
CONDITION_TEXT = {
    "новое с биркой": "Новое, с биркой, не носилось.",
    "новое": "Новое, не носилось, без следов носки.",
    "отличное": "Состояние отличное: носилось мало, без пятен, затяжек и повреждений.",
    "хорошее": "Состояние хорошее: есть лёгкие следы носки, всё видно на фото.",
    "удовлетворительное": "Состояние удовлетворительное: вещь живая, есть заметные следы носки — смотрите фото.",
    "есть дефекты": "Есть дефекты, честно описываю их ниже и показываю на фото.",
}
DEFECT_PHRASES = [
    "Дефекты: {defects}.",
    "Что важно знать: {defects}.",
    "Нюансы состояния: {defects}.",
    "Особенности: {defects}.",
]
OUTROS = [
    "Отправка оформляется через пункт выдачи Wildberries после заказа.",
    "Перед публикацией проверьте, что все характеристики и фото соответствуют вещи.",
    "Если нужна дополнительная информация, задайте вопрос в сообщениях к объявлению.",
    "Детали посадки и состояния можно уточнить до заказа в сообщениях к объявлению.",
]
CARE = [
    "Рекомендации по уходу лучше сверить с ярлыком изделия.",
    "Состав и уход не указаны — проверьте информацию на ярлыке перед публикацией.",
    "Уход: следуйте рекомендациям на ярлыке изделия.",
]


def _hash(item: dict[str, Any], salt: str = "") -> int:
    key = f"{item.get('vendor_code', '')}|{salt}"
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:12], 16)


def _type_word(item: dict[str, Any]) -> str:
    t = (item.get("type") or item.get("category") or "платье").strip().lower()
    if t and t != "платье" and "плать" in t:
        return t
    style = (item.get("style") or "").lower()
    if "вечер" in style:
        return "вечернее платье"
    if "футляр" in style:
        return "платье-футляр"
    if "сарафан" in t:
        return "платье-сарафан"
    return "платье"


def _brand_part(item: dict[str, Any]) -> str:
    brand = (item.get("brand") or "").strip()
    color = (item.get("color") or "").strip()
    material = (item.get("material") or "").strip()
    parts = [p for p in (brand, color, material) if p]
    return " ".join(parts[:2]) if parts else "без бренда"


def build_title(item: dict[str, Any], variant: int = 0) -> str:
    """Заголовок как для поиска WB: тип + бренд + цвет + размер + состояние."""
    tw = _type_word(item)
    brand = (item.get("brand") or "").strip()
    color = (item.get("color") or "").strip()
    ru = sizes.normalize_size(item.get("size") or item.get("size_ru"))
    size_txt = f"р.{ru}" if ru else ""
    style = (item.get("style") or "").strip()
    tail = ["б/у", "ресейл", "секонд-хенд", "вторая жизнь", "из гардероба"][variant % 5]
    pieces = [tw.capitalize()]
    if brand:
        pieces.append(brand)
    if color:
        pieces.append(color.lower())
    if style and len(" ".join(pieces)) < 55:
        pieces.append(style.lower())
    if size_txt:
        pieces.append(size_txt)
    pieces.append(tail)
    title = ", ".join(p for p in pieces if p)
    return re.sub(r"\s+", " ", title)[:110]


def build_keywords(item: dict[str, Any]) -> list[str]:
    tw = _type_word(item)
    ru = sizes.normalize_size(item.get("size") or item.get("size_ru"))
    words = [
        tw, f"{tw} б/у", f"{tw} ресейл", f"{tw} женское бу",
        (item.get("brand") or "").lower(), (item.get("color") or "").lower(),
        (item.get("material") or "").lower(), (item.get("style") or "").lower(),
        (item.get("season") or "").lower(),
        f"{tw} {ru} размер" if ru else "",
        f"{tw} {item.get('brand', '')} {item.get('color', '')}".strip(),
        "платье на каждый день", "платье для прогулок", "одежда секонд хенд",
    ]
    seen, result = set(), []
    for w in words:
        w = re.sub(r"\s+", " ", w or "").strip(" ,")
        if len(w) > 2 and w.lower() not in seen:
            seen.add(w.lower())
            result.append(w)
    return result[:18]


def build_description(item: dict[str, Any], variant: int | None = None,
                      include_size_block: bool = True) -> str:
    """Готовый текст объявления: уникальный вариант на каждую вещь."""
    v = _hash(item, "description") % 5 if variant is None else variant % 5
    tw = _type_word(item)
    brand = (item.get("brand") or "").strip()
    color = (item.get("color") or "").strip()
    material = (item.get("material") or "").strip()
    condition_raw = (item.get("condition") or "").strip()
    condition = normalize_condition(condition_raw) if condition_raw else ""
    defects_raw = (item.get("defects") or "").strip()
    defects_absent = defects_raw.lower() in {"нет", "без дефектов", "-", "не обнаружено", "отсутствуют"}
    defects = "" if defects_absent else defects_raw
    ru = sizes.normalize_size(item.get("size") or item.get("size_ru"))
    season = (item.get("season") or "").strip()
    style = (item.get("style") or "").strip()
    meas = sizes.parse_measurements(item)

    blocks: list[str] = []
    blocks.append(INTROS[v].format(type_word=tw, type_word_cap=tw.capitalize(), brand_part=_brand_part(item)))

    features = []
    if brand:
        features.append(f"Бренд: {brand}")
    if color:
        features.append(f"Цвет: {color}")
    if material:
        features.append(f"Материал/состав: {material}")
    if season:
        features.append(f"Сезон: {season}")
    if style:
        features.append(f"Стиль: {style}")
    if ru:
        row = sizes.size_row(ru)
        features.append(f"Размер: {ru} ({row['intl']}, EU {row['eu']})")
    if features:
        blocks.append("\n".join(features))

    if condition:
        blocks.append(CONDITION_TEXT[condition])
    else:
        blocks.append("Состояние не указано — проверьте вещь перед публикацией и выберите точную категорию.")
    if defects:
        blocks.append(DEFECT_PHRASES[v % len(DEFECT_PHRASES)].format(defects=defects.rstrip(".")))
    elif defects_absent:
        blocks.append("По осмотру продавца дефекты не обнаружены.")
    else:
        blocks.append("Информация о дефектах не заполнена — осмотрите вещь и добавьте все нюансы.")

    if include_size_block:
        blocks.append(sizes.size_block(item) if (ru or meas) else "Размер уточняйте в сообщениях — сделаю дополнительные фото и замеры.")

    blocks.append(CARE[_hash(item, "care") % len(CARE)])
    blocks.append(OUTROS[v % len(OUTROS)])

    kw = build_keywords(item)
    blocks.append("Ключевые слова: " + ", ".join(kw[:12]) + ".")
    blocks.append("Перед публикацией проверьте, что фотографии показывают вещь и заметные нюансы состояния.")
    return "\n\n".join(b for b in blocks if b.strip())


def _llm_config() -> dict[str, str] | None:
    url = os.getenv("LLM_API_URL", "").strip()
    key = os.getenv("LLM_API_KEY", "").strip()
    if not url or not key:
        return None
    return {
        "url": url.rstrip("/") if url.rstrip("/").endswith("/chat/completions") else url.rstrip("/") + "/chat/completions",
        "key": key,
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "timeout": os.getenv("LLM_TIMEOUT", "45"),
    }


def llm_available() -> bool:
    return _llm_config() is not None


def _chat(cfg: dict[str, str], messages: list[dict[str, Any]], max_tokens: int = 900) -> str | None:
    payload = json.dumps({
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        cfg["url"], data=payload,
        headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(cfg["timeout"])) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, KeyError, ValueError, IndexError) as exc:
        print(f"[LLM] недоступен, используем шаблон: {exc}")
        return None


def generate(item: dict[str, Any], variant: int | None = None, use_llm: bool = True) -> dict[str, Any]:
    """Возвращает {title, description, keywords, source}."""
    cfg = _llm_config() if use_llm else None
    if cfg:
        sys_prompt = (
            "Ты — копирайтер объявлений для раздела «Ресейл» Wildberries (б/у вещи). "
            "Пиши по-русски, честно и конкретно, без воды и без превосходных степеней. "
            "Структура: 1) короткий абзац о вещи, 2) характеристики списком, "
            "3) честное состояние и ВСЕ дефекты, 4) замеры в см и на какой размер подойдёт, "
            "5) уход и отправка через ПВЗ Wildberries, 6) строка с ключевыми словами. "
            "Запрещено: ссылки, телефоны, упоминание других площадок, обещания, "
            "что вещь новая, если она б/у, скрывать дефекты. "
            "Верни ТОЛЬКО JSON: {title, description, keywords[]}."
        )
        data = {k: v for k, v in item.items() if k in {
            "vendor_code", "brand", "type", "color", "print", "material", "size",
            "condition", "defects", "chest", "waist", "hips", "length", "sleeve",
            "shoulders", "season", "style", "notes",
        }}
        answer = _chat(cfg, [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": "Данные о вещи (JSON):\n" + json.dumps(data, ensure_ascii=False)},
        ])
        if answer:
            try:
                start, end = answer.find("{"), answer.rfind("}")
                parsed = json.loads(answer[start:end + 1])
                return {
                    "title": str(parsed.get("title") or build_title(item, variant or 0))[:110],
                    "description": str(parsed.get("description") or "").strip() or build_description(item, variant),
                    "keywords": parsed.get("keywords") or build_keywords(item),
                    "source": f"LLM ({cfg['model']})",
                }
            except (ValueError, TypeError):
                pass
    return {
        "title": build_title(item, variant or 0),
        "description": build_description(item, variant),
        "keywords": build_keywords(item),
        "source": "шаблон (offline)",
    }


VISION_PROMPT = (
    "Ты определяешь параметры б/у одежды по фотографии для объявления на ресейл-площадке. "
    "Осмотри вещь и верни ТОЛЬКО JSON без пояснений: "
    '{"type":"...","color":"...","material":"...","style":"...","print":"...",'
    '"size_guess":"...","condition":"отличное|хорошее|удовлетворительное|есть дефекты",'
    '"defects":"...","confidence":0.0}. '
    "Не выдумывай бренд и замеры: если не видно, пиши пустую строку. "
    "Размер определяй только если видна бирка. Опирайся на видимые дефекты."
)


def describe_from_photo(path: Path) -> dict[str, Any] | None:
    """Vision-распознавание вещи по первому фото (если включено LLM_VISION=1)."""
    import base64

    cfg = _llm_config()
    if not cfg or os.getenv("LLM_VISION", "0") != "1" or not path.exists():
        return None
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    data_uri = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    answer = _chat(cfg, [
        {"role": "system", "content": VISION_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": "Опиши вещь на фото для карточки б/у платья."},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]},
    ], max_tokens=500)
    if not answer:
        return None
    try:
        start, end = answer.find("{"), answer.rfind("}")
        return json.loads(answer[start:end + 1])
    except ValueError:
        return None


def draft_buyer_reply(item: dict[str, Any], question: str, use_llm: bool = True) -> dict[str, Any]:
    """Черновик ответа покупателю в чате объявления."""
    cfg = _llm_config() if use_llm else None
    context = {
        "title": item.get("title"),
        "brand": item.get("brand"),
        "size": item.get("size"),
        "condition": item.get("condition"),
        "defects": item.get("defects"),
        "measurements": sizes.parse_measurements(item),
        "price": item.get("price"),
        "description": item.get("description"),
    }
    if cfg:
        answer = _chat(cfg, [
            {"role": "system", "content": (
                "Ты — вежливый ассистент продавца б/у одежды в разделе «Ресейл» Wildberries. "
                "Отвечай кратко (2–5 предложений), по-русски, только по данным карточки. "
                "Если данных нет — честно скажи, что уточнишь/сделаешь дополнительное фото. "
                "Не предлагай сделки мимо площадки, не давай ссылки и телефоны, не обещай скидку больше, чем указано. "
                "Не выдумывай замеры и состояние."
            )},
            {"role": "user", "content": f"Карточка: {json.dumps(context, ensure_ascii=False)}\nВопрос покупателя: {question}"},
        ], max_tokens=350)
        if answer:
            return {"reply": answer.strip(), "source": "LLM"}
    return {"reply": _rule_based_reply(item, question), "source": "шаблон (offline)"}


def _rule_based_reply(item: dict[str, Any], question: str) -> str:
    q = (question or "").lower()
    meas = sizes.parse_measurements(item)
    ru = sizes.normalize_size(item.get("size") or item.get("size_ru"))
    name = (item.get("title") or "Платье").split(",")[0]
    price = item.get("price")
    defects = (item.get("defects") or "").strip()
    cond = normalize_condition(item.get("condition"))

    if any(w in q for w in ("замер", "размер", "сядет", "подойдёт", "подойдет", "обхват", "длина", "см")):
        m = sizes.measurements_text(meas, multiline=False) if meas else "замеры уточню сегодня и пришлю фото с сантиметром"
        return (f"Здравствуйте! {name}: размер на бирке {ru or 'уточняется'}. {m}. "
                "Сверьте, пожалуйста, со своей вещью — у б/у одежды посадка важнее цифры на ярлыке. "
                "Могу сделать дополнительные фото с лентой, если нужно.")
    if any(w in q for w in ("дефект", "пятн", "состоян", "дыр", "затяж", "потерт", "как нов")):
        d = f"Есть нюансы: {defects}." if defects and defects.lower() not in {"нет", "-", "без дефектов"} else "Пятен, затяжек и повреждений нет."
        return f"Здравствуйте! Состояние — {cond}. {d} Все фото реальные и сделаны при дневном свете, при необходимости пришлю крупный план."
    if any(w in q for w in ("торг", "скид", "дешевле", "цена", "рубл", "₽", "предлож")):
        return (f"Здравствуйте! Сейчас цена {price} ₽. Небольшой торг через кнопку «Предложить цену» "
                "в объявлении — я вижу уведомление и отвечу. Фиксировать вещь заранее, к сожалению, нельзя.")
    if any(w in q for w in ("достав", "отправ", "пвз", "когда", "сколько едет", "забрать")):
        return ("Здравствуйте! Отправляю через пункт выдачи Wildberries: после оплаты передам вещь в ПВЗ в течение 1–2 дней, "
                "далее доставка идёт обычными сроками площадки. Личные встречи и отправка мимо WB не предусмотрены.")
    if any(w in q for w in ("состав", "материал", "ткань", "стирк", "уход")):
        return (f"Здравствуйте! Состав: {item.get('material') or 'уточню по бирке и пришлю фото ярлыка'}. "
                "Рекомендую деликатную стирку при 30 градусах. Вещь перед отправкой чистая и отпаренная.")
    if any(w in q for w in ("пример", "вернуть", "возврат", "не подойдёт", "не подойдет")):
        return ("Здравствуйте! Примерка возможна при получении в ПВЗ — условия возврата регулируются правилами Wildberries. "
                "Чтобы всё подошло, сверьте замеры в описании, я могу прислать дополнительные фото.")
    return (f"Здравствуйте! Спасибо за интерес к вещи. Это {name}, состояние — {cond}, "
            "все замеры и фото есть в объявлении. Если чего-то не хватает — напишите, сделаю дополнительные снимки и отвечу на любой вопрос.")
