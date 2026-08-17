import importlib.util
import json
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


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
    ET.SubElement(material, "UserData").text = "historical-managed-marker"
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

    def workspace(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        target = root / "SK_tree_Lauraceae_11.spm"
        target.write_text("fixture", encoding="utf-8")
        return temporary, target

    def props(self, target, *, target_row=None):
        template_target = target.with_name("SK_branch_Lauraceae_01.spm")
        mapping = {
            str(template_target): {
                "source_material_names": [self.material_name],
                "source_material_ids": [3],
                "adopt_source_material": True,
                "generator_variant_policy": "preserve_existing_slots",
                "source_binding_repairs": [],
            }
        }
        if target_row is not None:
            mapping[str(target)] = target_row
        return types.SimpleNamespace(
            collection_name="Whatever_Current_Collection_Is_Named",
            speedtree_atlas_asset_name=self.material_name,
            speedtree_source_materials_json=json.dumps(mapping),
        )

    def fake_speedtree(self, *, material_present=True):
        module = types.ModuleType(f"{PACKAGE_NAME}.speedtree")
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
        module.read_spm_xml = lambda value: (
            material_root(self.material_name, self.material_id, [200, 201])
            if material_present
            else ET.Element("SpeedTreeModel")
        )
        module.spm_material_mesh_ids = spm_material_mesh_ids

        def source_mapping(props):
            raw = json.loads(props.speedtree_source_materials_json)
            result = {}
            for path, row in raw.items():
                result[module.normalized_target_key(path)] = {
                    "source_material_names": list(
                        row.get("source_material_names") or []
                    ),
                    "source_material_ids": row.get("source_material_ids"),
                    "adopt_source_material": bool(
                        row.get("adopt_source_material", False)
                    ),
                    "generator_variant_policy": module.normalize_generator_variant_policy(
                        row.get("generator_variant_policy")
                    ),
                    "source_binding_repairs": list(
                        row.get("source_binding_repairs") or []
                    ),
                    "generator_delivery_scope_intent": row.get(
                        "generator_delivery_scope_intent"
                    ),
                }
            return result

        module.speedtree_source_material_mapping = source_mapping
        return module

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

    def test_explicit_current_transaction_ignores_historical_scope_state(self):
        temporary, target = self.workspace()
        self.addCleanup(temporary.cleanup)
        props = self.props(target)
        fake = self.fake_speedtree()

        result = self.run_with_speedtree(
            fake,
            lambda: policy.extend_current_transaction_source_material_adoptions(
                props,
                [target],
                blend_path=target.with_suffix(".blend"),
            ),
        )

        self.assertEqual(
            result["authority_policy"],
            "explicit_current_transaction_v2",
        )
        self.assertEqual(len(result["added"]), 1)
        self.assertEqual(
            result["added"][0]["ownership_authority"],
            "explicit_current_transaction",
        )
        self.assertEqual(result["added"][0]["source_mesh_ids"], [200, 201])
        mapping = json.loads(props.speedtree_source_materials_json)
        self.assertEqual(
            mapping[str(target.absolute())]["source_material_ids"],
            [self.material_id],
        )

    def test_explicit_current_transaction_overrides_old_conflicting_mapping(self):
        temporary, target = self.workspace()
        self.addCleanup(temporary.cleanup)
        props = self.props(
            target,
            target_row={
                "source_material_names": ["M_old_material"],
                "source_material_ids": [99],
                "adopt_source_material": True,
                "generator_variant_policy": "preserve_existing_slots",
                "source_binding_repairs": [{"old": "history"}],
            },
        )
        fake = self.fake_speedtree()

        result = self.run_with_speedtree(
            fake,
            lambda: policy.extend_current_transaction_source_material_adoptions(
                props,
                [target],
                blend_path=None,
            ),
        )

        self.assertEqual(len(result["reconciled"]), 1)
        mapping = json.loads(props.speedtree_source_materials_json)
        request = mapping[str(target.absolute())]
        self.assertEqual(request["source_material_names"], [self.material_name])
        self.assertEqual(request["source_material_ids"], [self.material_id])
        self.assertEqual(request["source_binding_repairs"], [])

    def test_live_target_still_must_contain_requested_material(self):
        temporary, target = self.workspace()
        self.addCleanup(temporary.cleanup)
        props = self.props(target)
        fake = self.fake_speedtree(material_present=False)

        with self.assertRaisesRegex(
            RuntimeError,
            "could not find Material_v8",
        ):
            self.run_with_speedtree(
                fake,
                lambda: policy.extend_current_transaction_source_material_adoptions(
                    props,
                    [target],
                ),
            )


if __name__ == "__main__":
    unittest.main()
