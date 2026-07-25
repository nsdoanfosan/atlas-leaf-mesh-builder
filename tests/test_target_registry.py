import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "addons" / "atlas_leaf_mesh_builder" / "target_registry.py"
SPEC = importlib.util.spec_from_file_location("atlas_leaf_target_registry_test", MODULE_PATH)
registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry)


class TargetRegistryTests(unittest.TestCase):
    def test_round_trip_uses_per_blend_sidecar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blend = root / "M_leaf_test_atlas_01.blend"
            blend.touch()
            targets = [
                root / "Tree" / "SK_Tree_01.spm",
                root / "Tree" / "Cluster" / "leaf_01.spm",
            ]

            saved = registry.save_target_registry(blend, targets)
            loaded = registry.load_target_registry(blend)

            self.assertEqual(
                Path(saved["registry_path"]).name,
                "M_leaf_test_atlas_01.atlas_leaf_targets.json",
            )
            self.assertEqual(loaded["target_spms"], [
                str(path.absolute()) for path in targets
            ])

    def test_duplicate_paths_are_removed_case_insensitively(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blend = root / "M_leaf_test.blend"
            blend.touch()
            target = root / "SK_Tree_01.spm"

            saved = registry.save_target_registry(
                blend, [target, Path(str(target).upper())]
            )

            self.assertEqual(len(saved["target_spms"]), 1)

    def test_access_denied_after_committed_bytes_is_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blend = root / "M_leaf_test.blend"
            blend.touch()
            destination = registry.registry_path_for_blend(blend)
            access_denied = PermissionError(13, "OneDrive access denied")

            def committed_then_denied(source, target):
                Path(target).write_bytes(Path(source).read_bytes())
                raise access_denied

            with mock.patch.object(
                registry.os, "replace", side_effect=committed_then_denied
            ) as replace, mock.patch.object(registry.time, "sleep") as sleep:
                saved = registry.save_target_registry(
                    blend, [root / "Tree" / "SK_Tree_01.spm"]
                )

            self.assertEqual(replace.call_count, 1)
            sleep.assert_not_called()
            self.assertFalse(Path(str(destination) + ".tmp").exists())
            self.assertEqual(
                registry.load_target_registry(blend)["target_spms"],
                saved["target_spms"],
            )

    def test_access_denied_with_different_bytes_retries_then_raises_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blend = root / "M_leaf_test.blend"
            blend.touch()
            destination = registry.registry_path_for_blend(blend)
            destination.write_bytes(b"old registry bytes")
            access_denied = PermissionError(13, "OneDrive access denied")

            with mock.patch.object(
                registry.os, "replace", side_effect=access_denied
            ) as replace, mock.patch.object(registry.time, "sleep") as sleep:
                with self.assertRaises(PermissionError) as caught:
                    registry.save_target_registry(
                        blend, [root / "Tree" / "SK_Tree_01.spm"]
                    )

            self.assertIs(caught.exception, access_denied)
            self.assertEqual(replace.call_count, registry.REPLACE_MAX_ATTEMPTS)
            self.assertEqual(sleep.call_count, registry.REPLACE_MAX_ATTEMPTS - 1)
            self.assertEqual(destination.read_bytes(), b"old registry bytes")
            self.assertTrue(Path(str(destination) + ".tmp").is_file())

    def test_winerror_five_retries_and_can_succeed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blend = root / "M_leaf_test.blend"
            blend.touch()
            winerror_five = OSError("OneDrive access denied")
            winerror_five.winerror = 5
            real_replace = registry.os.replace
            attempts = []

            def denied_then_replaced(source, target):
                attempts.append((source, target))
                if len(attempts) == 1:
                    raise winerror_five
                return real_replace(source, target)

            with mock.patch.object(
                registry.os, "replace", side_effect=denied_then_replaced
            ) as replace, mock.patch.object(registry.time, "sleep") as sleep:
                saved = registry.save_target_registry(
                    blend, [root / "Tree" / "SK_Tree_01.spm"]
                )

            self.assertEqual(replace.call_count, 2)
            sleep.assert_called_once_with(registry.REPLACE_RETRY_DELAY_SECONDS)
            self.assertEqual(
                registry.load_target_registry(blend)["target_spms"],
                saved["target_spms"],
            )

    def test_unrelated_replace_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blend = root / "M_leaf_test.blend"
            blend.touch()
            unrelated = OSError(28, "No space left on device")

            with mock.patch.object(
                registry.os, "replace", side_effect=unrelated
            ) as replace, mock.patch.object(registry.time, "sleep") as sleep:
                with self.assertRaises(OSError) as caught:
                    registry.save_target_registry(
                        blend, [root / "Tree" / "SK_Tree_01.spm"]
                    )

            self.assertIs(caught.exception, unrelated)
            self.assertEqual(replace.call_count, 1)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
