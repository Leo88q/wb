"""Веб-приложение «WB Ресейл Ассистент» — локальный дашборд без внешних зависимостей.

Запуск: python3 run.py --port 8000
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import traceback
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import assistant, bump, descriptions, photos, pipeline, research, sizes
from .db import Database

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DATA = ROOT / "data"
DB_PATH = DATA / "wb_resale.db"
MAX_BODY = 60 * 1024 * 1024

DB = Database(DB_PATH).init()


class Handler(BaseHTTPRequestHandler):
    server_version = "WBResale/1.0"

    # ---------- helpers ----------
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text: str, content_type: str = "text/plain; charset=utf-8", status: int = 200,
              filename: str | None = None) -> None:
        body = text.encode("utf-8") if isinstance(text, str) else text
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str | None = None, filename: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            return self._json({"error": "not found"}, 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("Слишком большой запрос (>60 МБ)")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _path(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    def log_message(self, fmt: str, *args: Any) -> None:
        if "/api/" in (self.path or "") or self.path.startswith("/data/"):
            return  # не засорять лог
        super().log_message(fmt, *args)

    # ---------- routing ----------
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path, query = self._path()
        try:
            if path.startswith("/api/"):
                return self._api_get(path, query)
            return self._static(path)
        except Exception as exc:
            traceback.print_exc()
            return self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        path, query = self._path()
        try:
            body = self._body()
            return self._api_post(path, body, query)
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": f"Некорректный запрос: {exc}"}, 400)
        except Exception as exc:
            traceback.print_exc()
            return self._json({"error": str(exc)}, 500)

    def do_PATCH(self) -> None:
        return self.do_POST()

    # ---------- GET API ----------
    def _api_get(self, path: str, q: dict[str, list[str]]) -> None:
        def one(key: str, default: str = "") -> str:
            return q.get(key, [default])[0]

        if path == "/api/health":
            return self._json({"ok": True, "llm": descriptions.llm_available(),
                               "pillow": _pillow(), "date": date.today().isoformat()})
        if path == "/api/stats":
            s = DB.stats()
            s["calendar"] = bump.calendar(DB, 14)
            s["settings"] = DB.settings()
            return self._json(s)
        if path == "/api/settings":
            return self._json(DB.settings())
        if path == "/api/listings":
            return self._json({"items": DB.list_listings(status=one("status"), q=one("q"),
                                                          limit=int(one("limit", "500")),
                                                          offset=int(one("offset", "0")))})
        m = re.match(r"^/api/listings/([^/]+)$", path)
        if m:
            item = DB.get_listing(urllib.parse.unquote(m.group(1)))
            return self._json(item or {"error": "not found"}, 200 if item else 404)
        if path == "/api/bumps/queue":
            return self._json({"items": DB.bump_tasks(status=one("status", "pending"), limit=500),
                               "calendar": bump.calendar(DB, 21), "settings": DB.settings()})
        if path == "/api/bumps/tasks":
            return self._json({"items": DB.bump_tasks(status=one("status"), limit=500)})
        if path == "/api/research":
            items = research.load_market_csv(DATA / "market.csv")
            return self._json({"analysis": research.analyze(items, query=one("query")),
                               "file": "data/market.csv", "has_data": bool(items)})
        if path == "/api/export/json":
            return self._json(pipeline.export_payload(DB, one("status")))
        if path == "/api/export/csv":
            return self._text(pipeline.export_csv(DB, one("status")), "text/csv; charset=utf-8",
                              filename="wb_resale_listings.csv")
        if path == "/api/export/zip":
            path_out = DATA / "exports" / "wb_resale_export.zip"
            pipeline.export_zip(DB, DATA, path_out, one("status"))
            return self._file(path_out, "application/zip", "wb_resale_export.zip")
        if path == "/api/sizes/grid":
            return self._json({"markdown": sizes.size_grid_markdown(list(sizes.SIZE_TABLE)),
                               "table": [sizes.size_row(r) for r in sizes.SIZE_TABLE]})
        if path == "/api/logs":
            return self._json({"log": _last_lines(LOG_PATH, 100)})
        return self._json({"error": "unknown endpoint", "path": path}, 404)

    # ---------- POST API ----------
    def _api_post(self, path: str, body: dict[str, Any], q: dict[str, list[str]]) -> None:
        if path == "/api/settings":
            return self._json({"ok": True, "settings": DB.set_settings(body)})

        if path == "/api/listings" or path.startswith("/api/listings/"):
            m = re.match(r"^/api/listings/([^/]+)/regenerate$", path)
            if m:
                item = DB.get_listing(urllib.parse.unquote(m.group(1)))
                if not item:
                    return self._json({"error": "not found"}, 404)
                gen = descriptions.generate(item, variant=(item["variant"] or 0) + 1,
                                            use_llm=bool(body.get("use_llm", True)))
                updated = DB.update_listing(item["id"], {"title": gen["title"], "description": gen["description"],
                                                         "keywords": gen["keywords"], "variant": (item["variant"] or 0) + 1,
                                                         "notes": (item.get("notes", "") + f" | {gen['source']}")[-500:]})
                return self._json({"ok": True, "source": gen["source"], "item": updated})
            m = re.match(r"^/api/listings/([^/]+)$", path)
            if m:
                updated = DB.update_listing(urllib.parse.unquote(m.group(1)), body)
                return self._json({"ok": True, "item": updated} if updated else {"error": "not found"}, 200 if updated else 404)

        if path == "/api/pipeline/import":
            text = body.get("csv", "")
            if not text.strip():
                return self._json({"error": "Пустой CSV"}, 400)
            result = pipeline.import_inventory(DB, text, DATA,
                                               use_llm=bool(body.get("use_llm", True)),
                                               vision=bool(body.get("vision", False)),
                                               process_photos=bool(body.get("process_photos", True)))
            return self._json({"ok": True, **result})

        if path == "/api/pipeline/photos":
            result = pipeline.reprocess_photos(DB, DATA, body.get("id", ""))
            return self._json({"ok": True, **result})

        if path == "/api/pipeline/upload-photos":
            vendor = str(body.get("vendor_code", "")).strip()
            if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", vendor):
                return self._json({"error": "Некорректный артикул (только A-Z, 0-9, - и _)"}, 400)
            folder = DATA / "incoming" / vendor
            folder.mkdir(parents=True, exist_ok=True)
            saved = 0
            for f in body.get("files", []):
                name = re.sub(r"[^A-Za-z0-9._-]", "_", str(f.get("name", "photo.jpg")))
                data_url = str(f.get("data", ""))
                if not data_url.startswith("data:") or "," not in data_url:
                    continue
                raw = base64.b64decode(data_url.split(",", 1)[1])
                if len(raw) > 25 * 1024 * 1024:
                    continue
                (folder / name).write_bytes(raw)
                saved += 1
            report = photos.process_folder(folder, DATA / "out" / vendor)
            DB.update_listing(vendor, {"photos": photos.list_photos(DATA / "out" / vendor),
                                       "photo_dir": str(DATA / "out" / vendor)})
            return self._json({"ok": True, "saved": saved, "report": report})

        if path == "/api/pipeline/upload-ready":
            # Файлы уже сжаты в браузере до лимита WB: просто раскладываем по папкам,
            # минуя повторное пережатие (иначе качество JPEG падает дважды).
            out: list[dict[str, Any]] = []
            total_before = total_after = max_file = 0
            for group in body.get("groups", [])[:500]:
                vendor = re.sub(r"[^A-Za-z0-9_-]", "-", str(group.get("vendor_code", "")))[:40].strip("-")
                if not vendor:
                    continue
                folder = DATA / "out" / vendor
                folder.mkdir(parents=True, exist_ok=True)
                append = bool(group.get("append"))
                if not append:
                    for old in folder.glob("[0-9][0-9].jpg"):
                        old.unlink()
                saved = len([x for x in folder.glob("[0-9][0-9].jpg")]) if append else 0
                warnings = []
                for f in group.get("files", [])[:30]:
                    if saved >= 30:
                        warnings.append("взято максимум 30 фото на карточку")
                        break
                    data_url = str(f.get("data", ""))
                    if not data_url.startswith("data:") or "," not in data_url:
                        continue
                    raw = base64.b64decode(data_url.split(",", 1)[1], validate=False)
                    total_before += int(f.get("original") or len(raw))
                    if len(raw) > 10 * 1024 * 1024:
                        warnings.append(f"{f.get('name')}: больше 10 МБ даже после сжатия")
                        continue
                    if not raw.startswith(b"\xff\xd8"):
                        warnings.append(f"{f.get('name')}: не JPEG")
                        continue
                    (folder / f"{saved + 1:02d}.jpg").write_bytes(raw)
                    total_after += len(raw)
                    max_file = max(max_file, len(raw))
                    saved += 1
                names = photos.list_photos(folder)
                existing = DB.get_listing(vendor)
                if existing:
                    DB.update_listing(vendor, {"photos": names, "photo_dir": str(folder)})
                else:
                    DB.upsert_listing({
                        "id": vendor, "vendor_code": vendor, "photos": names,
                        "photo_dir": str(folder), "status": "draft", "type": "платье",
                        "condition": "отличное", "source": group.get("source_folder", ""),
                        "notes": "Фото сжаты в браузере. Заполните CSV, чтобы получить описание и размерный ряд.",
                    })
                out.append({"vendor_code": vendor, "source_folder": group.get("source_folder", ""),
                            "photos": saved, "warnings": warnings})
            return self._json({"ok": True, "groups": out, "total_before": total_before,
                               "total_after": total_after, "max_file": max_file})

        if path == "/api/pipeline/demo":
            result = pipeline.seed_demo(DB, DATA, count=int(body.get("count", 300)),
                                        with_photos=int(body.get("with_photos", 12)))
            return self._json({"ok": True, **result})

        if path == "/api/pipeline/clear-demo":
            with DB.connect() as con:
                cur = con.execute("DELETE FROM listings WHERE is_demo=1")
                con.execute("DELETE FROM bump_tasks")
            return self._json({"ok": True, "deleted": cur.rowcount})

        if path == "/api/bumps/generate":
            result = bump.generate_queue(DB, body.get("date"), int(body.get("per_day", 0)) or None,
                                         include_ready=bool(body.get("include_ready", False)))
            return self._json({"ok": True, **result})

        if path == "/api/bumps/complete":
            result = bump.complete(DB, int(body.get("id", 0)),
                                   apply_price=bool(body.get("apply_price", True)),
                                   apply_description=bool(body.get("apply_description", True)),
                                   custom_price=body.get("custom_price"))
            return self._json({"ok": bool(result), "item": result} if result else {"error": "task not found"}, 200 if result else 404)

        if path == "/api/bumps/skip":
            DB.skip_bump_task(int(body.get("id", 0)))
            return self._json({"ok": True})

        if path == "/api/bumps/clear":
            return self._json({"ok": True, "deleted": DB.clear_pending_bumps()})

        if path == "/api/research/import":
            text = body.get("csv", "")
            items = research.parse_market_csv(text)
            if not items:
                return self._json({"error": "Не удалось распознать строки. Нужны колонки title/название и price/цена."}, 400)
            research.save_market_csv(DATA / "market.csv", items)
            return self._json({"ok": True, "analysis": research.analyze(items), "rows": len(items)})

        if path == "/api/research/reprice":
            from .pricing import suggest_price
            from .research import market_median_for
            median = market_median_for(DATA / "market.csv", body.get("query", ""))
            changed = 0
            for l in DB.all_listings():
                p = suggest_price(l, market_median=median)
                DB.update_listing(l["id"], {"price": p["price"], "min_price": p["min"]})
                changed += 1
            return self._json({"ok": True, "updated": changed, "market_median": median})

        if path == "/api/assistant":
            result = assistant.answer(DB, body.get("message", ""), body.get("listing_id"),
                                      mode=body.get("mode", "seller"), data_dir=DATA)
            return self._json(result)

        if path == "/api/generate/preview":
            item = dict(body)
            gen = descriptions.generate(item, use_llm=bool(body.get("use_llm", True)))
            return self._json({"ok": True, **gen, "size_block": sizes.size_block(item)})

        if path == "/api/seller-api/test":
            from .wb_api import WBSellerAPI
            api = WBSellerAPI(dry_run=True)
            return self._json({"ok": True, "note": "Это контур B2C-селлеров, а не Ресейл. Запрос не отправлен.",
                               "example": api.parent_categories()})

        return self._json({"error": "unknown endpoint", "path": path}, 404)

    # ---------- static ----------
    def _static(self, path: str) -> None:
        if path in ("/", ""):
            return self._file(WEB / "index.html", "text/html; charset=utf-8")
        if path in ("/prepare_photos.command", "/tools/prepare_photos.py"):
            target = ROOT / path.lstrip("/")
            return self._file(target, "application/octet-stream",
                              "prepare_photos.command" if path.endswith(".command") else "prepare_photos.py")
        if path.startswith("/data/out/"):
            rel = path[len("/data/out/"):]
            target = (DATA / "out" / rel).resolve()
            if not str(target).startswith(str((DATA / "out").resolve())):
                return self._json({"error": "forbidden"}, 403)
            return self._file(target)
        rel = path.lstrip("/")
        candidates = [WEB / rel, ROOT / rel]
        for c in candidates:
            c = c.resolve()
            if c.exists() and c.is_file() and (str(c).startswith(str(WEB)) or str(c).startswith(str(ROOT / "docs")) or str(c).startswith(str(ROOT / "extension"))):
                return self._file(c)
        return self._json({"error": "not found", "path": path}, 404)


def _pillow() -> bool:
    try:
        import PIL  # noqa
        return True
    except ImportError:
        return False


def _last_lines(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]


LOG_PATH = DATA / "server.log"


def main() -> None:
    parser = argparse.ArgumentParser(description="WB Ресейл Ассистент")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--seed-demo", action="store_true", help="создать демо-партию, если база пуста")
    parser.add_argument("--reseed", action="store_true", help="пересоздать демо-партию")
    args = parser.parse_args()

    DATA.mkdir(exist_ok=True)
    (DATA / "incoming").mkdir(exist_ok=True)
    (DATA / "out").mkdir(exist_ok=True)
    (DATA / "exports").mkdir(exist_ok=True)
    if args.reseed or (args.seed_demo and DB.stats()["total"] == 0):
        count = pipeline.seed_demo(DB, DATA, count=300, with_photos=12)
        print(f"[demo] создано: {count}")
    print(f"WB Ресейл Ассистент: http://localhost:{args.port}  (БД: {DB_PATH})")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
