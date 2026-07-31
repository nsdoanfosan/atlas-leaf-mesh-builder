import importlib.util
import os
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "addons" / "atlas_leaf_mesh_builder" / "source_index.py"
SPEC = importlib.util.spec_from_file_location("atlas_source_index_test", MODULE_PATH)
SOURCE_INDEX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE_INDEX)


class _Image:
    def __init__(self, name, filepath="", packed=False):
        self.name = name
        self.filepath = filepath
        self.library = None
        self.packed_file = object() if packed else None


class _BpyPath:
    @staticmethod
    def abspath(value, library=None):
        del library
        return value


class SourceIndexTests(unittest.TestCase):
    def _bpy(self, blend, *, dirty=False, images=()):
        return types.SimpleNamespace(
            data=types.SimpleNamespace(
                filepath=str(blend),
                is_dirty=dirty,
                images=list(images),
            ),
            path=_BpyPath(),
        )

    def test_indexes_exact_saved_current_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blend = root / "M_leaf.blend"
            blend.write_bytes(b"BLENDER-authoritative-content")
            albedo = root / "Leaf_Color.png"
            alpha = root / "Leaf_Opacity.png"
            bpy = self._bpy(
                blend,
                images=(
                    _Image("Leaf_Color.png", str(albedo)),
                    _Image("Leaf_Opacity.png", str(alpha), packed=True),
                ),
            )
            row = SOURCE_INDEX.current_blend_source_index(
                expected_blend_path=blend,
                bpy_module=bpy,
            )
            self.assertEqual(row["status"], "ok")
            self.assertTrue(row["indexed_by_blender"])
            self.assertEqual(row["blend_sha256"], SOURCE_INDEX.file_sha256(blend))
            self.assertEqual(row["image_count"], 2)
            self.assertEqual(
                [entry["name"] for entry in row["images"]],
                ["Leaf_Color.png", "Leaf_Opacity.png"],
            )

    def test_dirty_or_wrong_current_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = root / "expected.blend"
            opened = root / "opened.blend"
            expected.write_bytes(b"A")
            opened.write_bytes(b"B")
            with self.assertRaises(SOURCE_INDEX.BlendSourceIndexError):
                SOURCE_INDEX.current_blend_source_index(
                    expected_blend_path=expected,
                    bpy_module=self._bpy(opened),
                )
            with self.assertRaises(SOURCE_INDEX.BlendSourceIndexError):
                SOURCE_INDEX.current_blend_source_index(
                    expected_blend_path=expected,
                    bpy_module=self._bpy(expected, dirty=True),
                )

    def test_same_size_and_restored_mtime_do_not_authorize_old_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            blend = Path(temp_dir) / "same-size.blend"
            blend.write_bytes(b"AAAA")
            original_stat = blend.stat()
            first = SOURCE_INDEX.file_sha256(blend)
            blend.write_bytes(b"BBBB")
            os.utime(
                blend,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            second = SOURCE_INDEX.file_sha256(blend)
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
