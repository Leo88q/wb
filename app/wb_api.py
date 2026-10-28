"""Клиент ОФИЦИАЛЬНОГО Seller API Wildberries (контур B2C).

ВНИМАНИЕ: это НЕ API раздела «Ресейл». Content API создаёт карточки товаров
в кабинете селлера (ИП/самозанятый/ООО), где продажа б/у вещей как таковых
не предусмотрена. Модуль оставлен как заготовка на будущее (если WB откроет
API для C2C-объявлений) и для сценария «продаю уценённые/стоковые вещи
как селлер». Все методы по умолчанию в режиме dry-run, без записи.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

CONTENT = "https://content-api.wildberries.ru"
PRICES = "https://discounts-prices-api.wildberries.ru"


class WBSellerAPI:
    def __init__(self, token: str | None = None, dry_run: bool = True, timeout: int = 30):
        self.token = token or os.getenv("WB_API_TOKEN", "")
        self.dry_run = dry_run
        self.timeout = timeout

    def _call(self, method: str, url: str, payload: Any = None, scope: str = CONTENT) -> dict[str, Any]:
        full = url if url.startswith("http") else scope.rstrip("/") + "/" + url.lstrip("/")
        if self.dry_run:
            return {"dry_run": True, "method": method, "url": full, "payload": payload}
        if not self.token:
            return {"error": "Не задан WB_API_TOKEN"}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(full, data=data, method=method, headers={
            "Authorization": self.token,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return {"status": r.status, "body": r.read().decode("utf-8")}
        except urllib.error.HTTPError as e:
            return {"status": e.code, "error": e.read().decode("utf-8")[:1000]}
        except (urllib.error.URLError, OSError) as e:
            return {"error": str(e)}

    # справочники
    def parent_categories(self) -> dict[str, Any]:
        return self._call("GET", "/content/v2/object/parent/all")

    def subjects(self, name: str = "", limit: int = 100) -> dict[str, Any]:
        return self._call("GET", f"/content/v2/object/all?name={name}&limit={limit}")

    def subject_charcs(self, subject_id: int) -> dict[str, Any]:
        return self._call("GET", f"/content/v2/object/charcs/{subject_id}")

    # карточки
    def upload_cards(self, cards: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call("POST", "/content/v2/cards/upload", cards)

    def cards_list(self, cursor: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = cursor or {"settings": {"cursor": {"limit": 100}, "filter": {"withPhoto": -1}}}
        return self._call("POST", "/content/v2/get/cards/list", payload)

    def failed_cards(self) -> dict[str, Any]:
        return self._call("GET", "/content/v2/cards/error/list")

    def update_cards(self, cards: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call("POST", "/content/v2/cards/update", cards)

    # медиа и цены
    def save_media(self, nm_id: int, urls: list[str]) -> dict[str, Any]:
        return self._call("POST", "/content/v3/media/save", {"nmId": nm_id, "data": urls})

    def set_prices(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call("POST", "/api/v2/upload/task", {"data": data}, scope=PRICES)


def card_payload_example(subject_id: int, vendor_code: str, title: str,
                         description: str, characteristics: list[dict[str, Any]],
                         size: str, price: int, sku: str) -> list[dict[str, Any]]:
    """Пример тела запроса для /content/v2/cards/upload (НЕ для ресейла)."""
    return [{
        "subjectID": subject_id,
        "variants": [{
            "vendorCode": vendor_code,
            "title": title,
            "description": description,
            "brand": "Нет бренда",
            "characteristics": characteristics,
            "sizes": [{"techSize": size, "wbSize": "RU", "price": price, "skus": [sku]}],
        }],
    }]
