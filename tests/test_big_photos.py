"""Тесты сжатия «тяжёлых» фото под лимит WB (до 10 МБ)."""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".deps"))

from app import photos  # noqa: E402
from app.db import Database  # noqa: E402


def noisy_jpeg(path: Path, size=(1500, 2000), quality: int = 95) -> int:
    from PIL import Image
    w, h = size
    im = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    im.save(path, "JPEG", quality=quality)
    return path.stat().st_size


class CompressTests(unittest.TestCase):
    def test_compress_under_limit(self):
        if not photos.HAS_PIL:
            self.skipTest("Pillow не установлен")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "big.jpg"
            dst = Path(tmp) / "out.jpg"
            original = noisy_jpeg(src, (1400, 1900))
            limit = 180_000
            r = photos.compress_image(src, dst, max_bytes=limit)
            self.assertTrue(r["ok"], r["warnings"])
            self.assertLessEqual(dst.stat().st_size, limit)
            self.assertGreaterEqual(r["quality"], 65)
            self.assertGreaterEqual(r["width"], photos.MIN_SIZE[0])
            self.assertGreaterEqual(r["height"], photos.MIN_SIZE[1])
            self.assertLessEqual(r["width"], photos.RECOMMENDED_TARGET[0])
            self.assertLess(r["bytes"], original)
            self.assertLessEqual(r["bytes"], photos.MAX_BYTES)

    def test_process_folder_reports_totals(self):
        if not photos.HAS_PIL:
            self.skipTest("Pillow не установлен")
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "in", Path(tmp) / "out"
            src.mkdir()
            noisy_jpeg(src / "a.jpg", (1200, 1600))
            noisy_jpeg(src / "b.jpg", (1200, 1600))
            report = photos.process_folder(src, dst, max_bytes=200_000)
            self.assertEqual(len(report["photos"]), 2)
            self.assertTrue(report["ok"])
            self.assertGreater(report["total_bytes_before"], report["total_bytes_after"])
            self.assertLessEqual(report["max_photo_bytes"], 200_000)
            self.assertEqual(photos.list_photos(dst), ["01.jpg", "02.jpg"])
            for p in dst.glob("*.jpg"):
                self.assertLessEqual(p.stat().st_size, 200_000)

    def test_heic_graceful(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "x.heic"
            src.write_bytes(b"not a real heic")
            dst = Path(tmp) / "x.jpg"
            r = photos.compress_image(src, dst, max_bytes=200_000)
            self.assertFalse(r["ok"])
            self.assertTrue(r["warnings"])


class StandaloneToolTests(unittest.TestCase):
    def test_groups_subfolders_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Одежда"
            out = Path(tmp) / "Одежда_WB"
            for folder in ("Платье синее", "Платье красное"):
                (root / folder).mkdir(parents=True)
                for i in range(1, 3):
                    noisy_jpeg(root / folder / f"IMG_{i}.jpg", (900, 1200), quality=90)
            cmd = [sys.executable, str(ROOT / "tools" / "prepare_photos.py"),
                   "--input", str(root), "--output", str(out), "--max-mb", "0.3",
                   "--size", "900x1200"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Готово", r.stdout)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["groups"], 2)
            self.assertLess(manifest["max_file_bytes"], int(0.3 * 1048576))
            self.assertEqual(manifest["failures"], 0)
            self.assertTrue((out / "report.csv").exists())
            self.assertTrue((out / "inventory.csv").exists())
            self.assertIn("vendor_code", (out / "inventory.csv").read_text(encoding="utf-8-sig"))
            for f in out.glob("DR-*/*.jpg"):
                self.assertLessEqual(f.stat().st_size, int(0.3 * 1048576))

    def test_flat_grouping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "flat"
            out = Path(tmp) / "out"
            root.mkdir()
            for i in range(1, 8):
                noisy_jpeg(root / f"IMG_{i:03d}.jpg", (800, 1000), quality=85)
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "prepare_photos.py"),
                                "--input", str(root), "--output", str(out), "--group", "flat",
                                "--per-item", "3", "--max-mb", "0.3", "--size", "900x1200"],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["groups"], 3)  # 7 файлов по 3 = 3 пачки
            self.assertEqual(sum(s["photos"] for s in manifest["summary"]), 7)

    def test_default_flat_groups_are_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "flat", Path(tmp) / "out"
            root.mkdir()
            for i in range(4):
                noisy_jpeg(root / f"IMG_{i:03d}.jpg", (800, 1000), quality=85)
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "prepare_photos.py"),
                                "--input", str(root), "--output", str(out), "--max-mb", "0.3",
                                "--size", "900x1200"], capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["groups"], 2)
            self.assertEqual([s["photos"] for s in manifest["summary"]], [2, 2])

    def test_missing_folder(self):
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "prepare_photos.py"),
                            "--input", "/definitely/not/here", "--output", "/tmp/x"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("не найдена", r.stdout)


class UploadReadyApiTests(unittest.TestCase):
    def test_upload_ready_and_downloads(self):
        import app.server as srv

        with tempfile.TemporaryDirectory() as tmp:
            srv.DB = Database(Path(tmp) / "api.db").init()
            srv.DATA = Path(tmp)
            (Path(tmp) / "out").mkdir(exist_ok=True)
            server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                # минимальный валидный JPEG
                from PIL import Image
                buf = io.BytesIO()
                Image.new("RGB", (900, 1200), (210, 200, 220)).save(buf, "JPEG", quality=85)
                data = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
                payload = {"groups": [{"vendor_code": "DR-9001", "source_folder": "Платье синее",
                                        "files": [{"name": "a.jpg", "data": data, "original": 15_000_000},
                                                  {"name": "b.jpg", "data": data, "original": 12_000_000}]}]}
                req = urllib.request.Request(base + "/api/pipeline/upload-ready",
                                             data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=20) as resp:
                    r = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(r["ok"])
                self.assertEqual(r["groups"][0]["photos"], 2)
                self.assertGreater(r["total_before"], r["total_after"])
                item = srv.DB.get_listing("DR-9001")
                self.assertEqual(len(item["photos"]), 2)
                self.assertEqual(item["status"], "draft")
                for path in ("/tools/prepare_photos.py", "/prepare_photos.command"):
                    with urllib.request.urlopen(base + path, timeout=20) as resp:
                        self.assertEqual(resp.status, 200)
                        self.assertGreater(len(resp.read()), 500)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
