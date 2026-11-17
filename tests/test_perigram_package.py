import json
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from package_perigram_plugin import REQUIRED_FILES, package_plugin


class PerigramPluginPackageTests(unittest.TestCase):
    def test_archive_contains_listing_runtime_preview_and_pyodide_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            listing_path = root / "perigram-listing.json"
            output = root / "artifacts" / "plugin.zip"
            for relative in REQUIRED_FILES:
                target = dist / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("placeholder", encoding="utf-8")
            wheel = dist / "python/wheels/test-wheel.whl"
            wheel.write_bytes(b"wheel")
            (dist / "python/wheels/manifest.json").write_text(
                json.dumps([{"filename": "test-wheel.whl"}]),
                encoding="utf-8",
            )
            listing_path.write_text(json.dumps({
                "pluginId": "build123d-flow",
                "declarations": {
                    "runtimes": [{"entry": "/perigram-runtime.js"}],
                    "surfaces": [{"module": "/preview/build123d-preview.js"}],
                },
            }), encoding="utf-8")

            (root / "PERIGRAM_PLUGIN.md").write_text("Installation notes", encoding="utf-8")
            result = package_plugin(dist, listing_path, output)
            self.assertEqual(result, output)
            with ZipFile(output) as archive:
                names = set(archive.namelist())
            self.assertIn("listing.json", names)
            self.assertIn("README.md", names)
            self.assertIn("perigram-runtime.js", names)
            self.assertIn("preview/build123d-preview.js", names)
            self.assertIn("python/wheels/test-wheel.whl", names)
            self.assertIn("pyodide/v314.0.7/full/pyodide.mjs", names)

    def test_package_rejects_listing_that_points_at_missing_runtime_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            listing_path = root / "listing.json"
            dist.mkdir()
            listing_path.write_text(json.dumps({
                "declarations": {
                    "runtimes": [{"entry": "/wrong.js"}],
                    "surfaces": [{"module": "/preview/build123d-preview.js"}],
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must point at /perigram-runtime.js"):
                package_plugin(dist, listing_path, root / "out.zip")


if __name__ == "__main__":
    unittest.main()
