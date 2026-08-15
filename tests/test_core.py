import tempfile
import threading
import unittest
from pathlib import Path

from core import FOOTER_SIZE, process_file, scan_media, sha256


class CoreTests(unittest.TestCase):
    def test_copy_changes_hash_and_preserves_content(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); src = root / "sample.mp4"; dst = root / "out" / "sample.mp4"
            content = b"fake-media" * 10000; src.write_bytes(content)
            process_file(src, dst)
            self.assertEqual(src.read_bytes(), content)
            self.assertEqual(dst.read_bytes()[:-FOOTER_SIZE], content)
            self.assertNotEqual(sha256(src), sha256(dst))

    def test_reprocessing_replaces_footer(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.jpg"; path.write_bytes(b"image-data")
            process_file(path, path, in_place=True); first = path.stat().st_size; hash1 = sha256(path)
            process_file(path, path, in_place=True)
            self.assertEqual(path.stat().st_size, first)
            self.assertNotEqual(hash1, sha256(path))

    def test_scan_is_recursive_and_filters(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "sub").mkdir(); (root / "sub" / "a.PNG").write_bytes(b"x"); (root / "note.txt").write_text("x")
            files = scan_media(root)
            self.assertEqual([str(f.relative) for f in files], [str(Path("sub/a.PNG"))])


if __name__ == "__main__": unittest.main()

