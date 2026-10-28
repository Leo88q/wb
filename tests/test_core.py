"""Запуск: python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import assistant, bump, descriptions, photos, pipeline, pricing, research, sizes
from app.db import Database


class SizeTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(sizes.normalize_size("44"), 44)
        self.assertEqual(sizes.normalize_size("M"), 44)
        self.assertEqual(sizes.normalize_size("XL"), 50)
        self.assertEqual(sizes.normalize_size("44-46"), 46)
        self.assertEqual(sizes.normalize_size("p. 48"), 48)
        self.assertIsNone(sizes.normalize_size(""))
        self.assertEqual(sizes.normalize_size("999"), 60)

    def test_size_row_and_grid(self):
        row = sizes.size_row(44)
        self.assertEqual(row["intl"], "M")
        self.assertEqual(row["chest"], 88)
        self.assertIn("| 44 | M |", sizes.size_grid_markdown([44, 46]))

    def test_measurements_and_recommendation(self):
        m = sizes.parse_measurements({"chest": "88,5", "waist": 68, "hips": "94"})
        self.assertAlmostEqual(m["chest"], 88.5)
        self.assertEqual(sizes.recommend_by_measurements({"chest": 96, "waist": 76, "hips": 102}), 48)
        block = sizes.size_block({"size": "44", "chest": 88, "waist": 68, "length": 112})
        self.assertIn("Замеры изделия", block)
        self.assertIn("Длина изделия: 112 см", block)


class DescriptionTests(unittest.TestCase):
    ITEM = {
        "vendor_code": "DR-001", "brand": "ZARA", "type": "платье миди", "color": "чёрный",
        "material": "вискоза", "size": "44", "condition": "отличное",
        "defects": "небольшая затяжка на подоле", "chest": 88, "waist": 68,
        "hips": 94, "length": 112, "price": 1900, "season": "демисезон", "style": "офисное",
    }

    def test_description_contains_facts(self):
        d = descriptions.build_description(self.ITEM)
        for frag in ("ZARA", "чёрный", "вискоза", "88", "94", "112", "затяжка", "Ключевые", "Wildberries"):
            self.assertIn(frag, d)
        self.assertNotIn("http", d)

    def test_missing_condition_and_defects_are_not_invented(self):
        item = {"vendor_code": "DR-UNKNOWN", "type": "платье"}
        d = descriptions.build_description(item)
        self.assertIn("Состояние не указано", d)
        self.assertIn("Информация о дефектах не заполнена", d)
        self.assertNotIn("Состояние отличное", d)
        self.assertNotIn("Дефектов и пятен нет", d)
        self.assertNotIn("Вещь чистая", d)

    def test_variants_are_unique(self):
        a = descriptions.build_description(self.ITEM, variant=0)
        b = descriptions.build_description(self.ITEM, variant=3)
        self.assertNotEqual(a, b)

    def test_offline_generation(self):
        r = descriptions.generate(self.ITEM, use_llm=False)
        self.assertEqual(r["source"], "шаблон (offline)")
        self.assertTrue(r["title"].startswith("Платье"))
        self.assertGreater(len(r["keywords"]), 5)

    def test_buyer_reply(self):
        reply = descriptions.draft_buyer_reply(self.ITEM, "Какие замеры?", use_llm=False)["reply"]
        self.assertIn("88", reply)
        reply2 = descriptions.draft_buyer_reply(self.ITEM, "Можно подешевле?", use_llm=False)["reply"]
        self.assertIn("Предложить цену", reply2)


class PricingTests(unittest.TestCase):
    def test_condition_affects_price(self):
        good = pricing.suggest_price({"condition": "отличное", "brand": "ZARA"})
        bad = pricing.suggest_price({"condition": "есть дефекты", "brand": "ZARA"})
        self.assertGreater(good["price"], bad["price"])

    def test_market_median(self):
        a = pricing.suggest_price({"condition": "отличное", "brand": ""}, market_median=2000)
        b = pricing.suggest_price({"condition": "отличное", "brand": ""})
        self.assertGreater(a["price"], b["price"])

    def test_next_price(self):
        self.assertLess(pricing.next_price(2000, 5), 2000)
        self.assertEqual(pricing.next_price(100, 5, floor=95), 95)


class ResearchTests(unittest.TestCase):
    CSV = "title;price;condition\nПлатье ZARA б/у;1000;отличное\nПлатье MANGO 44;2000;хорошее\nПлатье ZARA миди;3000;отличное\n"

    def test_parse_and_analyze(self):
        items = research.parse_market_csv(self.CSV)
        self.assertEqual(len(items), 3)
        a = research.analyze(items, query="zara")
        self.assertEqual(a["query_count"], 2)
        self.assertEqual(a["median"], 2000)
        self.assertEqual(a["min"], 1000)
        self.assertTrue(any(w["word"] == "платье" or w["word"] == "zara" for w in a["top_words"]))

    def test_russian_headers(self):
        items = research.parse_market_csv("Название;Цена;Состояние\nПлатье;1 500 ₽;отличное\n")
        self.assertEqual(items[0]["price"], 1500.0)


class DbAndPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "incoming" / "DR-001").mkdir(parents=True)
        # минимальный валидный JPEG через Pillow, если есть
        try:
            from PIL import Image
            Image.new("RGB", (700, 1000), (200, 190, 210)).save(self.data / "incoming" / "DR-001" / "a.jpg")
        except ImportError:
            (self.data / "incoming" / "DR-001" / "a.jpg").write_bytes(b"x")
        self.db = Database(self.data / "test.db").init()

    def tearDown(self):
        self.tmp.cleanup()

    CSV = ("vendor_code;brand;color;size;condition;defects;chest;waist;hips;length;price\n"
           "DR-001;ZARA;чёрный;44;отличное;;88;68;94;112;1900\n"
           "DR-002;MANGO;бежевый;M;хорошее;затяжка;88;70;96;100;1500\n"
           "DR-001;ZARA;чёрный;44;отличное;;88;68;94;112;2100\n")

    def test_import_and_exports(self):
        r = pipeline.import_inventory(self.db, self.CSV, self.data, use_llm=False)
        self.assertEqual(r["rows"], 2)
        self.assertEqual(r["created"], 2)
        items = self.db.list_listings()
        self.assertEqual(len(items), 2)
        first = self.db.get_listing("DR-001")
        self.assertEqual(first["price"], 2100)  # последняя строка победила
        self.assertGreater(len(first["description"]), 200)
        self.assertTrue(first["photos"], "фото должны обработаться")
        payload = pipeline.export_payload(self.db)
        self.assertEqual(len(payload), 2)
        self.assertIn("Размер", pipeline.export_csv(self.db))
        target = self.data / "e.zip"
        pipeline.export_zip(self.db, self.data, target)
        self.assertIn("listings.json", __import__("zipfile").ZipFile(target).namelist())

    def test_bump_queue(self):
        pipeline.import_inventory(self.db, self.CSV, self.data, use_llm=False)
        for l in self.db.all_listings():
            self.db.update_listing(l["id"], {"status": "published", "next_bump_at": "2020-01-01",
                                            "last_bump_at": "2020-01-01", "price": 2000, "min_price": 900})
        result = bump.generate_queue(self.db, per_day=2)
        self.assertEqual(result["created"], 2)
        tasks = self.db.bump_tasks(status="pending")
        self.assertEqual(len(tasks), 2)
        done = bump.complete(self.db, tasks[0]["id"])
        self.assertIsNotNone(done)
        self.assertGreater(done["bump_count"], 0)
        self.assertEqual(self.db.stats()["bumps_done"], 1)
        self.assertTrue(bump.calendar(self.db, 5))

    def test_assistant_intents(self):
        pipeline.import_inventory(self.db, self.CSV, self.data, use_llm=False)
        a = assistant.answer(self.db, "Как поднять карточки?", mode="seller", data_dir=self.data)
        self.assertIn("редактирование", a["reply"])
        b = assistant.answer(self.db, "Какие замеры?", listing_id="DR-001", mode="buyer")
        self.assertIn("88", b["reply"])
        c = assistant.answer(self.db, "Что делать сегодня?", data_dir=self.data)
        self.assertIn("Сегодня", c["reply"])

    def test_photo_processing_idempotent(self):
        r1 = photos.process_folder(self.data / "incoming" / "DR-001", self.data / "out" / "DR-001")
        r2 = photos.process_folder(self.data / "incoming" / "DR-001", self.data / "out" / "DR-001")
        self.assertEqual(len(r1["photos"]), len(r2["photos"]))
        self.assertEqual(photos.list_photos(self.data / "out" / "DR-001")[0], "01.jpg")


class ApiSmokeTests(unittest.TestCase):
    def test_endpoints(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        import app.server as srv

        with tempfile.TemporaryDirectory() as tmp:
            srv.DB = Database(Path(tmp) / "api.db").init()
            srv.DATA = Path(tmp)
            server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                def get(path):
                    with urllib.request.urlopen(base + path, timeout=5) as r:
                        return json.loads(r.read().decode("utf-8"))
                self.assertTrue(get("/api/health")["ok"])
                self.assertEqual(get("/api/stats")["total"], 0)
                self.assertIn("markdown", get("/api/sizes/grid"))
                req = urllib.request.Request(base + "/api/pipeline/import",
                    data=json.dumps({"csv": DbAndPipelineTests.CSV, "use_llm": False,
                                     "process_photos": False}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=10) as r:
                    rsp = json.loads(r.read().decode("utf-8"))
                self.assertEqual(rsp["created"], 2)
                self.assertEqual(len(get("/api/listings")["items"]), 2)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
