import importlib.util
import json
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "addons"
    / "atlas_leaf_mesh_builder"
    / "unit_contract.py"
)
SPEC = importlib.util.spec_from_file_location(
    "atlas_leaf_mesh_builder_unit_contract_test",
    MODULE_PATH,
)
UNIT_CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UNIT_CONTRACT)
validate_unit_probe_contract = UNIT_CONTRACT.validate_unit_probe_contract
resolve_unit_probe_contract_update = (
    UNIT_CONTRACT.resolve_unit_probe_contract_update
)


def fixture(**selected_overrides):
    selected = {
        "mesh_geometry_scale": 1.0,
        "mesh_asset_scale": 0.01,
        "generator_scale": 1.0,
        "scale_location": "SPM_MESH_ASSET",
        "effective_scale": 0.01,
    }
    selected.update(selected_overrides)
    return {
        "kind": "speedtree_fbx_spm_unit_probe",
        "version": 1,
        "status": "verified",
        "physical_target_meters": 0.1,
        "blender_units": {
            "system": "METRIC",
            "scale_length": 1.0,
            "target_blender_units": 0.1,
        },
        "selected": selected,
        "generator_results": [
            {
                "generator_type": "Frond",
                "status": "verified",
                "same_unit_contract": True,
            },
            {
                "generator_type": "Leaf Mesh",
                "status": "verified",
                "same_unit_contract": True,
            },
        ],
    }


class UnitContractTests(unittest.TestCase):
    def test_accepts_one_common_mesh_asset_scale(self):
        result = validate_unit_probe_contract(fixture())
        self.assertEqual(result["scale_location"], "SPM_MESH_ASSET")
        self.assertEqual(result["verified_generator_types"], ["Frond", "Leaf Mesh"])

    def test_rejects_duplicate_geometry_and_asset_scale(self):
        with self.assertRaisesRegex(ValueError, "duplicated"):
            validate_unit_probe_contract(
                fixture(
                    mesh_geometry_scale=0.01,
                    mesh_asset_scale=0.01,
                    effective_scale=0.0001,
                    scale_location="FBX_GEOMETRY",
                )
            )

    def test_rejects_role_specific_generator_scale(self):
        with self.assertRaisesRegex(ValueError, "generator or Frond"):
            validate_unit_probe_contract(
                fixture(generator_scale=0.01)
            )

    def test_requires_both_frond_and_leaf_mesh_measurements(self):
        value = fixture()
        value["generator_results"] = value["generator_results"][:1]
        with self.assertRaisesRegex(ValueError, "Leaf Mesh"):
            validate_unit_probe_contract(value)

    def test_missing_update_preserves_existing_verified_contract(self):
        existing = fixture()
        resolved = resolve_unit_probe_contract_update(
            json.dumps(existing),
            None,
        )
        self.assertEqual(resolved["contract_sha256"], validate_unit_probe_contract(
            existing
        )["contract_sha256"])

    def test_explicit_clear_is_required_to_remove_existing_contract(self):
        self.assertIsNone(resolve_unit_probe_contract_update(
            json.dumps(fixture()),
            None,
            clear=True,
        ))

    def test_replace_and_clear_together_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "replaced and cleared"):
            resolve_unit_probe_contract_update(
                json.dumps(fixture()),
                fixture(),
                clear=True,
            )


if __name__ == "__main__":
    unittest.main()
