"""A prior PCG mapping must not replace a freshly captured Cluster card."""
import hashlib
import importlib.util
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from test_speedtree_xml import speedtree, PACKAGE_DIR

spec = importlib.util.spec_from_file_location(
    "capture_priority_texture_paths", PACKAGE_DIR / "texture_paths.py"
)
texture_paths = importlib.util.module_from_spec(spec)
spec.loader.exec_module(texture_paths)


class ClusterCaptureTexturePriorityTests(unittest.TestCase):
    def test_verified_capture_wins_over_existing_canonical_and_repeats(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            color = root / "branch.tga"
            color.write_bytes(b"new physical branch capture")
            blend = root / "SK_branch.blend"
            group = {"material": "M_branch", "objects": [types.SimpleNamespace(
                name="branch_01", material_slots=[types.SimpleNamespace(
                    material=types.SimpleNamespace(name="M_branch"))]) ]}
            receipt = {
                "workflow_mode": "PHYSICAL_DIRECT_CAPTURE",
                "physical_capture_contract_sha256": "capture",
                "physical_capture_contract": {
                    "contract_sha256": "capture", "source_blend": str(blend),
                    "capture_maps": [{"role": "Color", "path": str(color),
                        "sha256": hashlib.sha256(color.read_bytes()).hexdigest()}],
                },
            }
            with patch.object(speedtree, "validate_source_texture_fallback",
                              texture_paths.validate_source_texture_fallback), \
                 patch.object(speedtree, "resolve_production_texture_contract",
                              return_value={"texture_contract_status": "canonical_pcg_output",
                                            "files": {"color": root / "T_old_bark_color.tga"}}) as old:
                for _ in range(2):
                    result = speedtree.resolve_group_production_texture_contract(
                        root / "SK_tree.spm", group, 3, {"albedo": color}, receipt,
                        blend_file=blend)
                    self.assertEqual(result["texture_contract_status"], "blender_cluster_bake")
                    self.assertEqual(result["source_paths"]["albedo"], color)
                old.assert_not_called()
                color.write_bytes(b"changed outside the capture")
                with self.assertRaisesRegex(RuntimeError, "texture is stale"):
                    speedtree.resolve_group_production_texture_contract(
                        root / "SK_tree.spm", group, 3, {"albedo": color}, receipt,
                        blend_file=blend)
                old.assert_not_called()

    def test_ordinary_atlas_keeps_canonical_selection(self):
        expected = {"texture_contract_status": "canonical_pcg_output"}
        with patch.object(speedtree, "resolve_production_texture_contract",
                          return_value=expected) as canonical:
            self.assertIs(speedtree.resolve_group_production_texture_contract(
                "tree.spm", {"material": "M_leaf"}, 10, {}, None), expected)
            canonical.assert_called_once()

    def test_unproven_physical_capture_cannot_silently_select_old_textures(self):
        with patch.object(speedtree, "validate_source_texture_fallback", return_value={}), \
             patch.object(speedtree, "resolve_production_texture_contract") as canonical:
            with self.assertRaisesRegex(RuntimeError, "origin is not proven"):
                speedtree.resolve_group_production_texture_contract(
                    "tree.spm", {"material": "M_branch"}, 3, {},
                    {"workflow_mode": "PHYSICAL_DIRECT_CAPTURE"})
            canonical.assert_not_called()


if __name__ == "__main__":
    unittest.main()
