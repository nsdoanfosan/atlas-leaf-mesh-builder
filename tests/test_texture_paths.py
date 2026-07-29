import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "addons" / "atlas_leaf_mesh_builder" / "texture_paths.py"


def load_texture_paths_module():
    spec = importlib.util.spec_from_file_location("atlas_texture_paths_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


texture_paths = load_texture_paths_module()


class AtlasTexturePathTests(unittest.TestCase):
    def _write_canonical_manifest(self, root, outputs):
        asset = Path(root) / "tree_asset"
        texture = asset / "texture"
        cluster = asset / "cluster"
        texture.mkdir(parents=True)
        cluster.mkdir()
        target = cluster / "SK_leaf_test_01.spm"
        target.touch()
        payload_outputs = []
        for output in outputs:
            texture_base = output["texture_base"]
            files = {}
            for role in texture_paths.CANONICAL_LEAF_ROLES:
                path = texture / f"{texture_base}_{role}.tga"
                if role not in output.get("missing_roles", ()):
                    path.touch()
                files[role] = path.name
            payload_outputs.append(
                {
                    "texture_base": texture_base,
                    "required_roles": list(
                        output.get(
                            "required_roles",
                            texture_paths.CANONICAL_LEAF_ROLES,
                        )
                    ),
                    "files": files,
                    "material_targets": [
                        {
                            "spm": str(target.relative_to(asset)),
                            "material_id": output["material_id"],
                            "material_name": output["material_name"],
                        }
                    ],
                    "producer": {
                        "tool": "PCG ST9 Texture",
                        "source": "test.sbs",
                    },
                }
            )
        manifest = texture / texture_paths.CANONICAL_OUTPUT_MANIFEST
        manifest.write_text(
            json.dumps(
                {
                    "kind": texture_paths.CANONICAL_OUTPUT_KIND,
                    "schema_version": (
                        texture_paths.CANONICAL_OUTPUT_SCHEMA_VERSION
                    ),
                    "asset_root": str(asset),
                    "texture_root": str(texture),
                    "outputs": payload_outputs,
                }
            ),
            encoding="utf-8",
        )
        return asset, texture, target, manifest

    def test_plain_albedo_finds_gloss_and_ao_siblings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            albedo = root / "branch_elm_01.tga"
            gloss = root / "branch_elm_01_Gloss.tga"
            ao = root / "branch_elm_01_AO.tga"
            subsurface = root / "branch_elm_01_Subsurface.tga"
            subsurface_amount = (
                root / "branch_elm_01_SubsurfaceAmount.tga"
            )
            unrelated = root / "branch_scotspine_01_AO.tga"
            for path in (
                albedo,
                gloss,
                ao,
                subsurface,
                subsurface_amount,
                unrelated,
            ):
                path.touch()

            result = texture_paths.atlas_texture_paths(albedo)

            self.assertEqual(result["gloss"], gloss)
            self.assertEqual(result["ao"], ao)
            self.assertEqual(result["translucency"], subsurface)
            self.assertEqual(
                result["subsurface_amount"],
                subsurface_amount,
            )

    def test_role_tagged_albedo_uses_same_base_for_gloss_and_ambient_occlusion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            albedo = root / "TCom_Leaves_Elm01_4K_albedo.tif"
            gloss = root / "TCom_Leaves_Elm01_4K_smoothness.png"
            ao = root / "TCom_Leaves_Elm01_4K_ambient_occlusion.exr"
            for path in (albedo, gloss, ao):
                path.touch()

            result = texture_paths.atlas_texture_paths(albedo)

            self.assertEqual(result["gloss"], gloss)
            self.assertEqual(result["ao"], ao)

    def test_short_ao_token_requires_a_filename_token_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            albedo = root / "branch_elm_01.tga"
            false_positive = root / "branch_elm_01_chaos.tga"
            albedo.touch()
            false_positive.touch()

            result = texture_paths.atlas_texture_paths(albedo)

            self.assertNotIn("ao", result)

    def test_canonical_manifest_resolves_complete_asset_local_t_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            _asset, texture, target, manifest = (
                self._write_canonical_manifest(
                    folder,
                    [
                        {
                            "texture_base": "T_leaf_test_atlas_01",
                            "material_id": 8,
                            "material_name": "M_leaf_test_atlas_01",
                        }
                    ],
                )
            )

            result = texture_paths.resolve_canonical_texture_output(
                target,
                "M_leaf_test_atlas_01",
                8,
            )

            self.assertEqual(result["manifest_path"], manifest)
            self.assertEqual(
                set(result["files"]),
                set(texture_paths.CANONICAL_LEAF_ROLES),
            )
            self.assertTrue(
                all(path.parent == texture for path in result["files"].values())
            )

    def test_material_id_wins_over_material_name_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            _asset, _texture, target, _manifest = (
                self._write_canonical_manifest(
                    folder,
                    [
                        {
                            "texture_base": "T_leaf_by_id",
                            "material_id": 7,
                            "material_name": "M_old_name",
                        },
                        {
                            "texture_base": "T_leaf_by_name",
                            "material_id": 8,
                            "material_name": "M_leaf_test",
                        },
                    ],
                )
            )

            result = texture_paths.resolve_canonical_texture_output(
                target,
                "M_leaf_test",
                7,
            )

            self.assertEqual(result["texture_base"], "T_leaf_by_id")

    def test_missing_role_reports_material_role_expected_path_and_action(self):
        with tempfile.TemporaryDirectory() as folder:
            _asset, texture, target, _manifest = (
                self._write_canonical_manifest(
                    folder,
                    [
                        {
                            "texture_base": "T_leaf_missing",
                            "material_id": 8,
                            "material_name": "M_leaf_missing",
                            "missing_roles": {"normal"},
                        }
                    ],
                )
            )

            with self.assertRaises(RuntimeError) as raised:
                texture_paths.resolve_canonical_texture_output(
                    target,
                    "M_leaf_missing",
                    8,
                )

            message = str(raised.exception)
            self.assertIn("material=M_leaf_missing", message)
            self.assertIn("role=normal", message)
            self.assertIn(
                str(texture / "T_leaf_missing_normal.tga"),
                message,
            )
            self.assertIn("PCG ST9 Texture에서 생성", message)

    def test_manifest_files_cannot_point_at_original_or_external_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            _asset, _texture, _target, manifest = (
                self._write_canonical_manifest(
                    folder,
                    [
                        {
                            "texture_base": "T_leaf_external",
                            "material_id": 8,
                            "material_name": "M_leaf_external",
                        }
                    ],
                )
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["outputs"][0]["files"]["color"] = str(
                Path(folder) / "original" / "T_leaf_external_color.tga"
            )
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                RuntimeError,
                "must be manifest-relative",
            ):
                texture_paths.load_canonical_output_manifest(manifest)

    def test_optional_generated_ao_is_allowed_only_under_generated_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            _asset, texture, target, manifest = (
                self._write_canonical_manifest(
                    folder,
                    [
                        {
                            "texture_base": "T_leaf_generated",
                            "material_id": 8,
                            "material_name": "M_leaf_generated",
                        }
                    ],
                )
            )
            generated = texture / "_pcgtex_generated"
            generated.mkdir()
            ao = generated / "T_leaf_generated_ao_from_height.png"
            ao.touch()
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["outputs"][0]["files"]["ao"] = str(
                ao.relative_to(texture)
            )
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            result = texture_paths.resolve_canonical_texture_output(
                target,
                "M_leaf_generated",
                8,
            )

            self.assertEqual(result["files"]["ao"], ao)

    def test_isolated_cache_target_is_not_a_production_spm(self):
        with tempfile.TemporaryDirectory() as folder:
            target = (
                Path(folder)
                / ".sk_batch_isolated_bark"
                / "scope"
                / "SK_leaf_test.spm"
            )
            target.parent.mkdir(parents=True)
            target.touch()

            with self.assertRaisesRegex(
                RuntimeError,
                "cannot be an isolated/temp/cache target",
            ):
                texture_paths.resolve_canonical_texture_output(
                    target,
                    "M_leaf_test",
                    8,
                )

    def test_missing_manifest_never_falls_back_to_source_siblings(self):
        with tempfile.TemporaryDirectory() as folder:
            asset = Path(folder) / "tree"
            target = asset / "cluster" / "SK_leaf_test.spm"
            target.parent.mkdir(parents=True)
            target.touch()
            source = asset / "texture" / "Leaf" / "TCom_Leaf_Albedo.tif"
            source.parent.mkdir(parents=True)
            source.touch()

            with self.assertRaises(RuntimeError) as raised:
                texture_paths.resolve_canonical_texture_output(
                    target,
                    "M_leaf_test",
                    8,
                )

            message = str(raised.exception)
            self.assertIn("Canonical T_* output manifest is missing", message)
            self.assertIn("material=M_leaf_test", message)
            self.assertIn("PCG ST9 Texture에서 생성", message)

    def test_production_contract_prefers_canonical_over_available_source(self):
        with tempfile.TemporaryDirectory() as folder:
            _asset, _texture, target, _manifest = (
                self._write_canonical_manifest(
                    folder,
                    [
                        {
                            "texture_base": "T_leaf_priority",
                            "material_id": 8,
                            "material_name": "M_leaf_priority",
                        }
                    ],
                )
            )
            source = Path(folder) / "original" / "Leaf_Albedo.tif"
            source.parent.mkdir()
            source.touch()

            result = texture_paths.resolve_production_texture_contract(
                target,
                "M_leaf_priority",
                8,
                source_paths={"albedo": source},
            )

            self.assertEqual(
                result["texture_contract_status"],
                texture_paths.CANONICAL_TEXTURE_STATUS,
            )
            self.assertEqual(
                result["canonical_output"]["texture_base"],
                "T_leaf_priority",
            )
            self.assertNotIn("source_paths", result)

    def test_irrelevant_manifest_output_does_not_block_source_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            _asset, _texture, target, _manifest = (
                self._write_canonical_manifest(
                    folder,
                    [{
                        "texture_base": "T_leaf_other",
                        "material_id": 9,
                        "material_name": "M_leaf_other",
                    }],
                )
            )
            source = Path(folder) / "capture" / "cluster_color.tga"
            source.parent.mkdir()
            source.touch()

            result = texture_paths.resolve_production_texture_contract(
                target,
                "M_cluster_requested",
                8,
                source_paths={"albedo": source},
            )

            self.assertEqual(
                result["texture_contract_status"],
                texture_paths.SOURCE_FALLBACK_STATUS,
            )
            self.assertEqual(result["source_paths"]["albedo"], source)

    def test_relevant_malformed_output_still_blocks_source_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            _asset, _texture, target, manifest = (
                self._write_canonical_manifest(
                    folder,
                    [{
                        "texture_base": "T_leaf_requested",
                        "material_id": 8,
                        "material_name": "M_leaf_requested",
                    }],
                )
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["outputs"][0]["producer"] = {}
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            source = Path(folder) / "capture" / "cluster_color.tga"
            source.parent.mkdir()
            source.touch()

            with self.assertRaisesRegex(
                RuntimeError,
                "producer.tool/source are required",
            ):
                texture_paths.resolve_production_texture_contract(
                    target,
                    "M_leaf_requested",
                    8,
                    source_paths={"albedo": source},
                )

    def test_irrelevant_malformed_output_does_not_hide_valid_exact_output(self):
        with tempfile.TemporaryDirectory() as folder:
            _asset, _texture, target, manifest = (
                self._write_canonical_manifest(
                    folder,
                    [
                        {
                            "texture_base": "T_leaf_requested",
                            "material_id": 8,
                            "material_name": "M_leaf_requested",
                        },
                        {
                            "texture_base": "T_leaf_other",
                            "material_id": 9,
                            "material_name": "M_leaf_other",
                        },
                    ],
                )
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["outputs"][1]["producer"] = {}
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            result = texture_paths.resolve_production_texture_contract(
                target,
                "M_leaf_requested",
                8,
                source_paths={},
            )

            self.assertEqual(
                result["canonical_output"]["texture_base"],
                "T_leaf_requested",
            )

    def test_absent_canonical_uses_structured_provisional_source_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            asset = Path(folder) / "tree_asset"
            target = asset / "cluster" / "SK_leaf_test.spm"
            target.parent.mkdir(parents=True)
            target.touch()
            source = Path(folder) / "original"
            source.mkdir()
            albedo = source / "Leaf_Albedo.tif"
            alpha = source / "Leaf_Opacity.tif"
            albedo.touch()
            alpha.touch()

            result = texture_paths.resolve_production_texture_contract(
                target,
                "M_leaf_test",
                8,
                source_paths={"albedo": albedo, "alpha": alpha},
            )

            self.assertEqual(
                result["texture_contract_status"],
                texture_paths.SOURCE_FALLBACK_STATUS,
            )
            self.assertEqual(
                result["source_paths"],
                {"albedo": albedo, "alpha": alpha},
            )
            self.assertEqual(result["source_roles"], ["albedo", "alpha"])
            self.assertEqual(
                result["expected_t_paths"]["color"],
                asset / "texture" / "T_leaf_test_color.tga",
            )
            self.assertEqual(
                result["remediation"],
                texture_paths.SOURCE_FALLBACK_REMEDIATION,
            )
            self.assertIn("provisionally", result["warning"])

    def test_provisional_source_is_promoted_when_manifest_appears(self):
        with tempfile.TemporaryDirectory() as folder:
            _asset, _texture, target, manifest = (
                self._write_canonical_manifest(
                    folder,
                    [
                        {
                            "texture_base": "T_leaf_promote",
                            "material_id": 8,
                            "material_name": "M_leaf_promote",
                        }
                    ],
                )
            )
            manifest_payload = manifest.read_text(encoding="utf-8")
            manifest.unlink()
            source = Path(folder) / "original" / "Leaf_Albedo.tif"
            source.parent.mkdir()
            source.touch()

            provisional = (
                texture_paths.resolve_production_texture_contract(
                    target,
                    "M_leaf_promote",
                    8,
                    source_paths={"albedo": source},
                )
            )
            manifest.write_text(manifest_payload, encoding="utf-8")
            promoted = texture_paths.resolve_production_texture_contract(
                target,
                "M_leaf_promote",
                8,
                source_paths={"albedo": source},
            )

            self.assertEqual(
                provisional["texture_contract_status"],
                texture_paths.SOURCE_FALLBACK_STATUS,
            )
            self.assertEqual(
                promoted["texture_contract_status"],
                texture_paths.CANONICAL_TEXTURE_STATUS,
            )
            self.assertNotEqual(
                provisional["source_paths"]["albedo"],
                promoted["files"]["color"],
            )

    def test_provisional_source_missing_is_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            target = (
                Path(folder)
                / "tree_asset"
                / "cluster"
                / "SK_leaf_test.spm"
            )
            target.parent.mkdir(parents=True)
            target.touch()

            with self.assertRaises(RuntimeError) as raised:
                texture_paths.resolve_production_texture_contract(
                    target,
                    "M_leaf_missing",
                    8,
                    source_paths={},
                )

            message = str(raised.exception)
            self.assertIn("material=M_leaf_missing", message)
            self.assertIn("role=albedo", message)
            self.assertIn(
                texture_paths.SOURCE_FALLBACK_REMEDIATION,
                message,
            )

    def test_provisional_source_rejects_cache_and_generated_png(self):
        with tempfile.TemporaryDirectory() as folder:
            target = (
                Path(folder)
                / "tree_asset"
                / "cluster"
                / "SK_leaf_test.spm"
            )
            target.parent.mkdir(parents=True)
            target.touch()
            cached = (
                Path(folder)
                / "cache"
                / "Leaf_Albedo.tif"
            )
            cached.parent.mkdir()
            cached.touch()

            with self.assertRaisesRegex(
                RuntimeError,
                "blocked_component=cache",
            ):
                texture_paths.resolve_production_texture_contract(
                    target,
                    "M_leaf_cache",
                    8,
                    source_paths={"albedo": cached},
                )

            generated = Path(folder) / "T_leaf_ao_from_height.png"
            generated.touch()
            with self.assertRaisesRegex(
                RuntimeError,
                "export_generated_png=true",
            ):
                texture_paths.resolve_production_texture_contract(
                    target,
                    "M_leaf_generated",
                    8,
                    source_paths={"albedo": generated},
                )


if __name__ == "__main__":
    unittest.main()
