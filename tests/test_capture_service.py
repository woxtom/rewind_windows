from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.capture_service import _compress_screenshot_for_thumbnail


class ThumbnailCompressionTests(unittest.TestCase):
    def test_compress_screenshot_for_thumbnail_reencodes_and_downscales(self) -> None:
        try:
            from PIL import Image
        except ModuleNotFoundError:
            self.skipTest("Pillow is not installed in this environment.")

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "capture.png"
            Image.new("RGB", (2400, 1600), color=(32, 64, 128)).save(source_path, format="PNG")

            output_path = _compress_screenshot_for_thumbnail(source_path)

            self.assertEqual(output_path.suffix.lower(), ".webp")
            self.assertTrue(output_path.exists())
            self.assertFalse(source_path.exists())

            with Image.open(output_path) as image:
                self.assertLessEqual(image.width, 960)
                self.assertLessEqual(image.height, 960)


if __name__ == "__main__":
    unittest.main()
