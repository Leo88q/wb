"""SQLite-хранилище: объявления, очередь поднятий, настройки."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    vendor_code TEXT UNIQUE,
    title TEXT DEFAULT '',
    description TEXT DEFAULT '',
    keywords TEXT DEFAULT '[]',
    brand TEXT DEFAULT '',
    type TEXT DEFAULT 'платье',
    color TEXT DEFAULT '',
    print TEXT DEFAULT '',
    material TEXT DEFAULT '',
    size TEXT DEFAULT '',
    condition TEXT DEFAULT 'отличное',
    defects TEXT DEFAULT '',
    measurements TEXT DEFAULT '{}',
    season TEXT DEFAULT '',
    style TEXT DEFAULT '',
    source TEXT DEFAULT '',
    price INTEGER DEFAULT 0,
    price_start INTEGER DEFAULT 0,
    min_price INTEGER DEFAULT 0,
    photos TEXT DEFAULT '[]',
    photo_dir TEXT DEFAULT '',
    status TEXT DEFAULT 'draft',
    wb_url TEXT DEFAULT '',
    is_demo INTEGER DEFAULT 0,
    variant INTEGER DEFAULT 0,
    published_at TEXT DEFAULT '',
    last_bump_at TEXT DEFAULT '',
    next_bump_at TEXT DEFAULT '',
    bump_count INTEGER DEFAULT 0,
    views INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_next_bump ON listings(next_bump_at);

CREATE TABLE IF NOT EXISTS bump_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL,
    due_at TEXT NOT NULL,
    kind TEXT DEFAULT 'edit',
    suggested_price INTEGER DEFAULT 0,
    suggested_description TEXT DEFAULT '',
    note TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT '',
    done_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_bump_due ON bump_tasks(due_at, status);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

LISTING_FIELDS = [
    "id", "vendor_code", "title", "description", "keywords", "brand", "type", "color",
    "print", "material", "size", "condition", "defects", "measurements", "season",
    "style", "source", "price", "price_start", "min_price", "photos", "photo_dir",
    "status", "wb_url", "is_demo", "variant", "published_at", "last_bump_at",
    "next_bump_at", "bump_count", "views", "notes", "created_at", "updated_at",
]

FIELD_DEFAULTS: dict[str, Any] = {
    "title": "", "description": "", "keywords": "[]", "brand": "", "type": "платье",
    "color": "", "print": "", "material": "", "size": "", "condition": "отличное",
    "defects": "", "measurements": "{}", "season": "", "style": "", "source": "",
    "price": 0, "price_start": 0, "min_price": 0, "photos": "[]", "photo_dir": "",
    "status": "draft", "wb_url": "", "is_demo": 0, "variant": 0, "published_at": "",
    "last_bump_at": "", "next_bump_at": "", "bump_count": 0, "views": 0, "notes": "",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "bump_interval_days": 10,
    "bump_per_day": 15,
    "bump_price_drop_pct": 3,
    "bump_auto_price_drop": False,
    "min_price": 300,
    "listing_lifetime_days": 60,
    "llm_enabled": True,
    "vision_enabled": False,
    "shop_name": "Мой ресейл",
    "pvz": "",
    "market_file": "data/market.csv",
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return date.today().isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def init(self) -> "Database":
        with self.connect() as con:
            con.executescript(SCHEMA)
            for k, v in DEFAULT_SETTINGS.items():
                con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, json.dumps(v)))
        return self

    # ---------- settings ----------
    def settings(self) -> dict[str, Any]:
        with self.connect() as con:
            rows = con.execute("SELECT key, value FROM settings").fetchall()
        result = dict(DEFAULT_SETTINGS)
        for r in rows:
            try:
                result[r["key"]] = json.loads(r["value"])
            except ValueError:
                result[r["key"]] = r["value"]
        return result

    def set_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        with self.connect() as con:
            for k, v in patch.items():
                if k in allowed:
                    con.execute("INSERT INTO settings(key,value) VALUES (?,?) "
                                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (k, json.dumps(v, ensure_ascii=False)))
        return self.settings()

    # ---------- listings ----------
    def upsert_listing(self, item: dict[str, Any]) -> None:
        payload = {k: item.get(k) for k in LISTING_FIELDS}
        payload["id"] = item.get("id") or item.get("vendor_code")
        payload["vendor_code"] = item.get("vendor_code") or payload["id"]
        for k in ("keywords", "photos", "measurements"):
            v = payload.get(k)
            if isinstance(v, (list, dict)):
                payload[k] = json.dumps(v, ensure_ascii=False)
            elif v is None:
                payload[k] = "[]" if k != "measurements" else "{}"
        payload.setdefault("created_at", now())
        for key, default in FIELD_DEFAULTS.items():
            if payload.get(key) is None:
                payload[key] = default
        payload["created_at"] = payload.get("created_at") or now()
        payload["updated_at"] = now()
        cols = ",".join(LISTING_FIELDS)
        marks = ",".join("?" for _ in LISTING_FIELDS)
        updates = ",".join(f"{k}=excluded.{k}" for k in LISTING_FIELDS if k != "created_at")
        sql = f"INSERT INTO listings ({cols}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET {updates}"
        with self.connect() as con:
            con.execute(sql, [payload[k] for k in LISTING_FIELDS])

    def update_listing(self, listing_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        allowed = set(LISTING_FIELDS) - {"id", "created_at"}
        patch = {k: (FIELD_DEFAULTS.get(k, "") if v is None else v)
                 for k, v in patch.items() if k in allowed}
        if not patch:
            return self.get_listing(listing_id)
        for k in ("keywords", "photos", "measurements"):
            if k in patch and isinstance(patch[k], (list, dict)):
                patch[k] = json.dumps(patch[k], ensure_ascii=False)
        patch["updated_at"] = now()
        sets = ",".join(f"{k}=?" for k in patch)
        with self.connect() as con:
            con.execute(f"UPDATE listings SET {sets} WHERE id=?", [*patch.values(), listing_id])
        return self.get_listing(listing_id)

    def get_listing(self, listing_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        return self._row(row) if row else None

    def list_listings(self, status: str = "", q: str = "", limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM listings WHERE 1=1"
        args: list[Any] = []
        if status:
            sql += " AND status=?"
            args.append(status)
        if q:
            sql += " AND (vendor_code LIKE ? OR title LIKE ? OR brand LIKE ? OR color LIKE ? OR size LIKE ?)"
            args += [f"%{q}%"] * 5
        sql += " ORDER BY vendor_code LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self.connect() as con:
            rows = con.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def all_listings(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM listings ORDER BY vendor_code").fetchall()
        return [self._row(r) for r in rows]

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for k in ("keywords", "photos", "measurements"):
            try:
                d[k] = json.loads(d.get(k) or ("{}" if k == "measurements" else "[]"))
            except (ValueError, TypeError):
                d[k] = {} if k == "measurements" else []
        return d

    def stats(self) -> dict[str, Any]:
        with self.connect() as con:
            rows = con.execute("SELECT status, COUNT(*) c FROM listings GROUP BY status").fetchall()
            by_status = {r["status"]: r["c"] for r in rows}
            total = con.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"]
            ready_photos = con.execute("SELECT COUNT(*) c FROM listings WHERE photos != '[]' AND photos != ''").fetchone()["c"]
            with_desc = con.execute("SELECT COUNT(*) c FROM listings WHERE length(description) > 120").fetchone()["c"]
            due = con.execute("SELECT COUNT(*) c FROM bump_tasks WHERE status='pending' AND due_at<=?", (today(),)).fetchone()["c"]
            pending = con.execute("SELECT COUNT(*) c FROM bump_tasks WHERE status='pending'").fetchone()["c"]
            done = con.execute("SELECT COUNT(*) c FROM bump_tasks WHERE status='done'").fetchone()["c"]
            stock_value = con.execute("SELECT COALESCE(SUM(price),0) s FROM listings WHERE status IN ('ready','published')").fetchone()["s"]
        return {
            "total": total,
            "by_status": by_status,
            "with_photos": ready_photos,
            "with_description": with_desc,
            "bumps_due": due,
            "bumps_pending": pending,
            "bumps_done": done,
            "stock_value": stock_value,
            "generated_at": now(),
        }

    # ---------- bump tasks ----------
    def add_bump_task(self, listing_id: str, due_at: str, kind: str, suggested_price: int,
                      suggested_description: str, note: str) -> int:
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO bump_tasks(listing_id,due_at,kind,suggested_price,suggested_description,note,status,created_at) "
                "VALUES (?,?,?,?,?,?,'pending',?)",
                (listing_id, due_at, kind, suggested_price, suggested_description, note, now()))
            return int(cur.lastrowid)

    def bump_tasks(self, status: str = "", due_before: str = "", limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM bump_tasks WHERE 1=1"
        args: list[Any] = []
        if status:
            sql += " AND status=?"
            args.append(status)
        if due_before:
            sql += " AND due_at<=?"
            args.append(due_before)
        sql += " ORDER BY due_at, id LIMIT ?"
        args.append(limit)
        with self.connect() as con:
            rows = con.execute(sql, args).fetchall()
        tasks = [dict(r) for r in rows]
        if tasks:
            ids = {t["listing_id"] for t in tasks}
            with self.connect() as con:
                marks = ",".join("?" for _ in ids)
                lrows = con.execute(f"SELECT id,vendor_code,title,price,photo_dir,photos,wb_url FROM listings WHERE id IN ({marks})", list(ids)).fetchall()
            lookup = {r["id"]: dict(r) for r in lrows}
            for t in tasks:
                l = lookup.get(t["listing_id"], {})
                t["vendor_code"] = l.get("vendor_code", t["listing_id"])
                t["title"] = l.get("title", "")
                t["price"] = l.get("price", 0)
                t["wb_url"] = l.get("wb_url", "")
        return tasks

    def complete_bump_task(self, task_id: int, interval_days: int, new_price: int | None = None,
                           description: str | None = None) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM bump_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return None
            con.execute("UPDATE bump_tasks SET status='done', done_at=? WHERE id=?", (now(), task_id))
            patch: dict[str, Any] = {
                "last_bump_at": today(),
                "next_bump_at": (date.today() + timedelta(days=interval_days)).isoformat(),
                "bump_count": (con.execute("SELECT bump_count FROM listings WHERE id=?", (row["listing_id"],)).fetchone() or [0])[0] + 1,
            }
            if new_price:
                patch["price"] = new_price
            if description:
                patch["description"] = description
            sets = ",".join(f"{k}=?" for k in patch)
            con.execute(f"UPDATE listings SET {sets}, updated_at=? WHERE id=?", [*patch.values(), now(), row["listing_id"]])
            if description:
                con.execute("UPDATE listings SET variant=variant+1 WHERE id=?", (row["listing_id"],))
            result = con.execute("SELECT * FROM listings WHERE id=?", (row["listing_id"],)).fetchone()
        return dict(result) if result else None

    def skip_bump_task(self, task_id: int) -> None:
        with self.connect() as con:
            con.execute("UPDATE bump_tasks SET status='skipped', done_at=? WHERE id=?", (now(), task_id))

    def clear_pending_bumps(self) -> int:
        with self.connect() as con:
            cur = con.execute("DELETE FROM bump_tasks WHERE status='pending'")
            return cur.rowcount
