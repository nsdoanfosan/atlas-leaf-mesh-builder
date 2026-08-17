import copy
import importlib.util
import json
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO / "addons" / "atlas_leaf_mesh_builder"
PACKAGE_NAME = "atlas_leaf_mesh_builder"


def load_policy_module():
    package = sys.modules.get(PACKAGE_NAME)
    if package is None:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules[PACKAGE_NAME] = package
    name = f"{PACKAGE_NAME}.current_transaction_adoption"
    spec = importlib.util.spec_from_file_location(
        name,
        PACKAGE_DIR / "current_transaction_adoption.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, package


policy, package = load_policy_module()


def material_root(material_name, material_id, mesh_ids):
    root = ET.Element("SpeedTreeModel")
    assets = ET.SubElement(root, "Assets")
    material = ET.SubElement(
        assets,
        "Material_v8",
        {"Name": material_name, "ID": str(material_id)},
    )
    ET.SubElement(material, "CutoutMeshID").text = str(mesh_ids[0])
    supplemental = ET.SubElement(material, "SupplementalCutoutMeshIDs")
    supplemental.attrib["Count"] = str(max(0, len(mesh_ids) - 1))
    for mesh_id in mesh_ids[1:]:
        ET.SubElement(supplemental, "CutoutMesh", {"ID": str(mesh_id)})
    ET.SubElement(material, "UserData").text = "managed-marker"
    return root


def spm_material_mesh_ids(material):
    values = []
    first = material.findtext("CutoutMeshID")
    if first and first != "-1":
        values.append(int(first))
    supplemental = material.find("SupplementalCutoutMeshIDs")
    if supplemental is not None:
        values.extend(
            int(node.attrib["ID"])
            for node in supplemental.findall("CutoutMesh")
        )
    return values


class CurrentTransactionAdoptionTests(unittest.TestCase):
    material_name = "M_branch_lauraceae_01"
    material_id = 7
    previous_scope = "471f3e5f3ae3499cb5f21608b8d1e8eb"

    def workspace(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        target = root / "SK_tree_Lauraceae_11.spm"
        target.write_text("fixture", encoding="utf-8")
        blend = root / "SK_branch_Lauraceae_01.blend"
        blend.write_text("blend", encoding="utf-8")
        return temporary, root, target, blend

    def previous_manifest(self, target, blend, *, collection="Cluster_Plans"):
        return {
            "spm": str(target),
            "blend_file": str(blend),
            "source_collection": collection,
            "export_scope_id": self.previous_scope,
            "speedtree_material_groups": [{
                "material": self.material_name,
                "material_id": self.material_id,
                # Deliberately stale generated output IDs.  These must not
                # override the current live SPM during a same-source refresh.
                "mesh_ids": [100, 101],
            }],
            "source_material_adoption": {
                "version": 1,
                "scope": self.previous_scope,
                "material_name": self.material_name,
                "material_id": self.material_id,
                "original_material_snapshot": "material-snapshot-v1",
                "original_mesh_snapshots": [
                    {"mesh_id": 10, "snapshot": "mesh-10"},
                    {"mesh_id": 11, "snapshot": "mesh-11"},
                ],
                "final_material_mesh_ids": [100, 101],
            },
        }

    def fake_speedtree(
        self,
        props,
        target,
        blend,
        previous_manifests,
        *,
        legacy_side_effect=None,
    ):
        module = types.ModuleType(f"{PACKAGE_NAME}.speedtree")
        module.SOURCE_MATERIAL_ADOPTION_VERSION = 1
        module.normalized_target_key = lambda value: str(
            Path(value).expanduser().absolute()
        ).casefold()
        module.normalize_generator_variant_policy = (
            lambda value: str(value or "preserve_existing_slots")
        )
        module.positive_int = lambda value: (
            int(value)
            if str(value or "").lstrip("-").isdigit() and int(value) > 0
            else None
        )
        module.read_spm_xml = lambda value: material_root(
            self.material_name,
            self.material_id,
            [200, 201],
        )
        module.spm_material_mesh_ids = spm_material_mesh_ids
        module.parse_atlas_leaf_spm_user_data = lambda value: {
            "kind": "material",
            "scope": self.previous_scope,
        }
        module.spm_export_scope = lambda manifest: str(
            manifest.get("export_scope_id") or ""
        )
        module.target_scope_manifests_for_blend = (
            lambda target_value, blend_value: copy.deepcopy(previous_manifests)
        )

        def source_identity(current, previous, material_name):
            return bool(
                str(Path(current.get("blend_file") or "").absolute()).casefold()
                == str(Path(previous.get("blend_file") or "").absolute()).casefold()
                and str(current.get("source_collection") or "")
                == str(previous.get("source_collection") or "")
                and material_name == self.material_name
            )

        module.manifests_share_source_identity = source_identity

        def source_mapping(value):
            raw = json.loads(value.speedtree_source_materials_json)
            result = {}
            for path, row in raw.items():
                result[module.normalized_target_key(path)] = {
                    "source_material_names": list(
                        row.get("source_material_names") or []
                    ),
                    "source_material_ids": copy.deepcopy(
                        row.get("source_material_ids")
                    ),
                    "adopt_source_material": bool(
                        row.get("adopt_source_material", False)
                    ),
                    "generator_variant_policy": module.normalize_generator_variant_policy(
                        row.get("generator_variant_policy")
                    ),
                    "source_binding_repairs": copy.deepcopy(
                        row.get("source_binding_repairs") or []
                    ),
                    "generator_delivery_scope_intent": copy.deepcopy(
                        row.get("generator_delivery_scope_intent")
                    ),
                }
            return result

        module.speedtree_source_material_mapping = source_mapping
        if legacy_side_effect is None:
            def legacy_should_not_run(*args, **kwargs):
                raise AssertionError(
                    "same-source current transaction should not use legacy fallback"
                )
            module.extend_source_material_adoptions_for_targets = legacy_should_not_run
        else:
            module.extend_source_material_adoptions_for_targets = legacy_side_effect
        return module

    def props(self, target, blend):
        template_target = target.with_name("SK_branch_Lauraceae_01.spm")
        return types.SimpleNamespace(
            collection_name="Cluster_Plans",
            speedtree_atlas_asset_name=self.material_name,
            speedtree_source_materials_json=json.dumps({
                str(template_target): {
                    "source_material_names": [self.material_name],
                    "source_material_ids": [3],
                    "adopt_source_material": True,
                    "generator_variant_policy": "preserve_existing_slots",
                }
            }),
        )

    def run_with_speedtree(self, fake, callback):
        previous = sys.modules.get(f"{PACKAGE_NAME}.speedtree")
        had_attr = hasattr(package, "speedtree")
        old_attr = getattr(package, "speedtree", None)
        try:
            sys.modules[f"{PACKAGE_NAME}.speedtree"] = fake
            package.speedtree = fake
            return callback()
        finally:
            if previous is None:
                sys.modules.pop(f"{PACKAGE_NAME}.speedtree", None)
            else:
                sys.modules[f"{PACKAGE_NAME}.speedtree"] = previous
            if had_attr:
                package.speedtree = old_attr
            elif hasattr(package, "speedtree"):
                delattr(package, "speedtree")

    def test_same_source_current_live_meshes_win_over_stale_final_mesh_ids(self):
        temporary, _root, target, blend = self.workspace()
        self.addCleanup(temporary.cleanup)
        props = self.props(target, blend)
        previous = self.previous_manifest(target, blend)
        fake = self.fake_speedtree(props, target, blend, [previous])

        result = self.run_with_speedtree(
            fake,
            lambda: policy.extend_current_transaction_source_material_adoptions(
                props,
                [target],
                blend_path=blend,
            ),
        )

        self.assertEqual(result["authority_policy"], "current_transaction_same_source_v1")
        self.assertEqual(len(result["added"]), 1)
        row = result["added"][0]
        self.assertEqual(row["source_mesh_ids"], [200, 201])
        self.assertEqual(row["predecessor_scopes"], [self.previous_scope])
        self.assertEqual(row["ownership_authority"], "current_transaction_same_source")
        mapping = json.loads(props.speedtree_source_materials_json)
        request = mapping[str(target.absolute())]
        self.assertEqual(request["source_material_ids"], [self.material_id])
        self.assertTrue(request["adopt_source_material"])

    def test_different_source_collection_still_uses_protected_legacy_path(self):
        temporary, _root, target, blend = self.workspace()
        self.addCleanup(temporary.cleanup)
        props = self.props(target, blend)
        previous = self.previous_manifest(
            target,
            blend,
            collection="Different_Atlas_Provider",
        )

        def protected(*args, **kwargs):
            raise RuntimeError("foreign provider remains protected")

        fake = self.fake_speedtree(
            props,
            target,
            blend,
            [previous],
            legacy_side_effect=protected,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "foreign provider remains protected",
        ):
            self.run_with_speedtree(
                fake,
                lambda: policy.extend_current_transaction_source_material_adoptions(
                    props,
                    [target],
                    blend_path=blend,
                ),
            )

    def test_conflicting_original_snapshots_still_fail_closed(self):
        temporary, _root, target, blend = self.workspace()
        self.addCleanup(temporary.cleanup)
        props = self.props(target, blend)
        first = self.previous_manifest(target, blend)
        second = copy.deepcopy(first)
        second["source_material_adoption"][
            "original_material_snapshot"
        ] = "different-original-material"
        fake = self.fake_speedtree(props, target, blend, [first, second])

        with self.assertRaisesRegex(
            RuntimeError,
            "same-provider adoption receipts disagree",
        ):
            self.run_with_speedtree(
                fake,
                lambda: policy.extend_current_transaction_source_material_adoptions(
                    props,
                    [target],
                    blend_path=blend,
                ),
            )


if __name__ == "__main__":
    unittest.main()
