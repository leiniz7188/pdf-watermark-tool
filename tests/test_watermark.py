"""Integration checks: native pixels, flattening, geometry, and failure isolation."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import fitz
from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pdf_watermark as wm


class WatermarkIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.config = wm.DEFAULTS.copy()
        self.font = wm.find_font(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def make_image_pdf(self, name="input.pdf"):
        image = Image.new("RGB", (420, 594), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((15, 15, 405, 579), outline="#2288cc", width=3)
        draw.text((25, 50), "Original English text 123", fill="black")
        stream = BytesIO()
        image.save(stream, format="PNG")
        path = self.folder / name
        with fitz.open() as doc:
            p = doc.new_page(width=595, height=842)
            p.insert_image(p.rect, stream=stream.getvalue(), keep_proportion=False)
            doc.set_metadata({"author": "Fixture author", "title": "Fixture"})
            doc.set_toc([[1, "First page", 1]])
            doc.save(path)
        return path, image

    def test_native_pixels_centered_watermark_and_source_unchanged(self):
        source, original = self.make_image_pdf()
        before = hashlib.sha256(source.read_bytes()).digest()
        dest = self.folder / "output.pdf"
        report = wm.process_pdf(source, dest, self.config, self.font)
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
        self.assertEqual(report["pages"][0]["method"], "保留原图像素")
        with fitz.open(dest) as doc:
            page = doc[0]
            self.assertEqual(tuple(page.rect), (0, 0, 595, 842))
            self.assertEqual(page.get_text(), "")
            self.assertEqual(len(page.get_images()), 1)
            embedded = doc.extract_image(page.get_images()[0][0])
            final = Image.open(BytesIO(embedded["image"])).convert("RGB")
            self.assertEqual(final.size, original.size)
            self.assertEqual(doc.metadata["author"], "Fixture author")
            self.assertEqual(doc.get_toc(), [[1, "First page", 1]])
        bbox = ImageChops.difference(original, final).getbbox()
        self.assertIsNotNone(bbox)
        left, top, right, bottom = bbox
        self.assertLess(abs((left + right) / 2 - original.width / 2), 3)
        self.assertLess(abs((top + bottom) / 2 - original.height / 2), 3)
        self.assertGreater(top, original.height * 0.25)
        self.assertLess(bottom, original.height * 0.75)
        self.assertEqual(original.crop((0, 0, 420, 100)).tobytes(), final.crop((0, 0, 420, 100)).tobytes())
        with self.assertRaises(ValueError):
            wm.process_pdf(source, source, self.config, self.font)
        with self.assertRaises(ValueError):
            wm.process_pdf(source, dest, self.config, self.font)

    def test_vector_and_rotated_pages_keep_visible_geometry(self):
        source = self.folder / "vector.pdf"
        with fitz.open() as doc:
            doc.new_page(width=240, height=160).insert_text((20, 40), "Landscape vector text")
            rotated = doc.new_page(width=160, height=240)
            rotated.insert_text((20, 40), "Rotated vector text")
            rotated.set_rotation(90)
            doc.save(source)
        dest = self.folder / "flattened.pdf"
        record = wm.process_pdf(source, dest, self.config, self.font)
        self.assertEqual(len(record["pages"]), 2)
        with fitz.open(source) as original, fitz.open(dest) as output:
            for before, after in zip(original, output):
                self.assertEqual(tuple(before.rect), tuple(after.rect))
                self.assertEqual(after.rotation, 0)
                info = output.extract_image(after.get_images()[0][0])
                self.assertEqual((info["width"], info["height"]), (1000, 667))
                before_image = wm.page_image(before, 300)[0]
                after_image = Image.open(BytesIO(info["image"])).convert("RGB")
                self.assertEqual(before_image.crop((0, 0, 1000, 100)).tobytes(), after_image.crop((0, 0, 1000, 100)).tobytes())

    def test_corrupt_and_encrypted_files_do_not_stop_batch(self):
        good, _ = self.make_image_pdf()
        broken = self.folder / "broken.pdf"
        broken.write_bytes(b"Not a PDF")
        locked = self.folder / "locked.pdf"
        with fitz.open() as doc:
            doc.new_page()
            doc.save(locked, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner-test", user_pw="user-test")
        folder, records = wm.run_batch([broken, locked, good], self.folder / "results", self.config, self.font)
        self.assertEqual([r["status"] for r in records], ["error", "error", "ok"])
        self.assertIn("密码", records[1]["error"])
        self.assertTrue(Path(records[2]["output"]).is_file())
        self.assertEqual(len(list(folder.glob("*.pdf"))), 1)
        self.assertEqual(len(json.loads((folder / "处理记录.json").read_text(encoding="utf-8"))["files"]), 3)
        self.assertFalse(list(folder.glob(".watermark-*")))

    def test_input_deduplication_and_unique_output_folders(self):
        source, _ = self.make_image_pdf()
        sub = self.folder / "already_processed"
        sub.mkdir()
        (sub / "nested.pdf").write_bytes(source.read_bytes())
        inputs = wm.collect_pdfs([self.folder, source])
        self.assertEqual(inputs, [source])
        first = wm.make_output_dir(self.folder)
        second = wm.make_output_dir(self.folder)
        self.assertNotEqual(first, second)

    def test_configuration_validation(self):
        path = self.folder / "config.json"
        for invalid in ({"opacity": 0}, {"opacity": True}, {"color": "blue"}, {"primary_line": ""}, {"wrong": 1}):
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(ValueError):
                wm.load_settings(path)
        path.write_text(json.dumps({"opacity": 0.08, "secondary_line": "仅供学习"}), encoding="utf-8-sig")
        self.assertEqual(wm.load_settings(path)["opacity"], 0.08)

    def test_native_picker_returns_selected_files(self):
        import tkinter as tk

        source, _ = self.make_image_pdf()
        real_mainloop = tk.Tk.mainloop

        def choose_files(root):
            # Exercise the actual native window and button without opening a dialog.
            root.withdraw()
            row = next(child for child in root.winfo_children() if isinstance(child, tk.Frame))
            button = row.winfo_children()[0]
            root.after(0, button.invoke)
            real_mainloop(root)

        with patch("tkinter.Tk.mainloop", choose_files), patch(
                "tkinter.filedialog.askopenfilenames", return_value=(str(source),)):
            self.assertEqual(wm.select_inputs(), [source])


if __name__ == "__main__":
    unittest.main()
