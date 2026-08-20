import copy
import gzip
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


def load_speedtree_module():
    package_name = "atlas_leaf_mesh_builder"
    package = types.ModuleType(package_name)
    package.__path__ = [str(PACKAGE_DIR)]
    sys.modules[package_name] = package

    try:
        import bpy  # noqa: F401
    except ImportError:
        bpy = types.ModuleType("bpy")
        bpy.data = types.SimpleNamespace(filepath="")
        bpy.ops = types.SimpleNamespace()
        bpy.path = types.SimpleNamespace(abspath=lambda value: value)
        sys.modules["bpy"] = bpy

    try:
        import mathutils  # noqa: F401
    except ImportError:
        mathutils = types.ModuleType("mathutils")
        mathutils.Matrix = type("Matrix", (), {})
        mathutils.Vector = type("Vector", (), {})
        sys.modules["mathutils"] = mathutils

    constants = types.ModuleType(f"{package_name}.constants")
    constants.SPEEDTREE_101_BLANK_SPM = Path("__missing_blank__.spm")
    constants.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = Path("__missing_mesh__.spm")
    constants.SPEEDTREE_101_MATERIAL_SAMPLE = Path("__missing_material__.spm")
    sys.modules[constants.__name__] = constants
    materials = types.ModuleType(f"{package_name}.materials")
    materials.make_speedtree_material = lambda *args, **kwargs: None
    sys.modules[materials.__name__] = materials
    props = types.ModuleType(f"{package_name}.props")
    props.speedtree_spm_targets = lambda value: []
    sys.modules[props.__name__] = props
    texture_paths = types.ModuleType(f"{package_name}.texture_paths")
    texture_paths.CANONICAL_OUTPUT_KIND = (
        "pcg_st9_canonical_output_manifest"
    )
    texture_paths.CANONICAL_TEXTURE_STATUS = "canonical_pcg_output"
    texture_paths.SOURCE_FALLBACK_REMEDIATION = "run PCG ST9 Texture"
    texture_paths.SOURCE_FALLBACK_STATUS = (
        "source_fallback_needs_pcg_generation"
    )
    texture_paths.atlas_texture_paths = lambda value: {}
    texture_paths.canonical_texture_base_for_material = (
        lambda value: "T_" + str(value)
    )
    texture_paths.expected_canonical_role_paths = (
        lambda *args, **kwargs: {}
    )
    texture_paths.resolve_production_texture_contract = (
        lambda *args, **kwargs: {}
    )
    sys.modules[texture_paths.__name__] = texture_paths

    name = f"{package_name}.speedtree"
    spec = importlib.util.spec_from_file_location(name, PACKAGE_DIR / "speedtree.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


speedtree = load_speedtree_module()
delivery_scope = sys.modules[
    "atlas_leaf_mesh_builder.generator_delivery_scope"
]


class _FakePlan(dict):
    name = "Plan_Leaf_01"


class NormalizedPlanBoneContractTests(unittest.TestCase):
    def test_preserves_exact_source_bone(self):
        plan = _FakePlan({
            "speedtree_cluster_source_partition_mode": (
                "PER_CONNECTED_DEFORM_CLUSTER"
            ),
            "speedtree_cluster_source_bone": "Bone_7_Start",
            "speedtree_cluster_endpoint_bone": "Bone_7_End",
        })

        self.assertEqual(
            speedtree.normalized_plan_bone_contract(plan),
            {
                "source_partition_mode": "PER_CONNECTED_DEFORM_CLUSTER",
                "source_bone": "Bone_7_Start",
                "endpoint_bone": "Bone_7_End",
            },
        )

    def test_rejects_normalized_plan_without_exact_source_bone(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "has no exact source_bone identity",
        ):
            speedtree.normalized_plan_bone_contract(_FakePlan({
                "speedtree_cluster_source_partition_mode": (
                    "PER_CONNECTED_DEFORM_CLUSTER"
                ),
            }))


def canonical_test_textures(path):
    path = str(path)
    return {
        "color": path,
        "opacity": path,
        "normal": path,
        "extra": path,
        "height": path,
        "subsurface": path,
    }


class PhysicalNormalizationReceiptTests(unittest.TestCase):
    def test_scene_report_is_compacted_with_prototype_bounds(self):
        bounds = {
            "minimum": [-0.04, -0.045, -0.01],
            "maximum": [0.04, 0.045, 0.01],
            "size": [0.08, 0.09, 0.02],
            "center": [0.0, 0.0, 0.0],
        }
        capture_hash = "capture-contract-hash"
        report = {
            "workflow_mode": "PHYSICAL_DIRECT_CAPTURE",
            "size_policy": "uniform_whole_source_physical_target_meters",
            "plan_uv_policy": "direct_physical_capture_projection",
            "direct_uv_source": "same_blender_physical_capture_projection",
            "generator_size_policy": (
                "preserve_user_authored_leaf_and_frond_dimensions"
            ),
            "physical_capture_contract": {
                "kind": "speedtree_cluster_physical_capture_fit",
                "contract_sha256": capture_hash,
            },
            "physical_capture_contract_sha256": capture_hash,
            "prototypes": [{
                "prototype_index": 1,
                "skeletal_asset": "SK_branch_elm_01_01",
                "normalized_bounds": bounds,
                "mesh": "large-runtime-only-field",
            }],
            "variants": [{
                "index": 1,
                "card_index": 1,
                "skeletal_asset": "SK_branch_elm_01_01",
                "plan": "branch_elm_01_01",
                "normalized_bounds": bounds,
                "object_transforms_identity": True,
                "plan_covers_projection": True,
                "plan_uv_transfer": {
                    "policy": "direct_physical_capture_projection",
                    "direct_uv_source": (
                        "same_blender_physical_capture_projection"
                    ),
                },
                "plan_hull": [[0.0, 0.0], [1.0, 1.0]],
            }],
        }
        scene = {
            "speedtree_cluster_normalizer_last_report": json.dumps(report),
        }

        receipt = speedtree.physical_normalization_receipt_from_scene(scene)

        self.assertEqual(
            receipt["prototypes"][0]["normalized_bounds"]["size"],
            [0.08, 0.09, 0.02],
        )
        self.assertEqual(
            receipt["variants"][0]["plan"],
            "branch_elm_01_01",
        )
        self.assertNotIn("mesh", receipt["prototypes"][0])
        self.assertNotIn("plan_hull", receipt["variants"][0])
        self.assertEqual(
            receipt["generator_size_policy"],
            "preserve_user_authored_leaf_and_frond_dimensions",
        )

    def test_legacy_scene_report_does_not_create_physical_receipt(self):
        scene = {
            "speedtree_cluster_normalizer_last_report": json.dumps({
                "workflow_mode": "LEGACY_CAMERA_UV",
            }),
        }
        self.assertIsNone(
            speedtree.physical_normalization_receipt_from_scene(scene)
        )


class ClusterBakeTextureOriginTests(unittest.TestCase):
    def test_receipt_and_blender_material_use_prove_cluster_bake_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            blend = root / "SK_branch_test.blend"
            color = root / "branch_test.tga"
            opacity = root / "branch_test_Opacity.tga"
            color.touch()
            opacity.touch()
            material = types.SimpleNamespace(name="M_branch_test")
            source = types.SimpleNamespace(
                name="branch_test_01",
                material_slots=[
                    types.SimpleNamespace(material=material)
                ],
            )
            capture_hash = "physical-capture-hash"
            receipt = {
                "workflow_mode": "PHYSICAL_DIRECT_CAPTURE",
                "physical_capture_contract_sha256": capture_hash,
                "physical_capture_contract": {
                    "contract_sha256": capture_hash,
                    "source_blend": str(blend),
                    "capture_maps": [
                        {
                            "role": "Color",
                            "path": str(color),
                            "sha256": "color-hash",
                        },
                        {
                            "role": "Opacity",
                            "path": str(opacity),
                            "sha256": "opacity-hash",
                        },
                    ],
                },
            }
            group = {
                "material": "M_branch_test",
                "objects": [source],
            }

            origin = speedtree.blender_cluster_bake_origin_receipt(
                {"albedo": color, "alpha": opacity},
                group,
                receipt,
                blend_file=blend,
            )

            self.assertEqual(
                origin["source_origin"],
                speedtree.BLENDER_CLUSTER_BAKE_ORIGIN,
            )
            self.assertEqual(
                origin["material_users"],
                ["branch_test_01"],
            )
            self.assertEqual(len(origin["capture_maps"]), 2)

            baked = speedtree.serializable_blender_cluster_bake(
                "M_branch_test",
                {"albedo": color, "alpha": opacity},
                origin,
            )
            self.assertEqual(
                baked["source_origin"],
                speedtree.BLENDER_CLUSTER_BAKE_ORIGIN,
            )
            self.assertEqual(
                baked["texture_contract_status"],
                speedtree.BLENDER_CLUSTER_BAKE_TEXTURE_STATUS,
            )
            self.assertIsNone(baked["warning"])
            self.assertEqual(baked["files"]["albedo"], str(color))

    def test_filename_match_without_material_use_is_not_cluster_bake(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            color = root / "branch_test.tga"
            color.touch()
            capture_hash = "physical-capture-hash"
            receipt = {
                "workflow_mode": "PHYSICAL_DIRECT_CAPTURE",
                "physical_capture_contract_sha256": capture_hash,
                "physical_capture_contract": {
                    "contract_sha256": capture_hash,
                    "capture_maps": [
                        {"role": "Color", "path": str(color)}
                    ],
                },
            }
            group = {
                "material": "M_branch_test",
                "objects": [
                    types.SimpleNamespace(
                        name="branch_test_01",
                        material_slots=[],
                    )
                ],
            }

            self.assertIsNone(
                speedtree.blender_cluster_bake_origin_receipt(
                    {"albedo": color},
                    group,
                    receipt,
                )
            )

    def test_exported_group_keeps_cluster_bake_contract_for_spm_upsert(self):
        baked = {
            "texture_contract_status": (
                speedtree.BLENDER_CLUSTER_BAKE_TEXTURE_STATUS
            ),
            "source_origin": speedtree.BLENDER_CLUSTER_BAKE_ORIGIN,
            "files": {"albedo": "branch.tga"},
            "origin_receipt": {"kind": "test"},
        }
        result = speedtree.exported_material_group_manifest(
            {
                "collection": "Atlas_Branch_Plans",
                "material": "M_branch_test",
                "texture_contract_status": (
                    speedtree.BLENDER_CLUSTER_BAKE_TEXTURE_STATUS
                ),
                "blender_cluster_bake_texture": baked,
            },
            [{"asset": "branch.fbx"}],
        )

        self.assertEqual(
            result["blender_cluster_bake_texture"]["files"],
            {"albedo": "branch.tga"},
        )
        self.assertIsNot(
            result["blender_cluster_bake_texture"],
            baked,
        )


class MeshAssetScaleTests(unittest.TestCase):
    def test_handoff_material_name_is_stable_per_scope_and_material(self):
        first = speedtree.speedtree_handoff_material_name("scope-a", "M_leaf")
        second = speedtree.speedtree_handoff_material_name("scope-a", "M_leaf")
        other = speedtree.speedtree_handoff_material_name("scope-a", "M_stem")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("_AtlasLeaf_SpeedTree_Handoff_"))

    def test_source_refresh_receipt_rehashes_blend_and_textures(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            blend = root / "atlas.blend"
            texture = root / "leaf.tga"
            blend.write_bytes(b"blend-v1")
            texture.write_bytes(b"texture-v1")
            texture_inputs = {"M_leaf:albedo": str(texture)}
            signature = speedtree.texture_path_signature(texture_inputs)
            receipt = speedtree.source_refresh_receipt(
                blend,
                signature,
                texture_inputs,
            )
            self.assertEqual(receipt["version"], 2)
            self.assertEqual(
                receipt["builder_contract"],
                speedtree.SOURCE_REFRESH_BUILDER_CONTRACT,
            )

            self.assertTrue(
                speedtree.source_refresh_receipt_is_current(receipt, blend)
            )
            stale_contract = dict(receipt, builder_contract="older-builder")
            self.assertFalse(
                speedtree.source_refresh_receipt_is_current(stale_contract, blend)
            )
            texture.write_bytes(b"texture-v2")
            self.assertFalse(
                speedtree.source_refresh_receipt_is_current(receipt, blend)
            )
            texture.write_bytes(b"texture-v1")
            blend.write_bytes(b"blend-v2")
            self.assertFalse(
                speedtree.source_refresh_receipt_is_current(receipt, blend)
            )

    def test_same_float_accepts_blender_float32_rounding(self):
        self.assertTrue(speedtree._same_float(0.1, 0.10000000149011612))
        self.assertFalse(speedtree._same_float(0.1, 0.1001))

    def test_generated_speedtree_mesh_uses_manifest_asset_scale(self):
        template = ET.Element("Mesh", {"ID": "1", "Name": "template"})
        for name, value in (
            ("Filename", "old.fbx"),
            ("Embedded", "true"),
            ("PivotStyle", "2"),
            ("Scale", "1"),
        ):
            ET.SubElement(template, name).text = value
        generated = speedtree.make_spm_mesh(
            template,
            17,
            Path("D:/tree/meshes/leaf.fbx"),
            Path("D:/tree/SK_tree.spm"),
            {"mesh_asset_scale": 0.01, "export_scope_id": "scope"},
        )
        self.assertEqual(generated.findtext("Scale"), "0.01")
        self.assertEqual(generated.findtext("Embedded"), "false")
        self.assertEqual(generated.findtext("PivotStyle"), "0")

    def test_texture_signature_changes_when_same_path_is_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            texture = Path(folder) / "leaf.tga"
            texture.write_bytes(b"first")
            first = speedtree.texture_path_signature({"albedo": str(texture)})
            texture.write_bytes(b"second")
            second = speedtree.texture_path_signature({"albedo": str(texture)})
            self.assertNotEqual(first, second)

    def test_source_mapping_preserves_exact_generator_variant_policy(self):
        target = r"D:\Trees\SK_tree.spm"
        delivery_intent = {
            "kind": "speedtree_generator_delivery_scope_intent",
            "intent_sha256": "a" * 64,
        }
        props = types.SimpleNamespace(
            speedtree_source_materials_json=json.dumps(
                {
                    target: {
                        "source_material_names": ["M_branch"],
                        "source_material_ids": [8],
                        "generator_variant_policy": (
                            "ensure_all_material_cutouts"
                        ),
                        "source_binding_repairs": [
                            {
                                "generator_guid": "stable-guid",
                                "slot_prefix": "Material:Frond:0",
                                "from_mesh_id": -9,
                                "to_mesh_id": -10,
                            }
                        ],
                        "generator_delivery_scope_intent": delivery_intent,
                    }
                }
            )
        )

        mapping = speedtree.speedtree_source_material_mapping(props)
        request = mapping[speedtree.normalized_target_key(target)]

        self.assertEqual(
            request["generator_variant_policy"],
            "ensure_all_material_cutouts",
        )
        self.assertEqual(
            request["source_binding_repairs"][0]["generator_guid"],
            "stable-guid",
        )
        self.assertEqual(
            request["generator_delivery_scope_intent"],
            delivery_intent,
        )
        self.assertIsNot(
            request["generator_delivery_scope_intent"],
            delivery_intent,
        )


def add_material(assets, material_id, name, mesh_ids, user_data=""):
    material = ET.SubElement(assets, "Material_v8", {"ID": str(material_id), "Name": name})
    ET.SubElement(material, "CutoutMeshID").text = str(mesh_ids[0])
    supplemental = ET.SubElement(material, "SupplementalCutoutMeshIDs", {"Count": str(len(mesh_ids) - 1)})
    for mesh_id in mesh_ids[1:]:
        ET.SubElement(supplemental, "CutoutMesh", {"ID": str(mesh_id)})
    ET.SubElement(material, "UserData").text = user_data
    return material


def add_mesh(assets, mesh_id, user_data=""):
    mesh = ET.SubElement(assets, "Mesh", {"ID": str(mesh_id), "Name": f"mesh_{mesh_id}"})
    ET.SubElement(mesh, "Filename").text = f"meshes/{mesh_id}.fbx"
    ET.SubElement(mesh, "UserData").text = user_data
    return mesh


def add_generator(root, generator_type, name, slots):
    generator = ET.SubElement(root, "Generator", {"Type": generator_type})
    ET.SubElement(generator, "Name").text = name
    ET.SubElement(generator, "GUID").text = f"{generator_type}:{name}"
    properties = ET.SubElement(generator, "Properties")
    parent_name = (
        "Material:Frond"
        if speedtree.normalized_generator_type(generator_type) == "frond"
        else "Leaves:Type"
    )
    for index, (material_id, mesh_id) in enumerate(slots):
        prefix = f"{parent_name}:{index}"
        for suffix, value in (("Material", material_id), ("Mesh", mesh_id)):
            prop = ET.SubElement(properties, "Property")
            ET.SubElement(prop, "Name").text = f"{prefix}:{suffix}"
            ET.SubElement(prop, "Value").text = str(value)
    return generator


def add_variant_generator(
    root,
    generator_type,
    name,
    slots,
    *,
    parent_name=None,
    child_count=None,
):
    generator = ET.SubElement(root, "Generator", {"Type": generator_type})
    ET.SubElement(generator, "Name").text = name
    ET.SubElement(generator, "GUID").text = f"{generator_type}:{name}"
    properties = ET.SubElement(generator, "Properties")
    parent_name = parent_name or (
        "Material:Frond"
        if speedtree.normalized_generator_type(generator_type) == "frond"
        else "Leaves:Type"
    )
    parent = ET.SubElement(properties, "Property")
    ET.SubElement(parent, "Name").text = parent_name
    ET.SubElement(parent, "Value").text = "0"
    ET.SubElement(parent, "MultiPropertyChildren").text = str(
        len(slots) if child_count is None else child_count
    )
    for index, (material_id, mesh_id) in enumerate(slots):
        prefix = f"{parent_name}:{index}"
        for suffix, value in (("Material", material_id), ("Mesh", mesh_id)):
            prop = ET.SubElement(properties, "Property")
            ET.SubElement(prop, "Name").text = f"{prefix}:{suffix}"
            ET.SubElement(prop, "Value").text = str(value)
    return generator


def write_spm(path, root):
    path.write_bytes(gzip.compress(ET.tostring(root, encoding="utf-8"), mtime=0))


def generator_values(path):
    root = speedtree.read_spm_xml(path)
    values = {}
    for pair in speedtree.spm_generator_property_pairs(root, {"Leaf Mesh", "Frond"}):
        values[(pair["generator_name"], pair["slot_prefix"])] = (
            int(pair["material_property"].findtext("Value")),
            int(pair["mesh_property"].findtext("Value")),
        )
    return values


class ManagedReferenceAuditTests(unittest.TestCase):
    def test_audit_keeps_orphan_and_missing_dimensions_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            (folder / "meshes").mkdir()
            (folder / "meshes" / "10.fbx").write_bytes(b"active")
            (folder / "meshes" / "11.fbx").write_bytes(b"orphan")
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            marker = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": "scope-current",
                "group": "Atlas_Cards",
                "kind": "mesh",
            })
            legacy = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": "scope-legacy",
                "kind": "mesh",
            })
            for mesh_id, user_data in ((10, marker), (11, marker), (12, legacy)):
                add_mesh(assets, mesh_id, user_data)
            missing_name = add_mesh(assets, 13, legacy)
            missing_name.remove(missing_name.find("Filename"))
            add_generator(root, "Leaf Mesh", "Leaf", [(8, 10)])
            write_spm(target, root)

            audit = speedtree.spm_managed_reference_audit(target)

            self.assertEqual(
                {name: audit[name] for name in (
                    "checked", "active", "managed_orphan", "missing", "orphan_missing"
                )},
                {
                    "checked": 4,
                    "active": 1,
                    "managed_orphan": 3,
                    "missing": 2,
                    "orphan_missing": 2,
                },
            )
            self.assertFalse(audit["meshes"][1]["groupless"])
            self.assertTrue(audit["meshes"][2]["groupless"])
            self.assertEqual(
                audit["meshes"][3]["missing_filenames"],
                ["<missing Filename>"],
            )

    def test_audit_recovers_untagged_legacy_and_scoped_receipt_claims(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree_01.spm"
            mesh_dir = folder / "meshes"
            mesh_dir.mkdir()
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            for mesh_id in (7, 51, 106, 200):
                (mesh_dir / f"{mesh_id}.fbx").write_bytes(str(mesh_id).encode())
                add_mesh(assets, mesh_id)
            add_generator(root, "Leaf Mesh", "Leaf", [(8, 106)])
            write_spm(target, root)

            legacy = {
                "spm": str(folder / "SK_tree_03.spm"),
                "material_groups": [{
                    "collection": "Legacy_Leaves",
                    "mesh_ids": [7, 51],
                    "meshes": [
                        {"asset": str(mesh_dir / "7.fbx")},
                        {"asset": str(mesh_dir / "51.fbx")},
                    ],
                }],
            }
            (folder / "speedtree_import_manifest_M_leaf.json").write_text(
                json.dumps(legacy), encoding="utf-8"
            )
            scope_dir = folder / ".atlas_leaf_speedtree_scopes"
            scope_dir.mkdir()
            scoped = {
                "export_scope_id": "scope-current",
                "spm": str(target),
                "material_groups": [{
                    "collection": "Current_Cards",
                    "mesh_ids": [51, 106],
                    "meshes": [
                        {"asset": str(mesh_dir / "51.fbx")},
                        {"asset": str(mesh_dir / "106.fbx")},
                    ],
                }],
            }
            (scope_dir / "scope-current__SK_tree_01.json").write_text(
                json.dumps(scoped), encoding="utf-8"
            )

            audit = speedtree.spm_managed_reference_audit(target)
            rows = {row["mesh_id"]: row for row in audit["meshes"]}

            self.assertEqual(audit["checked"], 3)
            self.assertEqual(audit["active"], 1)
            self.assertEqual(audit["managed_orphan"], 2)
            self.assertEqual(rows[7]["ownership_evidence"], "legacy_shadow_manifest")
            self.assertTrue(rows[7]["groupless"])
            self.assertEqual(rows[7]["legacy_group"], "Legacy_Leaves")
            self.assertEqual(rows[51]["ownership_evidence"], "scope_manifest")
            self.assertEqual(rows[51]["scope"], "scope-current")
            self.assertFalse(rows[51]["groupless"])
            self.assertEqual(rows[106]["usage"], "active")
            self.assertNotIn(200, rows)


class FrondGeneratorGeometryScaleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.spm_path = Path(self.temp_dir.name) / "SK_tree_test.spm"
        root = ET.Element("SpeedTree")
        generators = ET.SubElement(root, "Generators")
        generator = ET.SubElement(generators, "Generator", {"Type": "Frond"})
        ET.SubElement(generator, "Name").text = "Frond 36"
        ET.SubElement(generator, "GUID").text = "frond-guid"
        properties = ET.SubElement(generator, "Properties")
        for name, value in (
            ("Shape:Scale:Width", "2"),
            ("Shape:Scale:Height", "3"),
        ):
            prop = ET.SubElement(properties, "SplineProperty")
            ET.SubElement(prop, "Name").text = name
            ET.SubElement(prop, "Value").text = value
        write_spm(self.spm_path, root)
        self.connection = {
            "bindings": [
                {
                    "generator_index": 0,
                    "generator_name": "Frond 36",
                    "generator_type": "Frond",
                }
            ]
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def property_values(self):
        root = speedtree.read_spm_xml(self.spm_path)
        generator = next(root.iter("Generator"))
        return {
            node.findtext("Name"): float(node.findtext("Value"))
            for node in generator.findall("./Properties/*")
        }

    def test_geometry_scale_is_idempotent_and_reversible(self):
        first = speedtree.normalize_connected_frond_generator_geometry_scale(
            self.spm_path,
            self.connection,
            {},
            0.01,
        )
        self.assertTrue(first["changed"])
        self.assertEqual(
            self.property_values(),
            {
                "Shape:Scale:Width": 0.02,
                "Shape:Scale:Height": 0.03,
            },
        )

        previous_manifest = {"generator_scale_normalization": first}
        second = speedtree.normalize_connected_frond_generator_geometry_scale(
            self.spm_path,
            self.connection,
            previous_manifest,
            0.01,
        )
        self.assertFalse(second["changed"])
        self.assertEqual(self.property_values()["Shape:Scale:Width"], 0.02)

        root = speedtree.read_spm_xml(self.spm_path)
        restored = speedtree.restore_frond_generator_geometry_scale(
            root,
            [previous_manifest],
        )
        self.assertEqual(len(restored), 2)
        write_spm(self.spm_path, root)
        self.assertEqual(
            self.property_values(),
            {
                "Shape:Scale:Width": 2.0,
                "Shape:Scale:Height": 3.0,
            },
        )


class GeneratorConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.spm_path = Path(self.temp_dir.name) / "SK_weed_parsley_01.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_leaf_parsley_02", [6, 7, 8, 9, 10])
        add_material(assets, 7, "M_leaf_parsley_atlas_02_stem", [63, 64])
        add_material(assets, 8, "M_leaf_parsley_atlas_02_green", [70, 71])
        add_material(assets, 9, "M_leaf_parsley_atlas_02_yellow", [78])
        for mesh_id in [6, 7, 8, 9, 10, 63, 64, 70, 71, 78]:
            add_mesh(assets, mesh_id)
        add_generator(root, "LeafMesh", "Leaf", [(4, 6), (4, 7)])
        add_generator(root, "Leaf Mesh", "Leaf 2", [(4, 9)])
        add_generator(root, "Frond", "Frond", [(4, 10)])
        write_spm(self.spm_path, root)
        self.groups = [
            {
                "material": "M_leaf_parsley_atlas_02_stem",
                "material_id": 7,
                "mesh_ids": [63, 64],
                "meshes": [
                    {"source_object": "leaf_01_front_01"},
                    {"source_object": "leaf_02_front_02"},
                ],
            },
            {
                "material": "M_leaf_parsley_atlas_02_green",
                "material_id": 8,
                "mesh_ids": [70, 71],
                "meshes": [
                    {"source_object": "leaf_03_front_03"},
                    {"source_object": "leaf_04_front_04"},
                ],
            },
            {
                "material": "M_leaf_parsley_atlas_02_yellow",
                "material_id": 9,
                "mesh_ids": [78],
                "meshes": [{"source_object": "leaf_05_front_05"}],
            },
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def explicit_delivery_scope_intent(self, required_count=1):
        root = speedtree.read_spm_xml(self.spm_path)
        target_by_source_mesh = {
            6: (7, 63),
            7: (7, 64),
            9: (8, 71),
            10: (9, 78),
        }
        authored = []
        for pair in speedtree.spm_generator_property_pairs(
            root, {"Leaf Mesh", "Frond"}
        ):
            source_mesh_id = int(pair["mesh_property"].findtext("Value"))
            target = target_by_source_mesh[source_mesh_id]
            authored.append({
                "slot_identity": list(
                    delivery_scope.canonical_slot_identity(pair)
                ),
                "target_material_id": target[0],
                "target_mesh_id": target[1],
            })
        authored = delivery_scope.canonical_authored_slots(authored)
        required = [
            row["slot_identity"] for row in authored[:required_count]
        ]
        continuity = [
            {
                "slot_identity": row["slot_identity"],
                "reason": "sanitized recipe-owned continuity",
                "policy": delivery_scope.CONTINUITY_ONLY_POLICY,
                "provenance": {
                    "fixture": "atlas-issue-8-direct-producer",
                    "revision": 1,
                },
            }
            for row in authored[required_count:]
        ]
        provider = str(Path(self.temp_dir.name) / "provider.blend")
        intent = {
            "kind": delivery_scope.INTENT_KIND,
            "schema_version": 1,
            "authority": {
                "kind": "sanitized_test_recipe",
                "id": "atlas-issue-8",
                "provenance": {"fixture": "direct-producer"},
            },
            "target": {
                "spm": str(self.spm_path),
                "provider_blend": provider,
                "provider_scope_id": "scope-issue-8",
                "material_id": 4,
            },
            "authored_slots": authored,
            "required_live_slot_identities": required,
            "continuity_only_slots": continuity,
            "runtime_inactive_policy": (
                delivery_scope.RUNTIME_INACTIVE_POLICY
            ),
        }
        intent["intent_sha256"] = delivery_scope.canonical_sha256(intent)
        return intent

    def test_parsley_cutout_ordinals_connect_generator_slots(self):
        result = speedtree.connect_atlas_generators_in_spm(
            self.spm_path, ["M_leaf_parsley_02"], self.groups, [4]
        )
        self.assertTrue(result["complete"])
        self.assertNotIn("delivery_scope", result)

    def test_explicit_scope_is_planned_before_write_and_resolved_exactly(self):
        intent = self.explicit_delivery_scope_intent(required_count=1)

        result = speedtree.connect_atlas_generators_in_spm(
            self.spm_path,
            ["M_leaf_parsley_02"],
            self.groups,
            [4],
            generator_delivery_scope_intent=intent,
        )

        self.assertEqual(
            result["delivery_scope"]["intent"],
            intent,
        )
        self.assertEqual(
            result["delivery_scope"]["resolved"][
                "target_spm_postwrite_sha256"
            ],
            speedtree.spm_text_sha256(self.spm_path),
        )
        validated = delivery_scope.validate_resolved_delivery_scope(
            result,
            target_spm=self.spm_path,
            material_id=4,
            provider_blend=intent["target"]["provider_blend"],
            target_spm_postwrite_sha256=speedtree.spm_text_sha256(
                self.spm_path
            ),
        )
        self.assertEqual(validated["intent_sha256"], intent["intent_sha256"])
        self.assertEqual(len(validated["required_live_slot_identities"]), 1)
        self.assertEqual(len(validated["continuity_only_slot_identities"]), 3)

    def test_non_exact_or_mismatched_explicit_scope_fails_before_write(self):
        exact = self.explicit_delivery_scope_intent(required_count=1)
        cases = []

        partial = copy.deepcopy(exact)
        partial.pop("intent_sha256")
        partial["authored_slots"].pop()
        partial["continuity_only_slots"].pop()
        partial["intent_sha256"] = delivery_scope.canonical_sha256(partial)
        cases.append(partial)

        mismatched = copy.deepcopy(exact)
        mismatched.pop("intent_sha256")
        mismatched["authored_slots"][0]["target_mesh_id"] = 999
        mismatched["intent_sha256"] = delivery_scope.canonical_sha256(
            mismatched
        )
        cases.append(mismatched)

        foreign = copy.deepcopy(exact)
        foreign.pop("intent_sha256")
        foreign["target"]["spm"] = str(
            Path(self.temp_dir.name) / "foreign.spm"
        )
        foreign["intent_sha256"] = delivery_scope.canonical_sha256(foreign)
        cases.append(foreign)

        for intent in cases:
            with self.subTest(intent=intent["intent_sha256"]):
                before = self.spm_path.read_bytes()
                with self.assertRaises(delivery_scope.GeneratorDeliveryScopeError):
                    speedtree.connect_atlas_generators_in_spm(
                        self.spm_path,
                        ["M_leaf_parsley_02"],
                        self.groups,
                        [4],
                        generator_delivery_scope_intent=intent,
                    )
                self.assertEqual(self.spm_path.read_bytes(), before)

    def test_preflight_seals_provider_scope_and_detects_plan_drift(self):
        intent = self.explicit_delivery_scope_intent(required_count=0)
        manifest = {
            "blend_file": intent["target"]["provider_blend"],
            "export_scope_id": intent["target"]["provider_scope_id"],
        }
        preflight = speedtree.preflight_generator_delivery_scope(
            self.spm_path,
            intent,
            manifest=manifest,
            material_groups=self.groups,
            source_material_names=["M_leaf_parsley_02"],
            source_material_ids=[4],
        )
        self.assertEqual(preflight["intent_sha256"], intent["intent_sha256"])

        stale_preflight = copy.deepcopy(preflight)
        stale_preflight["planned_slot_identities"].pop()
        before = self.spm_path.read_bytes()
        with self.assertRaisesRegex(
            delivery_scope.GeneratorDeliveryScopeError,
            "changed after its pre-write plan",
        ):
            speedtree.connect_atlas_generators_in_spm(
                self.spm_path,
                ["M_leaf_parsley_02"],
                self.groups,
                [4],
                generator_delivery_scope_intent=intent,
                delivery_scope_preflight=stale_preflight,
            )
        self.assertEqual(self.spm_path.read_bytes(), before)

    def test_delivery_scope_uses_production_identity_for_staged_spm(self):
        intent = self.explicit_delivery_scope_intent(required_count=1)
        staged_spm = (
            Path(self.temp_dir.name)
            / "private-transaction-stage"
            / self.spm_path.name
        )
        staged_spm.parent.mkdir()
        staged_spm.write_bytes(self.spm_path.read_bytes())
        manifest = {
            "blend_file": intent["target"]["provider_blend"],
            "export_scope_id": intent["target"]["provider_scope_id"],
        }

        with self.assertRaisesRegex(
            delivery_scope.GeneratorDeliveryScopeError,
            "another target SPM",
        ):
            speedtree.preflight_generator_delivery_scope(
                staged_spm,
                intent,
                manifest=manifest,
                material_groups=self.groups,
                source_material_names=["M_leaf_parsley_02"],
                source_material_ids=[4],
            )

        preflight = speedtree.preflight_generator_delivery_scope(
            staged_spm,
            intent,
            contract_target_spm=self.spm_path,
            manifest=manifest,
            material_groups=self.groups,
            source_material_names=["M_leaf_parsley_02"],
            source_material_ids=[4],
        )
        result = speedtree.connect_atlas_generators_in_spm(
            staged_spm,
            ["M_leaf_parsley_02"],
            self.groups,
            [4],
            generator_delivery_scope_intent=intent,
            delivery_scope_preflight=preflight,
            contract_target_spm=self.spm_path,
        )

        validated = delivery_scope.validate_resolved_delivery_scope(
            result,
            target_spm=self.spm_path,
            material_id=4,
            provider_blend=intent["target"]["provider_blend"],
            target_spm_postwrite_sha256=speedtree.spm_text_sha256(
                staged_spm
            ),
        )
        self.assertEqual(
            validated["intent_sha256"], intent["intent_sha256"]
        )

    def test_minus9_source_binding_requires_hashed_exact_backup_evidence(self):
        live_root = ET.Element("SpeedTreeModel")
        live_assets = ET.SubElement(live_root, "Assets")
        add_material(live_assets, 4, "M_branch", [6])
        add_material(live_assets, 7, "M_branch_atlas", [63])
        add_mesh(live_assets, 6)
        add_mesh(live_assets, 63)
        add_generator(live_root, "Frond", "Frond 4", [(4, -9)])
        write_spm(self.spm_path, live_root)

        evidence_path = Path(self.temp_dir.name) / "evidence.spm"
        evidence_root = ET.Element("SpeedTreeModel")
        evidence_assets = ET.SubElement(evidence_root, "Assets")
        add_material(evidence_assets, 19, "M_branch", [132])
        add_mesh(evidence_assets, 132)
        add_generator(
            evidence_root,
            "Frond",
            "Frond 4",
            [(19, -10)],
        )
        write_spm(evidence_path, evidence_root)
        repair = {
            "generator_name": "Frond 4",
            "generator_guid": "Frond:Frond 4",
            "generator_type": "Frond",
            "slot_prefix": "Material:Frond:0",
            "source_material_name": "M_branch",
            "source_material_id": 4,
            "from_mesh_id": -9,
            "to_mesh_id": -10,
            "evidence": [
                {
                    "path": str(evidence_path),
                    "sha256": speedtree.file_sha256(evidence_path),
                }
            ],
        }
        groups = [
            {
                "material": "M_branch_atlas",
                "material_id": 7,
                "mesh_ids": [63],
                "meshes": [
                    {
                        "source_object": "branch_01",
                        "source_ordinal": 1,
                    }
                ],
            }
        ]

        result = speedtree.connect_atlas_generators_in_spm(
            self.spm_path,
            ["M_branch"],
            groups,
            [4],
            source_binding_repairs=[repair],
        )

        self.assertEqual(
            generator_values(self.spm_path)[
                ("Frond 4", "Material:Frond:0")
            ],
            (7, -10),
        )
        self.assertEqual(
            result["applied_source_binding_repairs"][0]["from_mesh_id"],
            -9,
        )
        self.assertEqual(
            result["bindings"][0]["source_mesh_id"],
            -10,
        )

    def test_managed_orphan_binding_restores_hashed_authored_cutout(self):
        live_root = ET.Element("SpeedTreeModel")
        live_assets = ET.SubElement(live_root, "Assets")
        add_material(live_assets, 4, "M_branch", [5, 6])
        add_material(live_assets, 7, "M_branch_atlas", [63])
        for mesh_id in [5, 6, 63, 107]:
            add_mesh(live_assets, mesh_id)
        add_generator(live_root, "Frond", "Frond 4", [(4, 107)])
        write_spm(self.spm_path, live_root)

        evidence_path = Path(self.temp_dir.name) / "evidence-authored.spm"
        evidence_root = ET.Element("SpeedTreeModel")
        evidence_assets = ET.SubElement(evidence_root, "Assets")
        add_material(evidence_assets, 19, "M_branch", [5, 6])
        add_mesh(evidence_assets, 5)
        add_mesh(evidence_assets, 6)
        add_generator(
            evidence_root,
            "Frond",
            "Frond 4",
            [(19, 5)],
        )
        write_spm(evidence_path, evidence_root)
        repair = {
            "generator_name": "Frond 4",
            "generator_guid": "Frond:Frond 4",
            "generator_type": "Frond",
            "slot_prefix": "Material:Frond:0",
            "source_material_name": "M_branch",
            "source_material_id": 4,
            "from_mesh_id": 107,
            "to_mesh_id": 5,
            "evidence": [
                {
                    "path": str(evidence_path),
                    "sha256": speedtree.file_sha256(evidence_path),
                }
            ],
        }
        groups = [
            {
                "material": "M_branch_atlas",
                "material_id": 7,
                "mesh_ids": [63],
                "meshes": [
                    {
                        "source_object": "branch_01",
                        "source_ordinal": 1,
                    }
                ],
            }
        ]

        result = speedtree.connect_atlas_generators_in_spm(
            self.spm_path,
            ["M_branch"],
            groups,
            [4],
            source_binding_repairs=[repair],
        )

        self.assertEqual(
            result["applied_source_binding_repairs"][0]["from_mesh_id"],
            107,
        )
        self.assertEqual(
            result["applied_source_binding_repairs"][0]["to_mesh_id"],
            5,
        )
        self.assertEqual(result["bindings"][0]["source_mesh_id"], 5)

    def test_explicit_source_ordinals_support_non_leaf_plan_names(self):
        groups = json.loads(json.dumps(self.groups))
        ordinal = 1
        for group in groups:
            for item in group["meshes"]:
                item["source_object"] = f"branch_elm_01_{ordinal:02d}"
                item["source_ordinal"] = ordinal
                ordinal += 1
        result = speedtree.connect_atlas_generators_in_spm(
            self.spm_path,
            ["M_leaf_parsley_02"],
            groups,
            [4],
        )
        self.assertTrue(result["complete"])
        self.assertEqual(
            sorted(binding["leaf_ordinal"] for binding in result["bindings"]),
            [1, 2, 4, 5],
        )
        self.assertEqual(result["changed_slot_pairs"], 4)
        self.assertEqual(
            generator_values(self.spm_path),
            {
                ("Leaf", "Leaves:Type:0"): (7, 63),
                ("Leaf", "Leaves:Type:1"): (7, 64),
                ("Leaf 2", "Leaves:Type:0"): (8, 71),
                ("Frond", "Material:Frond:0"): (9, 78),
            },
        )

    def test_second_pass_is_complete_without_source_slots(self):
        speedtree.connect_atlas_generators_in_spm(self.spm_path, ["M_leaf_parsley_02"], self.groups)
        result = speedtree.connect_atlas_generators_in_spm(
            self.spm_path, ["M_leaf_parsley_02"], self.groups
        )
        self.assertTrue(result["complete"])
        self.assertEqual(result["changed_slot_pairs"], 0)
        self.assertEqual(result["already_connected_slot_pairs"], 4)

    def test_second_pass_preserves_original_bindings_for_future_detach(self):
        first = speedtree.connect_atlas_generators_in_spm(
            self.spm_path, ["M_leaf_parsley_02"], self.groups
        )
        second = speedtree.connect_atlas_generators_in_spm(
            self.spm_path,
            ["M_leaf_parsley_02"],
            self.groups,
            previous_bindings=first["bindings"],
        )
        self.assertTrue(all(
            binding["source_material_id"] == 4
            for binding in second["bindings"]
        ))
        self.assertEqual(
            [binding["source_mesh_id"] for binding in second["bindings"]],
            [6, 7, 9, 10],
        )

    def test_previous_generated_bindings_retarget_after_adopted_mesh_ids_change(self):
        target = Path(self.temp_dir.name) / "SK_cluster_refresh.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(
            assets,
            1,
            "M_cluster_Silky_Dogwood_01",
            [20, 23],
        )
        for mesh_id in (10, 13, 20, 23):
            add_mesh(assets, mesh_id)
        leaf = add_variant_generator(
            root,
            "Leaf Mesh",
            "Leaf 4",
            [(1, 10)],
        )
        frond = add_variant_generator(
            root,
            "Frond",
            "Frond 2",
            [(1, 13)],
        )
        write_spm(target, root)
        groups = [{
            "material": "M_cluster_Silky_Dogwood_01",
            "material_id": 1,
            "mesh_ids": [20, 23],
            "meshes": [
                {
                    "source_object": "cluster_Silky_Dogwood_01_01",
                    "source_ordinal": 1,
                },
                {
                    "source_object": "cluster_Silky_Dogwood_01_04",
                    "source_ordinal": 4,
                },
            ],
        }]
        previous_bindings = [
            {
                "generator_index": 0,
                "generator_name": "Leaf 4",
                "generator_guid": speedtree.generator_guid(leaf),
                "generator_type": "Leaf Mesh",
                "slot_prefix": "Leaves:Type:0",
                "source_material_id": 1,
                "source_material_name": "M_cluster_Silky_Dogwood_01",
                "source_mesh_id": -10,
                "sentinel_policy": "mesh_-10_to_first_generated_leaf",
                "target_material_id": 1,
                "target_mesh_id": 10,
                "leaf_ordinal": 1,
                "created_slot": False,
            },
            {
                "generator_index": 1,
                "generator_name": "Frond 2",
                "generator_guid": speedtree.generator_guid(frond),
                "generator_type": "Frond",
                "slot_prefix": "Material:Frond:0",
                "source_material_id": 1,
                "source_material_name": "M_cluster_Silky_Dogwood_01",
                "source_mesh_id": 5,
                "sentinel_policy": None,
                "target_material_id": 1,
                "target_mesh_id": 13,
                "leaf_ordinal": 4,
                "created_slot": False,
            },
        ]

        result = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_cluster_Silky_Dogwood_01"],
            groups,
            [1],
            previous_bindings=previous_bindings,
            source_mesh_ids_by_name={
                "M_cluster_Silky_Dogwood_01": [2, 3, 4, 5, 6],
            },
            generator_variant_policy="ensure_all_material_cutouts",
        )

        self.assertEqual(
            generator_values(target),
            {
                ("Leaf 4", "Leaves:Type:0"): (1, 20),
                ("Frond 2", "Material:Frond:0"): (1, 23),
            },
        )
        by_name = {
            binding["generator_name"]: binding
            for binding in result["bindings"]
        }
        self.assertEqual(by_name["Leaf 4"]["source_mesh_id"], -10)
        self.assertEqual(by_name["Frond 2"]["source_mesh_id"], 5)
        self.assertEqual(result["changed_slot_pairs"], 2)

    def test_target_removal_restores_generators_and_removes_owned_assets(self):
        manifest = {
            "export_scope_id": "scope-a",
            "blend_file": str(Path(self.temp_dir.name) / "atlas.blend"),
            "spm": str(self.spm_path),
            "speedtree_material_groups": self.groups,
            "mesh_ids": [63, 64, 70, 71, 78],
            "meshes": [],
        }
        root = speedtree.read_spm_xml(self.spm_path)
        assets = root.find("Assets")
        for material_id in (7, 8, 9):
            speedtree.tag_spm_asset(
                assets.find(f"Material_v8[@ID='{material_id}']"), manifest, "material"
            )
        for mesh_id in (63, 64, 70, 71, 78):
            speedtree.tag_spm_asset(
                assets.find(f"Mesh[@ID='{mesh_id}']"), manifest, "mesh"
            )
        write_spm(self.spm_path, root)
        connection = speedtree.connect_atlas_generators_in_spm(
            self.spm_path, ["M_leaf_parsley_02"], self.groups
        )
        manifest["generator_connection"] = connection

        result = speedtree.remove_atlas_scope_assets_from_spm(
            self.spm_path, [manifest]
        )

        self.assertTrue(result["changed"])
        self.assertEqual(len(result["restored_generator_slots"]), 4)
        self.assertEqual(
            generator_values(self.spm_path),
            {
                ("Leaf", "Leaves:Type:0"): (4, 6),
                ("Leaf", "Leaves:Type:1"): (4, 7),
                ("Leaf 2", "Leaves:Type:0"): (4, 9),
                ("Frond", "Material:Frond:0"): (4, 10),
            },
        )
        assets = speedtree.read_spm_xml(self.spm_path).find("Assets")
        self.assertIsNotNone(assets.find("Material_v8[@ID='4']"))
        for material_id in (7, 8, 9):
            self.assertIsNone(assets.find(f"Material_v8[@ID='{material_id}']"))
        for mesh_id in (63, 64, 70, 71, 78):
            self.assertIsNone(assets.find(f"Mesh[@ID='{mesh_id}']"))

    def test_target_removal_detaches_unassigned_without_original_binding(self):
        manifest = {
            "export_scope_id": "scope-a",
            "speedtree_material_groups": self.groups,
            "mesh_ids": [63, 64, 70, 71, 78],
            "meshes": [],
        }
        root = speedtree.read_spm_xml(self.spm_path)
        assets = root.find("Assets")
        for material_id in (7, 8, 9):
            speedtree.tag_spm_asset(
                assets.find(f"Material_v8[@ID='{material_id}']"), manifest, "material"
            )
        for mesh_id in (63, 64, 70, 71, 78):
            speedtree.tag_spm_asset(
                assets.find(f"Mesh[@ID='{mesh_id}']"), manifest, "mesh"
            )
        write_spm(self.spm_path, root)
        speedtree.connect_atlas_generators_in_spm(
            self.spm_path, ["M_leaf_parsley_02"], self.groups
        )
        second = speedtree.connect_atlas_generators_in_spm(
            self.spm_path, ["M_leaf_parsley_02"], self.groups
        )
        manifest["generator_connection"] = second
        result = speedtree.remove_atlas_scope_assets_from_spm(self.spm_path, [manifest])

        self.assertTrue(result["changed"])
        self.assertTrue(all(
            binding["mode"].startswith("detached_unassigned")
            for binding in result["restored_generator_slots"]
        ))
        self.assertEqual(
            set(generator_values(self.spm_path).values()),
            {(-1, -10)},
        )
        assets = speedtree.read_spm_xml(self.spm_path).find("Assets")
        for material_id in (7, 8, 9):
            self.assertIsNone(assets.find(f"Material_v8[@ID='{material_id}']"))

    def test_missing_required_leaf_ordinal_fails_before_write(self):
        before = self.spm_path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "leaf_05"):
            speedtree.connect_atlas_generators_in_spm(
                self.spm_path, ["M_leaf_parsley_02"], self.groups[:-1]
            )
        self.assertEqual(self.spm_path.read_bytes(), before)

    def test_ladyfern_mesh_minus_ten_updates_material_only(self):
        root = speedtree.read_spm_xml(self.spm_path)
        assets = root.find("Assets")
        add_material(assets, 10, "cluster_ladyfern_02", [1, 2, 3, 13])
        for mesh_id in [1, 2, 3, 13]:
            add_mesh(assets, mesh_id)
        add_generator(root, "Frond", "Frond 2", [(10, -10)])
        write_spm(self.spm_path, root)
        result = speedtree.connect_atlas_generators_in_spm(
            self.spm_path, ["cluster_ladyfern_02"], self.groups
        )
        binding = next(item for item in result["bindings"] if item["generator_name"] == "Frond 2")
        self.assertEqual(binding["target_material_id"], 7)
        self.assertEqual(binding["target_mesh_id"], -10)
        self.assertEqual(binding["sentinel_policy"], "material_default_mesh_preserved")
        self.assertEqual(
            generator_values(self.spm_path)[("Frond 2", "Material:Frond:0")],
            (7, -10),
        )

    def test_frond_without_mesh_property_updates_material_only(self):
        target = Path(self.temp_dir.name) / "material_only_frond.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [5])
        add_material(assets, 7, "M_branch_atlas", [63])
        add_mesh(assets, 5)
        add_mesh(assets, 63)
        generator = add_generator(root, "Frond", "Frond material", [(4, -10)])
        properties = generator.find("Properties")
        mesh_property = next(
            prop
            for prop in list(properties)
            if prop.findtext("Name") == "Material:Frond:0:Mesh"
        )
        properties.remove(mesh_property)
        write_spm(target, root)
        groups = [{
            "material": "M_branch_atlas",
            "material_id": 7,
            "mesh_ids": [63],
            "meshes": [{"source_object": "leaf_01", "source_ordinal": 1}],
        }]

        result = speedtree.connect_atlas_generators_in_spm(
            target, ["M_branch"], groups
        )

        pair = speedtree.spm_generator_property_pairs(
            speedtree.read_spm_xml(target), {"Frond"}
        )[0]
        self.assertEqual(pair["material_property"].findtext("Value"), "7")
        self.assertIsNone(pair["mesh_property"])
        self.assertEqual(result["bindings"][0]["target_mesh_id"], -10)

    def test_duplicate_source_name_and_mismatched_id_list_fail(self):
        with self.assertRaisesRegex(RuntimeError, "duplicated"):
            speedtree.connect_atlas_generators_in_spm(
                self.spm_path,
                ["M_leaf_parsley_02", "M_leaf_parsley_02"],
                self.groups,
            )
        with self.assertRaisesRegex(RuntimeError, "count does not match"):
            speedtree.connect_atlas_generators_in_spm(
                self.spm_path, ["M_leaf_parsley_02"], self.groups, [4, 5]
            )

    def test_variant_coverage_opt_out_preserves_single_authored_frond_slot(self):
        target = Path(self.temp_dir.name) / "opt_out.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        add_material(assets, 8, "M_branch_generated", [10, 11, 12])
        for mesh_id in (1, 2, 3, 10, 11, 12):
            add_mesh(assets, mesh_id)
        add_variant_generator(root, "Frond", "Frond 36", [(4, 1)])
        write_spm(target, root)
        groups = [
            {
                "material": "M_branch_generated",
                "material_id": 8,
                "mesh_ids": [10, 11, 12],
                "meshes": [
                    {
                        "source_object": f"branch_elm_01_{ordinal:02d}",
                        "source_ordinal": ordinal,
                    }
                    for ordinal in range(1, 4)
                ],
            }
        ]

        result = speedtree.connect_atlas_generators_in_spm(
            target, ["M_branch"], groups, [4]
        )

        self.assertEqual(
            result["generator_variant_policy"], "preserve_existing_slots"
        )
        self.assertEqual(result["created_slot_pairs"], 0)
        self.assertEqual(
            generator_values(target),
            {("Frond 36", "Material:Frond:0"): (8, 10)},
        )
        generator = next(speedtree.read_spm_xml(target).iter("Generator"))
        self.assertEqual(
            speedtree.generator_variant_parent_state(
                generator, "Material:Frond"
            )["child_count"],
            1,
        )

    def test_variant_coverage_rebinds_extra_source_ordinals_to_generated_output(self):
        target = Path(self.temp_dir.name) / "partial_outputs.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        add_material(assets, 8, "M_branch_generated", [10])
        for mesh_id in (1, 2, 3, 10):
            add_mesh(assets, mesh_id)
        add_variant_generator(
            root,
            "Frond",
            "Frond 36",
            [(4, 1), (4, 2), (4, 3)],
        )
        write_spm(target, root)
        groups = [
            {
                "material": "M_branch_generated",
                "material_id": 8,
                "mesh_ids": [10],
                "meshes": [
                    {
                        "source_object": "branch_elm_01_01",
                        "source_ordinal": 1,
                    }
                ],
            }
        ]

        result = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups,
            [4],
            generator_variant_policy="ensure_all_material_cutouts",
        )

        self.assertEqual(result["created_slot_pairs"], 0)
        self.assertEqual(len(result["bindings"]), 3)
        self.assertEqual(
            generator_values(target),
            {
                ("Frond 36", "Material:Frond:0"): (8, 10),
                ("Frond 36", "Material:Frond:1"): (8, 10),
                ("Frond 36", "Material:Frond:2"): (8, 10),
            },
        )
        fallback_bindings = [
            binding
            for binding in result["bindings"]
            if binding.get("sentinel_policy")
            == "source_ordinal_without_output_to_first_generated"
        ]
        self.assertEqual(len(fallback_bindings), 2)

    def test_previous_fallback_bindings_retarget_to_fresh_first_output(self):
        target = Path(self.temp_dir.name) / "partial_output_refresh.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        add_material(assets, 8, "M_branch_generated", [10, 20])
        for mesh_id in (1, 2, 3, 10, 20):
            add_mesh(assets, mesh_id)
        add_variant_generator(
            root,
            "Frond",
            "Frond 36",
            [(8, 10), (8, 10), (8, 10)],
        )
        write_spm(target, root)
        previous = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            [{
                "material": "M_branch_generated",
                "material_id": 8,
                "mesh_ids": [10],
                "meshes": [{
                    "source_object": "branch_elm_01_01",
                    "source_ordinal": 1,
                }],
            }],
            [4],
            previous_bindings=[
                {
                    "generator_index": 0,
                    "generator_name": "Frond 36",
                    "generator_guid": speedtree.generator_guid(
                        next(root.iter("Generator"))
                    ),
                    "generator_type": "Frond",
                    "slot_prefix": f"Material:Frond:{index}",
                    "source_material_id": 4,
                    "source_material_name": "M_branch",
                    "source_mesh_id": source_mesh_id,
                    "sentinel_policy": (
                        None
                        if index == 0
                        else "source_ordinal_without_output_to_first_generated"
                    ),
                    "target_material_id": 8,
                    "target_mesh_id": 10,
                    "leaf_ordinal": 1,
                    "created_slot": False,
                }
                for index, source_mesh_id in enumerate((1, 2, 3))
            ],
            source_mesh_ids_by_name={"M_branch": [1, 2, 3]},
            generator_variant_policy="ensure_all_material_cutouts",
        )
        refreshed_groups = [{
            "material": "M_branch_generated",
            "material_id": 8,
            "mesh_ids": [20],
            "meshes": [{
                "source_object": "branch_elm_01_01",
                "source_ordinal": 1,
            }],
        }]

        refreshed = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            refreshed_groups,
            [4],
            previous_bindings=previous["bindings"],
            source_mesh_ids_by_name={"M_branch": [1, 2, 3]},
            generator_variant_policy="ensure_all_material_cutouts",
        )

        self.assertEqual(
            generator_values(target),
            {
                ("Frond 36", "Material:Frond:0"): (8, 20),
                ("Frond 36", "Material:Frond:1"): (8, 20),
                ("Frond 36", "Material:Frond:2"): (8, 20),
            },
        )
        self.assertEqual(
            [
                binding["source_mesh_id"]
                for binding in refreshed["bindings"]
            ],
            [1, 2, 3],
        )
        self.assertEqual(
            [
                binding.get("sentinel_policy")
                for binding in refreshed["bindings"]
            ],
            [
                None,
                "source_ordinal_without_output_to_first_generated",
                "source_ordinal_without_output_to_first_generated",
            ],
        )

    def test_previous_fallback_binding_upgrades_when_output_now_exists(self):
        target = Path(self.temp_dir.name) / "expanded_output_refresh.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2])
        add_material(assets, 8, "M_branch_generated", [10, 20, 21])
        for mesh_id in (1, 2, 10, 20, 21):
            add_mesh(assets, mesh_id)
        generator = add_variant_generator(
            root,
            "Frond",
            "Frond 36",
            [(8, 10)],
        )
        write_spm(target, root)
        previous = [{
            "generator_index": 0,
            "generator_name": "Frond 36",
            "generator_guid": speedtree.generator_guid(generator),
            "generator_type": "Frond",
            "slot_prefix": "Material:Frond:0",
            "source_material_id": 4,
            "source_material_name": "M_branch",
            "source_mesh_id": 2,
            "sentinel_policy": (
                "source_ordinal_without_output_to_first_generated"
            ),
            "target_material_id": 8,
            "target_mesh_id": 10,
            "leaf_ordinal": 1,
            "created_slot": False,
        }]

        refreshed = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            [{
                "material": "M_branch_generated",
                "material_id": 8,
                "mesh_ids": [20, 21],
                "meshes": [
                    {
                        "source_object": f"branch_elm_01_{ordinal:02d}",
                        "source_ordinal": ordinal,
                    }
                    for ordinal in (1, 2)
                ],
            }],
            [4],
            previous_bindings=previous,
            source_mesh_ids_by_name={"M_branch": [1, 2]},
            generator_variant_policy="ensure_all_material_cutouts",
        )

        self.assertEqual(
            generator_values(target),
            {
                ("Frond 36", "Material:Frond:0"): (8, 21),
                ("Frond 36", "Material:Frond:1"): (8, 20),
            },
        )
        upgraded = next(
            binding
            for binding in refreshed["bindings"]
            if binding["slot_prefix"] == "Material:Frond:0"
        )
        self.assertEqual(upgraded["leaf_ordinal"], 2)
        self.assertIsNone(upgraded["sentinel_policy"])

    def test_leaf_mesh_extra_source_ordinals_rebind_to_generated_output(self):
        target = Path(self.temp_dir.name) / "leaf_partial_outputs.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_leaf", [1, 2, 3])
        add_material(assets, 8, "M_leaf_generated", [10])
        for mesh_id in (1, 2, 3, 10):
            add_mesh(assets, mesh_id)
        add_variant_generator(
            root,
            "Leaf Mesh",
            "Leaf 63",
            [(4, 1), (4, 2), (4, 3)],
        )
        write_spm(target, root)
        groups = [
            {
                "material": "M_leaf_generated",
                "material_id": 8,
                "mesh_ids": [10],
                "meshes": [
                    {
                        "source_object": "leaf_elm_01_01",
                        "source_ordinal": 1,
                    }
                ],
            }
        ]

        result = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_leaf"],
            groups,
            [4],
            generator_variant_policy="ensure_all_material_cutouts",
        )

        self.assertTrue(result["complete"])
        self.assertEqual(
            generator_values(target),
            {
                ("Leaf 63", "Leaves:Type:0"): (8, 10),
                ("Leaf 63", "Leaves:Type:1"): (8, 10),
                ("Leaf 63", "Leaves:Type:2"): (8, 10),
            },
        )

    def test_partial_source_adoption_rebinds_slots_and_deletes_all_source_cutouts(self):
        target = Path(self.temp_dir.name) / "partial_adoption.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        for mesh_id in (1, 2, 3, 10):
            add_mesh(assets, mesh_id)
        add_variant_generator(
            root,
            "Frond",
            "Frond 36",
            [(4, 1), (4, 2), (4, 3)],
        )
        write_spm(target, root)

        adoption = speedtree.prepare_source_material_adoption(
            target,
            {"export_scope_id": "scope-partial-adoption"},
            "M_branch",
            4,
        )
        staged_root = speedtree.read_spm_xml(target)
        staged_material = staged_root.find("Assets/Material_v8[@ID='4']")
        speedtree.update_spm_material_mesh_ids(staged_material, [10])
        speedtree.write_spm_xml(target, staged_root)
        groups = [
            {
                "material": "M_branch",
                "material_id": 4,
                "mesh_ids": [10],
                "meshes": [
                    {
                        "source_object": "branch_elm_01_01",
                        "source_ordinal": 1,
                    }
                ],
            }
        ]
        connection = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups,
            [4],
            source_mesh_ids_by_name={"M_branch": [1, 2, 3]},
            generator_variant_policy="ensure_all_material_cutouts",
        )
        adoption = speedtree.finalize_source_material_adoption(
            target,
            adoption,
            [10],
            connection,
        )

        final_root = speedtree.read_spm_xml(target)
        final_assets = final_root.find("Assets")
        final_material = final_assets.find("Material_v8[@ID='4']")
        self.assertEqual(
            speedtree.spm_material_mesh_ids(final_material),
            [10],
        )
        self.assertIsNone(final_assets.find("Mesh[@ID='1']"))
        self.assertIsNone(final_assets.find("Mesh[@ID='2']"))
        self.assertIsNone(final_assets.find("Mesh[@ID='3']"))
        self.assertIsNotNone(final_assets.find("Mesh[@ID='10']"))
        self.assertEqual(adoption["removed_original_mesh_ids"], [1, 2, 3])
        self.assertEqual(adoption["preserved_original_mesh_ids"], [])
        self.assertEqual(adoption["final_material_mesh_ids"], [10])
        self.assertEqual(
            generator_values(target),
            {
                ("Frond 36", "Material:Frond:0"): (4, 10),
                ("Frond 36", "Material:Frond:1"): (4, 10),
                ("Frond 36", "Material:Frond:2"): (4, 10),
            },
        )

    def test_shared_original_mesh_asset_is_detached_without_breaking_other_material(self):
        target = Path(self.temp_dir.name) / "shared_source_mesh_adoption.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        add_material(assets, 9, "M_other", [2])
        for mesh_id in (1, 2, 3, 10):
            add_mesh(assets, mesh_id)
        add_variant_generator(
            root,
            "Frond",
            "Frond source",
            [(4, 1), (4, 2), (4, 3)],
        )
        add_variant_generator(
            root,
            "Frond",
            "Frond other",
            [(9, 2)],
        )
        write_spm(target, root)

        adoption = speedtree.prepare_source_material_adoption(
            target,
            {"export_scope_id": "scope-shared-source-mesh"},
            "M_branch",
            4,
        )
        staged_root = speedtree.read_spm_xml(target)
        staged_material = staged_root.find("Assets/Material_v8[@ID='4']")
        speedtree.update_spm_material_mesh_ids(staged_material, [10])
        speedtree.write_spm_xml(target, staged_root)
        connection = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            [
                {
                    "material": "M_branch",
                    "material_id": 4,
                    "mesh_ids": [10],
                    "meshes": [
                        {
                            "source_object": "branch_01_01",
                            "source_ordinal": 1,
                        }
                    ],
                }
            ],
            [4],
            source_mesh_ids_by_name={"M_branch": [1, 2, 3]},
            generator_variant_policy="ensure_all_material_cutouts",
        )
        adoption = speedtree.finalize_source_material_adoption(
            target,
            adoption,
            [10],
            connection,
        )

        final_root = speedtree.read_spm_xml(target)
        final_assets = final_root.find("Assets")
        source_material = final_assets.find("Material_v8[@ID='4']")
        other_material = final_assets.find("Material_v8[@ID='9']")
        self.assertEqual(
            speedtree.spm_material_mesh_ids(source_material),
            [10],
        )
        self.assertEqual(
            speedtree.spm_material_mesh_ids(other_material),
            [2],
        )
        self.assertIsNone(final_assets.find("Mesh[@ID='1']"))
        self.assertIsNotNone(final_assets.find("Mesh[@ID='2']"))
        self.assertIsNone(final_assets.find("Mesh[@ID='3']"))
        self.assertEqual(adoption["removed_original_mesh_ids"], [1, 3])
        self.assertEqual(
            adoption["retained_shared_original_mesh_ids"],
            [2],
        )

    def test_frond_variant_creation_clones_and_groups_every_slot_property(self):
        target = Path(self.temp_dir.name) / "frond_weighted.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        add_material(assets, 8, "M_branch_generated", [10, 11, 12])
        for mesh_id in (1, 2, 3, 10, 11, 12):
            add_mesh(assets, mesh_id)
        generator = add_variant_generator(
            root, "Frond", "Frond 36", [(4, 1)]
        )
        weight = ET.SubElement(generator.find("Properties"), "SplineProperty")
        ET.SubElement(weight, "Name").text = "Material:Frond:0:Weight"
        ET.SubElement(weight, "Value").text = "0.75"
        curve = ET.SubElement(weight, "Curve")
        ET.SubElement(curve, "Point").text = "0 0.75"
        write_spm(target, root)
        groups = [
            {
                "material": "M_branch_generated",
                "material_id": 8,
                "mesh_ids": [10, 11, 12],
                "meshes": [
                    {
                        "source_object": f"branch_elm_01_{ordinal:02d}",
                        "source_ordinal": ordinal,
                    }
                    for ordinal in range(1, 4)
                ],
            }
        ]

        result = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups,
            [4],
            generator_variant_policy="ensure_all_material_cutouts",
        )

        self.assertEqual(result["created_slot_pairs"], 2)
        parsed_generator = next(speedtree.read_spm_xml(target).iter("Generator"))
        property_nodes = list(parsed_generator.find("Properties"))
        self.assertEqual(
            [node.findtext("Name") for node in property_nodes],
            [
                "Material:Frond",
                "Material:Frond:0:Material",
                "Material:Frond:0:Mesh",
                "Material:Frond:0:Weight",
                "Material:Frond:1:Material",
                "Material:Frond:1:Mesh",
                "Material:Frond:1:Weight",
                "Material:Frond:2:Material",
                "Material:Frond:2:Mesh",
                "Material:Frond:2:Weight",
            ],
        )
        for index in range(3):
            weight_node = next(
                node
                for node in property_nodes
                if node.findtext("Name")
                == f"Material:Frond:{index}:Weight"
            )
            self.assertEqual(weight_node.tag, "SplineProperty")
            self.assertEqual(weight_node.findtext("Value"), "0.75")
            self.assertEqual(weight_node.findtext("./Curve/Point"), "0 0.75")
        created = [
            binding for binding in result["bindings"]
            if binding["created_slot"]
        ]
        self.assertEqual(
            [binding["created_property_names"] for binding in created],
            [
                [
                    "Material:Frond:1:Material",
                    "Material:Frond:1:Mesh",
                    "Material:Frond:1:Weight",
                ],
                [
                    "Material:Frond:2:Material",
                    "Material:Frond:2:Mesh",
                    "Material:Frond:2:Weight",
                ],
            ],
        )

    def test_generated_output_beyond_source_cutouts_creates_a_tail_slot(self):
        target = Path(self.temp_dir.name) / "frond_four_outputs_three_cutouts.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        add_material(assets, 8, "M_branch_generated", [10, 11, 12, 13])
        for mesh_id in (1, 2, 3, 10, 11, 12, 13):
            add_mesh(assets, mesh_id)
        add_variant_generator(
            root, "Frond", "Frond 36", [(4, 1), (4, 2), (4, 3)]
        )
        write_spm(target, root)
        groups = [
            {
                "material": "M_branch_generated",
                "material_id": 8,
                "mesh_ids": [10, 11, 12, 13],
                "meshes": [
                    {
                        "source_object": f"branch_densiflora_01_{ordinal:02d}",
                        "source_ordinal": ordinal,
                    }
                    for ordinal in range(1, 5)
                ],
            }
        ]

        result = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups,
            [4],
            generator_variant_policy="ensure_all_material_cutouts",
        )

        self.assertTrue(result["complete"])
        self.assertEqual(result["created_slot_pairs"], 1)
        self.assertEqual(
            generator_values(target),
            {
                ("Frond 36", "Material:Frond:0"): (8, 10),
                ("Frond 36", "Material:Frond:1"): (8, 11),
                ("Frond 36", "Material:Frond:2"): (8, 12),
                ("Frond 36", "Material:Frond:3"): (8, 13),
            },
        )
        created = [
            binding
            for binding in result["bindings"]
            if binding["created_slot"]
        ]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["leaf_ordinal"], 4)
        self.assertIsNone(created[0]["source_mesh_id"])
        self.assertEqual(
            created[0]["sentinel_policy"],
            "created_variant_without_source_cutout",
        )

    def test_existing_created_tail_slots_expand_from_three_to_four_outputs(self):
        target = Path(self.temp_dir.name) / "frond_incremental_fourth_output.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        add_material(assets, 8, "M_branch_generated", [10, 11, 12, 13])
        for mesh_id in (1, 2, 3, 10, 11, 12, 13):
            add_mesh(assets, mesh_id)
        add_variant_generator(root, "Frond", "Frond 36", [(4, 1)])
        write_spm(target, root)

        def groups(count):
            return [
                {
                    "material": "M_branch_generated",
                    "material_id": 8,
                    "mesh_ids": [10, 11, 12, 13][:count],
                    "meshes": [
                        {
                            "source_object": (
                                f"branch_densiflora_01_{ordinal:02d}"
                            ),
                            "source_ordinal": ordinal,
                        }
                        for ordinal in range(1, count + 1)
                    ],
                }
            ]

        first = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups(3),
            [4],
            generator_variant_policy="ensure_all_material_cutouts",
        )
        second = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups(4),
            [4],
            previous_bindings=first["bindings"],
            source_mesh_ids_by_name={"M_branch": [1, 2, 3]},
            generator_variant_policy="ensure_all_material_cutouts",
        )

        self.assertTrue(second["complete"])
        self.assertEqual(second["created_slot_pairs"], 1)
        self.assertEqual(
            generator_values(target),
            {
                ("Frond 36", "Material:Frond:0"): (8, 10),
                ("Frond 36", "Material:Frond:1"): (8, 11),
                ("Frond 36", "Material:Frond:2"): (8, 12),
                ("Frond 36", "Material:Frond:3"): (8, 13),
            },
        )
        created = [
            binding
            for binding in second["bindings"]
            if binding["created_slot"]
        ]
        self.assertEqual(len(created), 3)
        self.assertEqual(
            {
                (
                    binding["variant_parent_children_before"],
                    binding["variant_parent_children_after"],
                )
                for binding in created
            },
            {(1, 4)},
        )

    def test_source_adoption_appends_generated_outputs_beyond_cutout_count(self):
        target = Path(self.temp_dir.name) / "frond_adoption_four_outputs.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        for mesh_id in (1, 2, 3, 10, 11, 12, 13):
            add_mesh(assets, mesh_id)
        add_variant_generator(
            root, "Frond", "Frond 36", [(4, 1), (4, 2), (4, 3)]
        )
        write_spm(target, root)

        adoption = speedtree.prepare_source_material_adoption(
            target,
            {"export_scope_id": "scope-four-output-adoption"},
            "M_branch",
            4,
        )
        staged_root = speedtree.read_spm_xml(target)
        staged_material = staged_root.find("Assets/Material_v8[@ID='4']")
        speedtree.update_spm_material_mesh_ids(
            staged_material, [10, 11, 12, 13]
        )
        speedtree.write_spm_xml(target, staged_root)
        groups = [
            {
                "material": "M_branch",
                "material_id": 4,
                "mesh_ids": [10, 11, 12, 13],
                "meshes": [
                    {
                        "source_object": f"branch_densiflora_01_{ordinal:02d}",
                        "source_ordinal": ordinal,
                    }
                    for ordinal in range(1, 5)
                ],
            }
        ]
        connection = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups,
            [4],
            source_mesh_ids_by_name={"M_branch": [1, 2, 3]},
            generator_variant_policy="ensure_all_material_cutouts",
        )
        adoption = speedtree.finalize_source_material_adoption(
            target,
            adoption,
            [10, 11, 12, 13],
            connection,
        )

        final_root = speedtree.read_spm_xml(target)
        final_assets = final_root.find("Assets")
        final_material = final_assets.find("Material_v8[@ID='4']")
        self.assertEqual(
            speedtree.spm_material_mesh_ids(final_material),
            [10, 11, 12, 13],
        )
        self.assertEqual(
            generator_values(target),
            {
                ("Frond 36", "Material:Frond:0"): (4, 10),
                ("Frond 36", "Material:Frond:1"): (4, 11),
                ("Frond 36", "Material:Frond:2"): (4, 12),
                ("Frond 36", "Material:Frond:3"): (4, 13),
            },
        )
        self.assertEqual(
            adoption["final_material_mesh_ids"],
            [10, 11, 12, 13],
        )
        self.assertEqual(adoption["removed_original_mesh_ids"], [1, 2, 3])

    def test_broken_frond_variant_schema_migrates_idempotently_and_removes(self):
        target = Path(self.temp_dir.name) / "frond_broken_generated.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        add_material(assets, 8, "M_branch_generated", [10, 11, 12])
        for mesh_id in (1, 2, 3, 10, 11, 12):
            add_mesh(assets, mesh_id)
        generator = add_variant_generator(
            root,
            "Frond",
            "Frond 36",
            [(8, 10), (8, 11), (8, 12)],
        )
        properties = generator.find("Properties")
        weight = ET.SubElement(properties, "SplineProperty")
        ET.SubElement(weight, "Name").text = "Material:Frond:0:Weight"
        ET.SubElement(weight, "Value").text = "0.625"
        curve = ET.SubElement(weight, "Curve")
        ET.SubElement(curve, "Point").text = "0.25 0.625"
        width = ET.SubElement(properties, "SplineProperty")
        ET.SubElement(width, "Name").text = "Shape:Scale:Width"
        ET.SubElement(width, "Value").text = "1"
        original_weight_signature = (
            weight.tag,
            weight.findtext("Name"),
            weight.findtext("Value"),
            weight.findtext("./Curve/Point"),
        )
        write_spm(target, root)
        groups = [
            {
                "material": "M_branch_generated",
                "material_id": 8,
                "mesh_ids": [10, 11, 12],
                "meshes": [
                    {
                        "source_object": f"branch_elm_01_{ordinal:02d}",
                        "source_ordinal": ordinal,
                    }
                    for ordinal in range(1, 4)
                ],
            }
        ]
        previous_bindings = []
        for slot_index, source_mesh_id, target_mesh_id in (
            (1, 2, 11),
            (2, 3, 12),
        ):
            slot_prefix = f"Material:Frond:{slot_index}"
            previous_bindings.append(
                {
                    "generator_index": 0,
                    "generator_name": "Frond 36",
                    "generator_type": "Frond",
                    "slot_prefix": slot_prefix,
                    "source_material_id": 4,
                    "source_material_name": "M_branch",
                    "source_mesh_id": source_mesh_id,
                    "target_material_id": 8,
                    "target_mesh_id": target_mesh_id,
                    "leaf_ordinal": slot_index + 1,
                    "created_slot": True,
                    "variant_parent_property": "Material:Frond",
                    "variant_parent_children_before": 1,
                    "variant_parent_children_after": 3,
                    "created_material_property": f"{slot_prefix}:Material",
                    "created_mesh_property": f"{slot_prefix}:Mesh",
                }
            )

        first = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups,
            [4],
            previous_bindings=previous_bindings,
            generator_variant_policy="ensure_all_material_cutouts",
        )

        self.assertEqual(first["created_slot_pairs"], 0)
        self.assertEqual(
            [
                item["added_property_names"]
                for item in first["repaired_variant_slot_schemas"]
            ],
            [
                ["Material:Frond:1:Weight"],
                ["Material:Frond:2:Weight"],
            ],
        )
        parsed_generator = next(speedtree.read_spm_xml(target).iter("Generator"))
        property_nodes = list(parsed_generator.find("Properties"))
        self.assertEqual(
            [node.findtext("Name") for node in property_nodes],
            [
                "Material:Frond",
                "Material:Frond:0:Material",
                "Material:Frond:0:Mesh",
                "Material:Frond:0:Weight",
                "Material:Frond:1:Material",
                "Material:Frond:1:Mesh",
                "Material:Frond:1:Weight",
                "Material:Frond:2:Material",
                "Material:Frond:2:Mesh",
                "Material:Frond:2:Weight",
                "Shape:Scale:Width",
            ],
        )
        preserved_weight = next(
            node
            for node in property_nodes
            if node.findtext("Name") == "Material:Frond:0:Weight"
        )
        self.assertEqual(
            (
                preserved_weight.tag,
                preserved_weight.findtext("Name"),
                preserved_weight.findtext("Value"),
                preserved_weight.findtext("./Curve/Point"),
            ),
            original_weight_signature,
        )
        self.assertEqual(
            next(
                node
                for node in property_nodes
                if node.findtext("Name") == "Shape:Scale:Width"
            ).findtext("Value"),
            "1",
        )

        second = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups,
            [4],
            previous_bindings=first["bindings"],
            generator_variant_policy="ensure_all_material_cutouts",
        )
        self.assertTrue(
            all(
                not item["added_property_names"] and not item["reordered"]
                for item in second["repaired_variant_slot_schemas"]
            )
        )

        removal_root = speedtree.read_spm_xml(target)
        removed = speedtree.remove_created_generator_variant_slots(
            removal_root,
            second["bindings"],
        )
        removal_generator = next(removal_root.iter("Generator"))
        removal_state = speedtree.generator_variant_parent_state(
            removal_generator,
            "Material:Frond",
        )
        self.assertEqual(len(removed), 2)
        self.assertEqual(removal_state["child_count"], 1)
        self.assertEqual(
            [
                node.findtext("Name")
                for node in removal_generator.find("Properties")
            ],
            [
                "Material:Frond",
                "Material:Frond:0:Material",
                "Material:Frond:0:Mesh",
                "Material:Frond:0:Weight",
                "Shape:Scale:Width",
            ],
        )

    def test_owned_variant_interval_preserves_later_scope_slots(self):
        root = ET.Element("SpeedTreeModel")
        generator = add_variant_generator(
            root,
            "Frond",
            "Frond 36",
            [
                (4, 1),
                (8, 11),
                (8, 12),
                (9, 21),
                (9, 22),
            ],
        )
        properties = generator.find("Properties")
        for slot_index, value in ((0, "1.0"), (3, "later-a"), (4, "later-b")):
            prop = ET.SubElement(properties, "Property")
            ET.SubElement(prop, "Name").text = (
                f"Material:Frond:{slot_index}:Weight"
            )
            ET.SubElement(prop, "Value").text = value
        bindings = []
        for slot_index, source_mesh_id, target_mesh_id in (
            (1, 2, 11),
            (2, 3, 12),
        ):
            slot_prefix = f"Material:Frond:{slot_index}"
            bindings.append({
                "generator_index": 0,
                "generator_name": "Frond 36",
                "generator_guid": speedtree.generator_guid(generator),
                "generator_type": "Frond",
                "slot_prefix": slot_prefix,
                "source_material_id": 4,
                "source_material_name": "M_branch",
                "source_mesh_id": source_mesh_id,
                "target_material_id": 8,
                "target_mesh_id": target_mesh_id,
                "leaf_ordinal": slot_index + 1,
                "created_slot": True,
                "variant_parent_property": "Material:Frond",
                "variant_parent_children_before": 1,
                "variant_parent_children_after": 3,
                "created_material_property": f"{slot_prefix}:Material",
                "created_mesh_property": f"{slot_prefix}:Mesh",
            })

        def later_scope_signature():
            return [
                ET.tostring(prop, encoding="unicode")
                for prop in generator.find("Properties")
                if str(prop.findtext("Name") or "").startswith(
                    ("Material:Frond:3:", "Material:Frond:4:")
                )
            ]

        later_before = later_scope_signature()
        repaired = speedtree.repair_created_generator_variant_slots(
            root,
            bindings,
        )
        state = speedtree.generator_variant_parent_state(
            generator,
            "Material:Frond",
        )

        self.assertEqual(state["child_count"], 5)
        self.assertEqual(len(repaired), 2)
        self.assertEqual(later_scope_signature(), later_before)

        restored = speedtree.remove_created_generator_variant_slots(
            root,
            bindings,
        )
        state = speedtree.generator_variant_parent_state(
            generator,
            "Material:Frond",
        )
        self.assertEqual(state["child_count"], 5)
        self.assertEqual(
            (
                int(state["slots"][1]["material"].findtext("Value")),
                int(state["slots"][1]["mesh"].findtext("Value")),
                int(state["slots"][2]["material"].findtext("Value")),
                int(state["slots"][2]["mesh"].findtext("Value")),
            ),
            (4, 2, 4, 3),
        )
        self.assertEqual(later_scope_signature(), later_before)
        self.assertEqual(
            {
                item["mode"] for item in restored
            },
            {
                "restored_created_variant_slot_"
                "preserving_later_scope_interval"
            },
        )

    def test_legacy_created_variant_binding_follows_generator_after_reorder(self):
        target = Path(self.temp_dir.name) / "reordered_generators.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        add_material(assets, 7, "M_leaf", [5, 6])
        add_material(assets, 8, "M_branch_generated", [10, 11, 12])
        for mesh_id in (1, 2, 3, 5, 6, 10, 11, 12):
            add_mesh(assets, mesh_id)
        add_variant_generator(
            root,
            "Leaf Mesh",
            "Leaf 63",
            [(7, 5), (7, 6), (7, 6)],
        )
        add_variant_generator(
            root,
            "Frond",
            "Frond 36",
            [(8, 10), (8, 11), (8, 12)],
        )
        write_spm(target, root)
        groups = [{
            "material": "M_branch_generated",
            "material_id": 8,
            "mesh_ids": [10, 11, 12],
            "meshes": [
                {
                    "source_object": f"branch_elm_01_{ordinal:02d}",
                    "source_ordinal": ordinal,
                }
                for ordinal in range(1, 4)
            ],
        }]
        legacy_bindings = []
        for slot_index, source_mesh_id, target_mesh_id in (
            (1, 2, 11),
            (2, 3, 12),
        ):
            slot_prefix = f"Material:Frond:{slot_index}"
            legacy_bindings.append({
                # Stale pre-GUID index: current index 0 is Leaf 63.
                "generator_index": 0,
                "generator_name": "Frond 36",
                "generator_type": "Frond",
                "slot_prefix": slot_prefix,
                "source_material_id": 4,
                "source_material_name": "M_branch",
                "source_mesh_id": source_mesh_id,
                "target_material_id": 8,
                "target_mesh_id": target_mesh_id,
                "leaf_ordinal": slot_index + 1,
                "created_slot": True,
                "variant_parent_property": "Material:Frond",
                "variant_parent_children_before": 1,
                "variant_parent_children_after": 3,
                "created_material_property": f"{slot_prefix}:Material",
                "created_mesh_property": f"{slot_prefix}:Mesh",
                "created_property_names": [
                    f"{slot_prefix}:Material",
                    f"{slot_prefix}:Mesh",
                ],
            })

        connection = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups,
            [4],
            previous_bindings=legacy_bindings,
            generator_variant_policy="ensure_all_material_cutouts",
        )

        created = [
            binding
            for binding in connection["bindings"]
            if binding["created_slot"]
        ]
        self.assertEqual(
            {binding["generator_index"] for binding in created},
            {1},
        )
        self.assertEqual(
            {binding["generator_guid"] for binding in created},
            {"Frond:Frond 36"},
        )
        self.assertEqual(
            generator_values(target)[("Leaf 63", "Leaves:Type:0")],
            (7, 5),
        )

        reordered = speedtree.read_spm_xml(target)
        generators = list(reordered.iter("Generator"))
        leaf = next(
            generator
            for generator in generators
            if generator.findtext("Name") == "Leaf 63"
        )
        frond = next(
            generator
            for generator in generators
            if generator.findtext("Name") == "Frond 36"
        )
        reordered.remove(leaf)
        reordered.remove(frond)
        reordered.insert(1, frond)
        reordered.insert(2, leaf)
        removed = speedtree.remove_created_generator_variant_slots(
            reordered,
            created,
        )
        write_spm(target, reordered)

        self.assertEqual(
            {item["generator_guid"] for item in removed},
            {"Frond:Frond 36"},
        )
        parsed = speedtree.read_spm_xml(target)
        parsed_by_name = {
            generator.findtext("Name"): generator
            for generator in parsed.iter("Generator")
        }
        self.assertEqual(
            speedtree.generator_variant_parent_state(
                parsed_by_name["Frond 36"],
                "Material:Frond",
            )["child_count"],
            1,
        )
        self.assertEqual(
            speedtree.generator_variant_parent_state(
                parsed_by_name["Leaf 63"],
                "Leaves:Type",
            )["child_count"],
            3,
        )

    def test_missing_legacy_generator_binding_is_a_tombstone(self):
        root = ET.Element("SpeedTreeModel")
        add_variant_generator(
            root,
            "Frond",
            "Frond 29",
            [(8, 12)],
        )
        add_variant_generator(
            root,
            "Frond",
            "Frond 30",
            [(8, 12)],
        )
        stale = {
            "generator_index": 26,
            "generator_name": "Frond 27",
            "generator_type": "Frond",
            "slot_prefix": "Material:Frond:0",
            "source_material_id": 8,
            "source_mesh_id": 2,
            "target_material_id": 8,
            "target_mesh_id": 12,
        }

        self.assertEqual(
            speedtree.normalize_generator_bindings(
                root,
                [stale],
                context="Previous Atlas Generator binding",
                allow_missing=True,
            ),
            [],
        )
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            speedtree.normalize_generator_bindings(root, [stale])
        self.assertEqual(
            speedtree.normalize_generator_bindings(
                root,
                [{**stale, "created_slot": True}],
                allow_missing=True,
            ),
            [],
        )

    def test_missing_guid_generator_slot_is_a_tombstone(self):
        root = ET.Element("SpeedTreeModel")
        generator = add_variant_generator(
            root,
            "Leaf Mesh",
            "Leaf 9",
            [(8, 12)],
        )
        binding = {
            "generator_guid": speedtree.generator_guid(generator),
            "generator_index": 0,
            "generator_name": "Leaf 9",
            "generator_type": "Leaf Mesh",
            "slot_prefix": "Leaves:Type:3",
            "source_material_id": 8,
            "source_mesh_id": 2,
            "target_material_id": 8,
            "target_mesh_id": 12,
        }

        self.assertEqual(
            speedtree.normalize_generator_bindings(
                root,
                [binding],
                context="Atlas scope removal binding",
                allow_missing=True,
            ),
            [],
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "missing its Material/Mesh pair",
        ):
            speedtree.normalize_generator_bindings(root, [binding])

    def test_legacy_renamed_generator_migrates_by_unique_slot_values(self):
        root = ET.Element("SpeedTreeModel")
        add_variant_generator(
            root,
            "Frond",
            "Renamed Frond",
            [(8, 12)],
        )
        add_variant_generator(
            root,
            "Frond",
            "Other Frond",
            [(8, 13)],
        )
        legacy = {
            "generator_index": 26,
            "generator_name": "Frond 27",
            "generator_type": "Frond",
            "slot_prefix": "Material:Frond:0",
            "source_material_id": 8,
            "source_mesh_id": 2,
            "target_material_id": 8,
            "target_mesh_id": 12,
        }

        identity = speedtree.resolve_generator_binding(
            root,
            legacy,
            context="Previous Atlas Generator binding",
            allow_missing=True,
        )
        self.assertEqual(identity["generator_name"], "Renamed Frond")
        self.assertEqual(identity["generator_guid"], "Frond:Renamed Frond")
        self.assertEqual(identity["resolution"], "legacy_type_slot_values")

    def test_duplicate_legacy_names_resolve_by_unique_slot_values(self):
        root = ET.Element("SpeedTreeModel")
        add_variant_generator(
            root,
            "Frond",
            "Frond 6",
            [(8, 12)],
        )
        add_variant_generator(
            root,
            "Frond",
            "Frond 6",
            [(8, 13)],
        )
        legacy = {
            "generator_index": 26,
            "generator_name": "Frond 6",
            "generator_type": "Frond",
            "slot_prefix": "Material:Frond:0",
            "source_material_id": 4,
            "source_mesh_id": 2,
            "target_material_id": 8,
            "target_mesh_id": 12,
        }

        identity = speedtree.resolve_generator_binding(
            root,
            legacy,
            context="Previous Atlas Generator binding",
        )

        self.assertEqual(identity["generator_index"], 0)
        self.assertEqual(identity["resolution"], "legacy_type_slot_values")

    def test_duplicate_legacy_slot_values_use_matching_recorded_index_once(self):
        root = ET.Element("SpeedTreeModel")
        add_variant_generator(
            root,
            "Frond",
            "Frond 6",
            [(8, 12)],
        )
        add_variant_generator(
            root,
            "Frond",
            "Frond 6",
            [(8, 12)],
        )
        legacy = {
            "generator_index": 1,
            "generator_name": "Frond 6",
            "generator_type": "Frond",
            "slot_prefix": "Material:Frond:0",
            "source_material_id": 4,
            "source_mesh_id": 2,
            "target_material_id": 8,
            "target_mesh_id": 12,
        }

        identity = speedtree.resolve_generator_binding(
            root,
            legacy,
            context="Previous Atlas Generator binding",
        )

        self.assertEqual(identity["generator_index"], 1)
        self.assertEqual(
            identity["resolution"],
            "legacy_index_type_slot_values",
        )

    def test_variant_coverage_repairs_declared_missing_tail_from_slot_zero_schema(self):
        target = Path(self.temp_dir.name) / "malformed.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        add_material(assets, 8, "M_branch_generated", [10, 11, 12])
        for mesh_id in (1, 2, 3, 10, 11, 12):
            add_mesh(assets, mesh_id)
        add_variant_generator(
            root,
            "Frond",
            "Frond 36",
            [(4, 1)],
            child_count=3,
        )
        write_spm(target, root)
        groups = [
            {
                "material": "M_branch_generated",
                "material_id": 8,
                "mesh_ids": [10, 11, 12],
                "meshes": [
                    {
                        "source_object": f"branch_elm_01_{ordinal:02d}",
                        "source_ordinal": ordinal,
                    }
                    for ordinal in range(1, 4)
                ],
            }
        ]
        result = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups,
            [4],
            generator_variant_policy="ensure_all_material_cutouts",
        )

        self.assertTrue(result["complete"])
        self.assertEqual(result["created_slot_pairs"], 2)
        self.assertEqual(
            generator_values(target),
            {
                ("Frond 36", "Material:Frond:0"): (8, 10),
                ("Frond 36", "Material:Frond:1"): (8, 11),
                ("Frond 36", "Material:Frond:2"): (8, 12),
            },
        )
        declared_repairs = [
            row
            for row in result["repaired_variant_slot_schemas"]
            if row.get("mode") == "normalized_declared_missing_tail"
        ]
        self.assertEqual(len(declared_repairs), 1)
        self.assertEqual(
            (
                declared_repairs[0]["declared_children_before"],
                declared_repairs[0]["declared_children_after"],
            ),
            (3, 1),
        )
        second = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_branch"],
            groups,
            [4],
            previous_bindings=result["bindings"],
            source_mesh_ids_by_name={"M_branch": [1, 2, 3]},
            generator_variant_policy="ensure_all_material_cutouts",
        )
        self.assertEqual(second["created_slot_pairs"], 0)
        self.assertEqual(second["changed_slot_pairs"], 0)
        self.assertEqual(second["already_connected_slot_pairs"], 3)
        self.assertEqual(
            generator_values(target),
            {
                ("Frond 36", "Material:Frond:0"): (8, 10),
                ("Frond 36", "Material:Frond:1"): (8, 11),
                ("Frond 36", "Material:Frond:2"): (8, 12),
            },
        )

    def test_variant_coverage_interior_child_gap_still_fails_without_write(self):
        target = Path(self.temp_dir.name) / "interior_gap.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_branch", [1, 2, 3])
        add_material(assets, 8, "M_branch_generated", [10, 11, 12])
        for mesh_id in (1, 2, 3, 10, 11, 12):
            add_mesh(assets, mesh_id)
        generator = add_variant_generator(
            root,
            "Frond",
            "Frond 36",
            [(4, 1), (4, 2)],
            child_count=3,
        )
        for prop in generator.findall("./Properties/Property"):
            name = str(prop.findtext("Name") or "")
            if name.startswith("Material:Frond:1:"):
                prop.find("Name").text = name.replace(
                    "Material:Frond:1:",
                    "Material:Frond:2:",
                )
        write_spm(target, root)
        groups = [
            {
                "material": "M_branch_generated",
                "material_id": 8,
                "mesh_ids": [10, 11, 12],
                "meshes": [
                    {
                        "source_object": f"branch_elm_01_{ordinal:02d}",
                        "source_ordinal": ordinal,
                    }
                    for ordinal in range(1, 4)
                ],
            }
        ]
        before = target.read_bytes()

        with self.assertRaisesRegex(RuntimeError, "child indices"):
            speedtree.connect_atlas_generators_in_spm(
                target,
                ["M_branch"],
                groups,
                [4],
                generator_variant_policy="ensure_all_material_cutouts",
            )

        self.assertEqual(target.read_bytes(), before)

    def test_variant_coverage_preserves_side_duplicate_weight_slots(self):
        target = Path(self.temp_dir.name) / "side_duplicates.spm"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_leaf_side", [1, 2, 3])
        add_material(assets, 8, "M_leaf_side_generated", [10, 11, 12])
        for mesh_id in (1, 2, 3, 10, 11, 12):
            add_mesh(assets, mesh_id)
        weighted = add_variant_generator(
            root,
            "LeafMesh",
            "Leaf 63",
            [(4, 2), (4, 3), (4, 3)],
        )
        weight = ET.SubElement(weighted.find("Properties"), "Property")
        ET.SubElement(weight, "Name").text = "Leaves:Type:1:Weight"
        ET.SubElement(weight, "Value").text = "2"
        add_variant_generator(
            root, "Leaf Mesh", "Leaf 65", [(4, 1)]
        )
        write_spm(target, root)
        groups = [
            {
                "material": "M_leaf_side_generated",
                "material_id": 8,
                "mesh_ids": [10, 11, 12],
                "meshes": [
                    {
                        "source_object": f"leaf_elm_side_01_{ordinal:02d}",
                        "source_ordinal": ordinal,
                    }
                    for ordinal in range(1, 4)
                ],
            }
        ]

        result = speedtree.connect_atlas_generators_in_spm(
            target,
            ["M_leaf_side"],
            groups,
            [4],
            generator_variant_policy="ensure_all_material_cutouts",
        )

        self.assertEqual(result["created_slot_pairs"], 0)
        self.assertEqual(len(result["bindings"]), 4)
        self.assertEqual(
            generator_values(target),
            {
                ("Leaf 63", "Leaves:Type:0"): (8, 11),
                ("Leaf 63", "Leaves:Type:1"): (8, 12),
                ("Leaf 63", "Leaves:Type:2"): (8, 12),
                ("Leaf 65", "Leaves:Type:0"): (8, 10),
            },
        )
        parsed = speedtree.read_spm_xml(target)
        generators = list(parsed.iter("Generator"))
        self.assertEqual(
            speedtree.generator_variant_parent_state(
                generators[0], "Leaves:Type"
            )["child_count"],
            3,
        )
        self.assertEqual(
            speedtree.generator_variant_parent_state(
                generators[1], "Leaves:Type"
            )["child_count"],
            1,
        )
        weight_values = [
            prop.findtext("Value")
            for prop in generators[0].find("Properties").findall("Property")
            if prop.findtext("Name") == "Leaves:Type:1:Weight"
        ]
        self.assertEqual(weight_values, ["2"])


class SafetyTests(unittest.TestCase):
    def _write_upsert_fixture(self, folder, existing_scope):
        folder = Path(folder)
        target = folder / "target.spm"
        material_name = "M_cluster_lauraceae_atlas_01"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        marker = ""
        if existing_scope is not None:
            marker = json.dumps(
                {
                    "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                    "scope": existing_scope,
                    "kind": "material",
                }
            )
        add_material(assets, 8, material_name, [18], marker)
        add_mesh(assets, 18, marker.replace('"material"', '"mesh"'))
        write_spm(target, root)

        sample = folder / "external_mesh_sample.spm"
        sample_root = ET.Element("SpeedTreeModel")
        sample_assets = ET.SubElement(sample_root, "Assets")
        sample_mesh = add_mesh(sample_assets, 1)
        ET.SubElement(sample_mesh, "Embedded").text = "false"
        write_spm(sample, sample_root)

        manifest = {
            "export_scope_id": "new-uuid-scope",
            "source_collection": material_name,
            "material_collection": material_name,
            "atlas_asset_name": material_name,
            "textures": canonical_test_textures(
                folder / "T_leaf_test_color.tga"
            ),
            "meshes": [{"asset": str(folder / "meshes" / "18.fbx")}],
        }
        return target, sample, manifest, material_name

    def test_target_orchestration_passes_delivery_intent_to_producer(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree_01.spm"
            target.write_bytes(b"fixture")
            manifest_path = folder / "target-manifest.json"
            intent = {
                "kind": "speedtree_generator_delivery_scope_intent",
                "intent_sha256": "a" * 64,
            }
            props = types.SimpleNamespace(
                speedtree_atlas_asset_name="M_branch_elm_01",
                speedtree_create_missing_spm=False,
                speedtree_source_materials_json=json.dumps({
                    str(target): {
                        "source_material_names": ["M_branch_elm_01"],
                        "source_material_ids": [8],
                        "adopt_source_material": True,
                        "generator_variant_policy": (
                            "ensure_all_material_cutouts"
                        ),
                        "generator_delivery_scope_intent": intent,
                    }
                }),
            )
            captured = {}
            original_targets = speedtree.speedtree_spm_targets
            original_export = speedtree._export_or_update_speedtree_spm_path_impl
            original_execute = speedtree.execute_atomic_target_update

            def fake_export(_props, target_spm, **kwargs):
                captured.update(kwargs)
                manifest_path.write_text(
                    json.dumps({"generator_connection": {}}),
                    encoding="utf-8",
                )
                return (
                    Path(target_spm),
                    manifest_path,
                    [],
                    "updated",
                    8,
                    [10],
                    [],
                    {
                        "removed_materials": [],
                        "removed_mesh_ids": [],
                        "removed_mesh_files": [],
                    },
                )

            def fake_execute(targets, build_target, _validate, **_kwargs):
                return [build_target(Path(item), Path(item)) for item in targets]

            speedtree.speedtree_spm_targets = lambda _props: [target]
            speedtree._export_or_update_speedtree_spm_path_impl = fake_export
            speedtree.execute_atomic_target_update = fake_execute
            try:
                results = speedtree.export_or_update_speedtree_spm_targets(
                    props
                )
            finally:
                speedtree.speedtree_spm_targets = original_targets
                speedtree._export_or_update_speedtree_spm_path_impl = original_export
                speedtree.execute_atomic_target_update = original_execute

            self.assertEqual(
                captured["generator_delivery_scope_intent"],
                intent,
            )
            self.assertEqual(len(results), 1)

    def test_cluster_relation_expansion_adopts_each_targets_local_material_id(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            first = folder / "SK_tree_01.spm"
            second = folder / "SK_tree_02.spm"
            for target, material_id, mesh_id in (
                (first, 8, 1),
                (second, 10, 12),
            ):
                root = ET.Element("SpeedTreeModel")
                assets = ET.SubElement(root, "Assets")
                add_material(
                    assets,
                    material_id,
                    "M_branch_elm_01",
                    [mesh_id],
                )
                add_mesh(assets, mesh_id)
                write_spm(target, root)
            props = types.SimpleNamespace(
                speedtree_atlas_asset_name="M_branch_elm_01",
                speedtree_source_materials_json=json.dumps({
                    str(first): {
                        "source_material_names": ["M_branch_elm_01"],
                        "source_material_ids": [8],
                        "adopt_source_material": True,
                        "generator_variant_policy": (
                            "ensure_all_material_cutouts"
                        ),
                    },
                }),
            )

            report = speedtree.extend_source_material_adoptions_for_targets(
                props,
                [first, second],
            )
            mapping = speedtree.speedtree_source_material_mapping(props)

            self.assertEqual(len(report["added"]), 1)
            self.assertEqual(report["added"][0]["material_id"], 10)
            self.assertEqual(
                mapping[speedtree.normalized_target_key(second)][
                    "source_material_ids"
                ],
                [10],
            )
            self.assertTrue(
                mapping[speedtree.normalized_target_key(second)][
                    "adopt_source_material"
                ]
            )
            self.assertEqual(
                mapping[speedtree.normalized_target_key(second)][
                    "generator_variant_policy"
                ],
                "ensure_all_material_cutouts",
            )

    def test_cluster_relation_duplicate_names_follow_explicit_id_without_blocking(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "SK_tree_01.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 4, "M_leaf_tree_01", [2, 3])
            add_material(assets, 14, "M_leaf_tree_01", [110, 111, 112])
            for mesh_id in (2, 3, 110, 111, 112):
                add_mesh(assets, mesh_id)
            write_spm(target, root)
            props = types.SimpleNamespace(
                speedtree_atlas_asset_name="M_leaf_tree_01",
                speedtree_source_materials_json=json.dumps({
                    str(target): {
                        "source_material_names": ["M_leaf_tree_01"],
                        "source_material_ids": [4],
                        "adopt_source_material": True,
                        "generator_variant_policy": (
                            "ensure_all_material_cutouts"
                        ),
                    },
                }),
            )

            report = speedtree.extend_source_material_adoptions_for_targets(
                props,
                [target],
            )
            mapping = speedtree.speedtree_source_material_mapping(props)

            self.assertEqual(report["preserved"], [str(target)])
            self.assertEqual(
                mapping[speedtree.normalized_target_key(target)][
                    "source_material_ids"
                ],
                [4],
            )
            _names, records = speedtree.source_materials_by_name(
                assets,
                ["M_leaf_tree_01"],
                [4],
            )
            self.assertEqual(list(records), [4])
            adoption = speedtree.prepare_source_material_adoption(
                target,
                {"export_scope_id": "test-scope"},
                "M_leaf_tree_01",
                4,
            )
            self.assertEqual(adoption["material_id"], 4)
            self.assertEqual(adoption["original_mesh_ids"], [2, 3])

    def test_cluster_relation_reconciles_stale_existing_local_material_id(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree_01.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 19, "M_branch_elm_01", [23])
            add_mesh(assets, 23)
            write_spm(target, root)
            props = types.SimpleNamespace(
                speedtree_atlas_asset_name="M_branch_elm_01",
                speedtree_source_materials_json=json.dumps({
                    str(target): {
                        "source_material_names": ["M_branch_elm_01"],
                        # Stale sibling/local ID from an older target mapping.
                        "source_material_ids": [7],
                        "adopt_source_material": True,
                        "generator_variant_policy": (
                            "ensure_all_material_cutouts"
                        ),
                        "generator_delivery_scope_intent": {
                            "kind": "speedtree_generator_delivery_scope_intent",
                            "intent_sha256": "a" * 64,
                        },
                    },
                }),
            )

            report = speedtree.extend_source_material_adoptions_for_targets(
                props,
                [target],
            )
            mapping = speedtree.speedtree_source_material_mapping(props)

            self.assertEqual(report["added"], [])
            self.assertEqual(len(report["reconciled"]), 1)
            self.assertEqual(report["reconciled"][0]["material_id"], 19)
            self.assertEqual(
                mapping[speedtree.normalized_target_key(target)][
                    "source_material_ids"
                ],
                [19],
            )
            self.assertEqual(
                mapping[speedtree.normalized_target_key(target)][
                    "generator_delivery_scope_intent"
                ]["intent_sha256"],
                "a" * 64,
            )

    def test_cluster_relation_expansion_does_not_claim_another_atlas_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            first = folder / "SK_tree_01.spm"
            second = folder / "SK_tree_02.spm"
            marker = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": "another-scope",
                "kind": "material",
            })
            for target, user_data in ((first, ""), (second, marker)):
                root = ET.Element("SpeedTreeModel")
                assets = ET.SubElement(root, "Assets")
                add_material(
                    assets,
                    8,
                    "M_branch_elm_01",
                    [1],
                    user_data,
                )
                add_mesh(assets, 1)
                write_spm(target, root)
            props = types.SimpleNamespace(
                speedtree_atlas_asset_name="M_branch_elm_01",
                speedtree_source_materials_json=json.dumps({
                    str(first): {
                        "source_material_names": ["M_branch_elm_01"],
                        "source_material_ids": [8],
                        "adopt_source_material": True,
                        "generator_variant_policy": (
                            "ensure_all_material_cutouts"
                        ),
                    },
                }),
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "already managed by Atlas scope",
            ):
                speedtree.extend_source_material_adoptions_for_targets(
                    props,
                    [first, second],
                )

    def test_cluster_relation_expansion_reuses_same_blend_adoption_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            blend = folder / "Cluster" / "SK_branch_elm_01.blend"
            blend.parent.mkdir()
            blend.touch()
            first = folder / "SK_tree_01.spm"
            second = folder / "SK_tree_02.spm"
            marker = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": "same-scope",
                "kind": "material",
            })
            for target, user_data in ((first, ""), (second, marker)):
                root = ET.Element("SpeedTreeModel")
                assets = ET.SubElement(root, "Assets")
                add_material(
                    assets,
                    8,
                    "M_branch_elm_01",
                    [1],
                    user_data,
                )
                add_mesh(assets, 1)
                write_spm(target, root)
            scope = folder / ".atlas_leaf_speedtree_scopes"
            scope.mkdir()
            (scope / "same-scope__SK_tree_02.json").write_text(
                json.dumps({
                    "blend_file": str(blend),
                    "spm": str(second),
                    "export_scope_id": "same-scope",
                    "source_material_adoption": {
                        "version": speedtree.SOURCE_MATERIAL_ADOPTION_VERSION,
                        "material_name": "M_branch_elm_01",
                        "material_id": 8,
                        "original_material_snapshot": "material-snapshot",
                        "original_mesh_snapshots": [
                            {"mesh_id": 1, "snapshot": "mesh-snapshot"},
                        ],
                    },
                }),
                encoding="utf-8",
            )
            props = types.SimpleNamespace(
                speedtree_atlas_asset_name="M_branch_elm_01",
                speedtree_source_materials_json=json.dumps({
                    str(first): {
                        "source_material_names": ["M_branch_elm_01"],
                        "source_material_ids": [8],
                        "adopt_source_material": True,
                        "generator_variant_policy": (
                            "ensure_all_material_cutouts"
                        ),
                    },
                }),
            )

            report = speedtree.extend_source_material_adoptions_for_targets(
                props,
                [first, second],
                blend_path=blend,
            )

            self.assertTrue(report["added"][0]["reused_existing_scope"])

    def test_cluster_relation_reuses_adoption_final_mesh_registry(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            blend = folder / "Cluster" / "SK_cluster_01.blend"
            blend.parent.mkdir()
            blend.touch()
            target = folder / "SK_tree_01.spm"
            material_name = "M_cluster_01"
            scope_name = "same-scope"
            marker = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": scope_name,
                "kind": "material",
            })
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(
                assets,
                8,
                material_name,
                [81, 82, 29],
                marker,
            )
            for mesh_id in (81, 82, 29):
                add_mesh(
                    assets,
                    mesh_id,
                    marker.replace('"material"', '"mesh"'),
                )
            write_spm(target, root)
            scope_dir = folder / ".atlas_leaf_speedtree_scopes"
            scope_dir.mkdir()
            (
                scope_dir / f"{scope_name}__SK_tree_01.json"
            ).write_text(
                json.dumps({
                    "blend_file": str(blend),
                    "spm": str(target),
                    "export_scope_id": scope_name,
                    "source_material_adoption": {
                        "version": (
                            speedtree.SOURCE_MATERIAL_ADOPTION_VERSION
                        ),
                        "material_name": material_name,
                        "material_id": 8,
                        "original_material_snapshot": "material-snapshot",
                        "original_mesh_snapshots": [
                            {"mesh_id": 12, "snapshot": "old-generated"},
                            {"mesh_id": 29, "snapshot": "preserved-source"},
                        ],
                        "generated_mesh_ids": [81, 82],
                        "preserved_original_mesh_ids": [29],
                        "final_material_mesh_ids": [81, 82, 29],
                    },
                }),
                encoding="utf-8",
            )
            props = types.SimpleNamespace(
                speedtree_atlas_asset_name=material_name,
                speedtree_source_materials_json=json.dumps({
                    str(target): {
                        "source_material_names": [material_name],
                        # Deliberately stale local mapping; the live material
                        # and exact same-scope final registry are authoritative.
                        "source_material_ids": [7],
                        "adopt_source_material": True,
                        "generator_variant_policy": (
                            "ensure_all_material_cutouts"
                        ),
                    },
                }),
            )

            report = speedtree.extend_source_material_adoptions_for_targets(
                props,
                [target],
                blend_path=blend,
            )

            self.assertTrue(
                report["reconciled"][0]["reused_existing_scope"]
            )
            mapping = speedtree.speedtree_source_material_mapping(props)
            self.assertEqual(
                mapping[speedtree.normalized_target_key(target)][
                    "source_material_ids"
                ],
                [8],
            )

    def test_cluster_relation_reuses_same_blend_legacy_group_with_untagged_mesh(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            blend = folder / "Cluster" / "SK_branch_elm_01.blend"
            blend.parent.mkdir()
            blend.touch()
            first = folder / "SK_tree_01.spm"
            second = folder / "SK_tree_02.spm"
            marker = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": "same-scope",
                "kind": "material",
            })
            for target, user_data in ((first, ""), (second, marker)):
                root = ET.Element("SpeedTreeModel")
                assets = ET.SubElement(root, "Assets")
                add_material(
                    assets,
                    8,
                    "M_branch_elm_01",
                    [1],
                    user_data,
                )
                # Legacy Atlas writers tagged the material but not its Mesh.
                add_mesh(assets, 1)
                write_spm(target, root)
            scope = folder / ".atlas_leaf_speedtree_scopes"
            scope.mkdir()
            (scope / "same-scope__SK_tree_02.json").write_text(
                json.dumps({
                    "blend_file": str(blend),
                    "spm": str(second),
                    "export_scope_id": "same-scope",
                    "source_collection": "Atlas_Branch_Plans",
                    "speedtree_material_groups": [{
                        "material": "M_branch_elm_01",
                        "material_id": 8,
                        "mesh_ids": [1],
                    }],
                }),
                encoding="utf-8",
            )
            props = types.SimpleNamespace(
                speedtree_atlas_asset_name="M_branch_elm_01",
                speedtree_source_materials_json=json.dumps({
                    str(first): {
                        "source_material_names": ["M_branch_elm_01"],
                        "source_material_ids": [8],
                        "adopt_source_material": True,
                        "generator_variant_policy": (
                            "ensure_all_material_cutouts"
                        ),
                    },
                }),
            )

            report = speedtree.extend_source_material_adoptions_for_targets(
                props,
                [first, second],
                blend_path=blend,
            )

            self.assertTrue(report["added"][0]["reused_existing_scope"])
            conflicting_root = speedtree.read_spm_xml(second)
            conflicting_mesh = conflicting_root.find("./Assets/Mesh[@ID='1']")
            conflicting_mesh.find("UserData").text = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": "different-scope",
                "kind": "mesh",
            })
            write_spm(second, conflicting_root)
            with self.assertRaisesRegex(
                RuntimeError,
                "already managed by Atlas scope",
            ):
                speedtree.extend_source_material_adoptions_for_targets(
                    props,
                    [second],
                    blend_path=blend,
                )

    def test_random_scope_owner_manifest_is_reclaimed_by_canonical_source_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree_01.spm"
            blend = folder / "Cluster" / "SK_branch_elm_01.blend"
            blend.parent.mkdir()
            blend.touch()
            material_name = "M_branch_elm_01"
            marker = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": "old-random-scope",
                "kind": "material",
            })
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            material = add_material(
                assets,
                19,
                material_name,
                [23],
                marker,
            )
            add_mesh(
                assets,
                23,
                marker.replace('"material"', '"mesh"'),
            )
            write_spm(target, root)
            scope_dir = folder / ".atlas_leaf_speedtree_scopes"
            scope_dir.mkdir()
            previous = {
                "export_scope_id": "old-random-scope",
                "blend_file": str(blend),
                "source_collection": "Atlas_Cluster_Cards",
                "spm": str(target),
                "speedtree_material_groups": [
                    {
                        "material": material_name,
                        "material_id": 19,
                        "mesh_ids": [23],
                    }
                ],
            }
            (scope_dir / "old-random-scope__SK_tree_01.json").write_text(
                json.dumps(previous),
                encoding="utf-8",
            )
            current = {
                "export_scope_id": "new-random-scope",
                "blend_file": str(blend),
                "source_collection": "Atlas_Cluster_Cards",
                "material_groups": [{"material": material_name}],
            }

            owner = speedtree.material_owner_manifest_for_source(
                target,
                material,
                current,
                material_name,
            )

            self.assertEqual(
                owner.get("export_scope_id"),
                "old-random-scope",
            )
            other_source = {
                **current,
                "blend_file": str(folder / "Cluster" / "other.blend"),
            }
            self.assertEqual(
                speedtree.material_owner_manifest_for_source(
                    target,
                    material,
                    other_source,
                    material_name,
                ),
                {},
            )

    def test_legacy_untagged_managed_meshes_require_exact_manifest_ordinals(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree_01.spm"
            blend = folder / "Cluster" / "SK_branch_01.blend"
            blend.parent.mkdir()
            blend.touch()
            mesh_dir = folder / "meshes"
            mesh_dir.mkdir()
            mesh_paths = [
                mesh_dir / "m_branch__01_branch_01.fbx",
                mesh_dir / "m_branch__02_branch_02.fbx",
            ]
            for path in mesh_paths:
                path.touch()
            marker = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": "legacy-scope",
                "kind": "material",
                "group": "Plans",
            })
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            material = add_material(
                assets,
                1,
                "M_branch",
                [106, 107],
                marker,
            )
            for mesh_id, path in zip((106, 107), mesh_paths):
                mesh = add_mesh(assets, mesh_id)
                mesh.find("Filename").text = str(
                    path.relative_to(folder)
                ).replace("\\", "/")
            write_spm(target, root)
            previous = {
                "export_scope_id": "legacy-scope",
                "blend_file": str(blend),
                "source_collection": "Plans",
                "spm": str(target),
                "material_groups": [{
                    "collection": "Plans",
                    "material": "M_branch",
                    "meshes": [
                        {
                            "asset": str(path),
                            "source_ordinal": ordinal,
                        }
                        for ordinal, path in enumerate(mesh_paths, 1)
                    ],
                }],
                "speedtree_material_groups": [{
                    "collection": "Plans",
                    "material": "M_branch",
                    "material_id": 1,
                    "mesh_ids": [106, 107],
                }],
            }
            scope_dir = folder / ".atlas_leaf_speedtree_scopes"
            scope_dir.mkdir()
            (
                scope_dir / "legacy-scope__SK_tree_01.json"
            ).write_text(json.dumps(previous), encoding="utf-8")
            current = {
                "export_scope_id": "new-scope",
                "blend_file": str(blend),
                "source_collection": "Plans",
            }

            owner = speedtree.material_owner_manifest_for_source(
                target,
                material,
                current,
                "M_branch",
            )

            self.assertEqual(owner.get("export_scope_id"), "legacy-scope")

            changed = speedtree.read_spm_xml(target)
            changed.find("./Assets/Mesh[@ID='107']/Filename").text = (
                "meshes/unrelated.fbx"
            )
            write_spm(target, changed)
            changed_material = speedtree.read_spm_xml(target).find(
                "./Assets/Material_v8[@ID='1']"
            )
            self.assertEqual(
                speedtree.material_owner_manifest_for_source(
                    target,
                    changed_material,
                    current,
                    "M_branch",
                ),
                {},
            )

    def test_same_source_managed_slot_without_old_adoption_snapshot_is_replaceable(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree_01.spm"
            blend = folder / "Cluster" / "SK_branch_elm_01.blend"
            blend.parent.mkdir()
            blend.touch()
            marker = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": "old-random-scope",
                "kind": "material",
            })
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(
                assets,
                19,
                "M_branch_elm_01",
                [23],
                marker,
            )
            add_mesh(
                assets,
                23,
                marker.replace('"material"', '"mesh"'),
            )
            write_spm(target, root)
            current = {
                "export_scope_id": "new-random-scope",
                "blend_file": str(blend),
                "source_collection": "Atlas_Cluster_Cards",
            }
            previous = {
                "export_scope_id": "old-random-scope",
                "blend_file": str(blend),
                "source_collection": "Atlas_Cluster_Cards",
            }

            adoption = speedtree.prepare_source_material_adoption(
                target,
                current,
                "M_branch_elm_01",
                19,
                previous,
            )

            self.assertEqual(
                adoption["baseline_kind"],
                "same_source_managed_takeover",
            )
            self.assertEqual(adoption["scope"], "new-random-scope")
            with self.assertRaisesRegex(
                RuntimeError,
                "already managed by Atlas scope",
            ):
                speedtree.prepare_source_material_adoption(
                    target,
                    {
                        **current,
                        "blend_file": str(
                            folder / "Cluster" / "different_source.blend"
                        ),
                    },
                    "M_branch_elm_01",
                    19,
                    previous,
                )

    def test_untagged_legacy_mesh_paths_are_migrated_to_uuid(self):
        with tempfile.TemporaryDirectory() as folder:
            target, sample, manifest, material_name = self._write_upsert_fixture(folder, None)
            original_material_sample = speedtree.SPEEDTREE_101_MATERIAL_SAMPLE
            original_mesh_sample = speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE
            speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = sample
            speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = sample
            try:
                _, action, material_id, mesh_ids = speedtree.upsert_speedtree_assets_in_spm(
                    target, manifest, material_name
                )
            finally:
                speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = original_material_sample
                speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = original_mesh_sample

            parsed_assets = speedtree.read_spm_xml(target).find("Assets")
            material = speedtree.find_material_by_name(parsed_assets, material_name)
            mesh = parsed_assets.find("Mesh[@ID='18']")
            self.assertEqual(action, "updated")
            self.assertEqual(material_id, 8)
            self.assertEqual(mesh_ids, [18])
            self.assertEqual(
                speedtree.parse_atlas_leaf_spm_user_data(material.findtext("UserData"))["scope"],
                "new-uuid-scope",
            )
            self.assertEqual(
                speedtree.parse_atlas_leaf_spm_user_data(mesh.findtext("UserData"))["scope"],
                "new-uuid-scope",
            )

    def test_upsert_removes_only_unreferenced_missing_external_meshes(self):
        with tempfile.TemporaryDirectory() as folder:
            target, sample, manifest, material_name = (
                self._write_upsert_fixture(folder, None)
            )
            root = speedtree.read_spm_xml(target)
            assets = root.find("Assets")
            orphan = add_mesh(assets, 77)
            ET.SubElement(orphan, "Filename").text = (
                "meshes/old_atlas_plate_missing.fbx"
            )
            ET.SubElement(orphan, "Embedded").text = "false"
            write_spm(target, root)

            original_material_sample = speedtree.SPEEDTREE_101_MATERIAL_SAMPLE
            original_mesh_sample = (
                speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE
            )
            speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = sample
            speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = sample
            try:
                speedtree.upsert_speedtree_assets_in_spm(
                    target,
                    manifest,
                    material_name,
                )
            finally:
                speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = (
                    original_material_sample
                )
                speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = (
                    original_mesh_sample
                )

            parsed_assets = speedtree.read_spm_xml(target).find("Assets")
            self.assertIsNone(parsed_assets.find("Mesh[@ID='77']"))
            self.assertIsNotNone(parsed_assets.find("Mesh[@ID='18']"))

    def test_cleanup_prunes_unbound_managed_mesh_but_preserves_source(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            spm = folder / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            marker = json.dumps(
                {
                    "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                    "scope": "managed-scope",
                    "kind": "material",
                }
            )
            managed = add_material(
                assets,
                8,
                "M_leaf_atlas_01",
                [77, 78],
                marker,
            )
            add_material(assets, 9, "M_leaf_source_01", [79])
            for mesh_id, filename in (
                (77, "meshes/missing_managed.fbx"),
                (78, "meshes/present_managed.fbx"),
                (79, "meshes/missing_source.fbx"),
            ):
                mesh = add_mesh(assets, mesh_id)
                ET.SubElement(mesh, "Filename").text = filename
                ET.SubElement(mesh, "Embedded").text = "false"
            present = folder / "meshes" / "present_managed.fbx"
            present.parent.mkdir()
            present.write_bytes(b"fbx")

            removed = (
                speedtree.remove_unreferenced_missing_external_mesh_nodes(
                    root,
                    spm,
                    candidate_mesh_ids={77, 79},
                )
            )

            self.assertEqual(
                [item["mesh_id"] for item in removed],
                [77],
            )
            self.assertEqual(
                speedtree.spm_material_mesh_ids(managed),
                [78],
            )
            self.assertIsNone(assets.find("Mesh[@ID='77']"))
            self.assertIsNotNone(assets.find("Mesh[@ID='79']"))

    def test_source_material_adoption_is_idempotent_and_reversible(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            source = add_material(assets, 8, "M_branch", [1, 2, 9])
            ET.SubElement(source, "AuthoredValue").text = "preserve-me"
            ET.SubElement(source, "Width").text = "2048"
            ET.SubElement(source, "Height").text = "2048"
            color_map = ET.SubElement(source, "Map", {"Name": "Color"})
            ET.SubElement(color_map, "TexFilename").text = "old_branch.tga"
            ET.SubElement(color_map, "TexEnabled").text = "true"
            ET.SubElement(color_map, "TexSizeX").text = "2048"
            ET.SubElement(color_map, "TexSizeY").text = "2048"
            for mesh_id in (1, 2, 9):
                mesh = add_mesh(assets, mesh_id)
                ET.SubElement(mesh, "Embedded").text = "true"
            add_variant_generator(root, "Frond", "Frond 36", [(8, 1)])
            write_spm(target, root)
            color = folder / "branch.tga"
            tga_header = bytearray(18)
            tga_header[2] = 2
            tga_header[12:14] = (1024).to_bytes(2, "little")
            tga_header[14:16] = (1024).to_bytes(2, "little")
            tga_header[16] = 24
            color.write_bytes(tga_header)

            sample = folder / "external_mesh_sample.spm"
            sample_root = ET.Element("SpeedTreeModel")
            sample_assets = ET.SubElement(sample_root, "Assets")
            sample_mesh = add_mesh(sample_assets, 100)
            ET.SubElement(sample_mesh, "Embedded").text = "false"
            write_spm(sample, sample_root)

            mesh_items = [
                {
                    "asset": str(folder / "meshes" / f"branch_{index:02d}.fbx"),
                    "source_object": f"branch_elm_01_{index:02d}",
                    "source_ordinal": index,
                }
                for index in range(1, 4)
            ]
            manifest = {
                "export_scope_id": "scope-adopt",
                "source_collection": "Plans",
                "material_collection": "Plans",
                "blend_file": str(folder / "normalized.blend"),
                "textures": canonical_test_textures(color),
                "meshes": mesh_items,
            }
            original_material_sample = speedtree.SPEEDTREE_101_MATERIAL_SAMPLE
            original_mesh_sample = speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE
            speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = sample
            speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = sample
            try:
                adoption = speedtree.prepare_source_material_adoption(
                    target, manifest, "M_branch", 8
                )
                _, first_action, material_id, first_mesh_ids = (
                    speedtree.upsert_speedtree_assets_in_spm(
                        target,
                        manifest,
                        "M_branch",
                        adopt_source_material_id=8,
                    )
                )
                groups = [
                    {
                        "material": "M_branch",
                        "material_id": material_id,
                        "mesh_ids": first_mesh_ids,
                        "meshes": mesh_items,
                    }
                ]
                first_connection = speedtree.connect_atlas_generators_in_spm(
                    target,
                    ["M_branch"],
                    groups,
                    [8],
                    source_mesh_ids_by_name={"M_branch": [1, 2, 9]},
                    generator_variant_policy="ensure_all_material_cutouts",
                )
                adoption = speedtree.finalize_source_material_adoption(
                    target, adoption, first_mesh_ids, first_connection
                )

                first_assets = speedtree.read_spm_xml(target).find("Assets")
                adopted_material = first_assets.find("Material_v8[@ID='8']")
                self.assertEqual(first_action, "adopted")
                self.assertEqual(first_mesh_ids, [10, 11, 12])
                self.assertEqual(
                    speedtree.spm_material_mesh_ids(adopted_material), [10, 11, 12]
                )
                self.assertEqual(adopted_material.findtext("AuthoredValue"), "preserve-me")
                self.assertEqual(adopted_material.findtext("Width"), "1024")
                self.assertEqual(adopted_material.findtext("Height"), "1024")
                adopted_color_map = adopted_material.find("Map[@Name='Color']")
                self.assertEqual(adopted_color_map.findtext("TexSizeX"), "1024")
                self.assertEqual(adopted_color_map.findtext("TexSizeY"), "1024")
                self.assertTrue(
                    all(first_assets.find(f"Mesh[@ID='{mesh_id}']") is None for mesh_id in (1, 2, 9))
                )
                self.assertEqual(
                    generator_values(target),
                    {
                        ("Frond 36", "Material:Frond:0"): (8, 10),
                        ("Frond 36", "Material:Frond:1"): (8, 11),
                        ("Frond 36", "Material:Frond:2"): (8, 12),
                    },
                )
                self.assertEqual(
                    first_connection["generator_variant_policy"],
                    "ensure_all_material_cutouts",
                )
                self.assertEqual(first_connection["created_slot_pairs"], 2)
                self.assertEqual(
                    [
                        item["slot_prefix"]
                        for item in first_connection["bindings"]
                        if item["created_slot"]
                    ],
                    ["Material:Frond:1", "Material:Frond:2"],
                )

                previous_manifest = {
                    **manifest,
                    "source_material_adoption": adoption,
                    "generator_connection": first_connection,
                    "speedtree_material_groups": groups,
                    "material_groups": groups,
                    "mesh_ids": first_mesh_ids,
                }
                second_adoption = speedtree.prepare_source_material_adoption(
                    target,
                    manifest,
                    "M_branch",
                    8,
                    previous_manifest,
                )
                _, second_action, _, second_mesh_ids = speedtree.upsert_speedtree_assets_in_spm(
                    target,
                    manifest,
                    "M_branch",
                    adopt_source_material_id=8,
                )
                groups[0]["mesh_ids"] = second_mesh_ids
                second_connection = speedtree.connect_atlas_generators_in_spm(
                    target,
                    ["M_branch"],
                    groups,
                    [8],
                    previous_bindings=first_connection["bindings"],
                    source_mesh_ids_by_name={"M_branch": [1, 2, 9]},
                    generator_variant_policy="ensure_all_material_cutouts",
                )
                second_adoption = speedtree.finalize_source_material_adoption(
                    target, second_adoption, second_mesh_ids, second_connection
                )
                self.assertEqual(second_action, "updated")
                self.assertEqual(second_mesh_ids, first_mesh_ids)
                self.assertEqual(second_connection["changed_slot_pairs"], 0)
                self.assertEqual(second_connection["created_slot_pairs"], 0)
                self.assertEqual(
                    second_connection["bindings"][0]["source_mesh_id"], 1
                )
                self.assertEqual(
                    [
                        item["slot_prefix"]
                        for item in second_connection["bindings"]
                        if item["created_slot"]
                    ],
                    ["Material:Frond:1", "Material:Frond:2"],
                )

                removal_manifest = {
                    **previous_manifest,
                    "source_material_adoption": second_adoption,
                    "generator_connection": second_connection,
                    "speedtree_material_groups": groups,
                    "mesh_ids": second_mesh_ids,
                }
                removed = speedtree.remove_atlas_scope_assets_from_spm(
                    target, [removal_manifest]
                )
            finally:
                speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = original_material_sample
                speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = original_mesh_sample

            restored_assets = speedtree.read_spm_xml(target).find("Assets")
            restored_material = restored_assets.find("Material_v8[@ID='8']")
            self.assertEqual(speedtree.spm_material_mesh_ids(restored_material), [1, 2, 9])
            self.assertEqual(restored_material.findtext("AuthoredValue"), "preserve-me")
            self.assertEqual(restored_material.findtext("Width"), "2048")
            self.assertEqual(restored_material.findtext("Height"), "2048")
            self.assertTrue(
                all(restored_assets.find(f"Mesh[@ID='{mesh_id}']") is not None for mesh_id in (1, 2, 9))
            )
            self.assertTrue(
                all(restored_assets.find(f"Mesh[@ID='{mesh_id}']") is None for mesh_id in (10, 11, 12))
            )
            self.assertEqual(
                generator_values(target),
                {("Frond 36", "Material:Frond:0"): (8, 1)},
            )
            restored_root = speedtree.read_spm_xml(target)
            restored_generator = next(restored_root.iter("Generator"))
            restored_state = speedtree.generator_variant_parent_state(
                restored_generator, "Material:Frond"
            )
            self.assertEqual(restored_state["child_count"], 1)
            self.assertEqual(removed["removed_materials"], [])
            self.assertEqual(removed["restored_adopted_materials"][0]["material_id"], 8)

    def test_adoption_retry_never_reuses_original_mesh_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            source = add_material(assets, 8, "M_branch", [3])
            source_mesh = add_mesh(assets, 3)
            add_generator(root, "Frond", "Frond 1", [(8, 3)])
            write_spm(target, root)

            sample = folder / "external_mesh_sample.spm"
            sample_root = ET.Element("SpeedTreeModel")
            sample_assets = ET.SubElement(sample_root, "Assets")
            sample_mesh = add_mesh(sample_assets, 100)
            ET.SubElement(sample_mesh, "Embedded").text = "false"
            write_spm(sample, sample_root)

            color = folder / "branch.tga"
            tga_header = bytearray(18)
            tga_header[2] = 2
            tga_header[12:14] = (1024).to_bytes(2, "little")
            tga_header[14:16] = (1024).to_bytes(2, "little")
            tga_header[16] = 24
            color.write_bytes(tga_header)
            mesh_items = [{
                "asset": str(folder / "meshes" / "branch_01.fbx"),
                "source_object": "branch_01",
                "source_ordinal": 1,
            }]
            manifest = {
                "export_scope_id": "scope-adopt-retry",
                "source_collection": "Plans",
                "material_collection": "Plans",
                "blend_file": str(folder / "normalized.blend"),
                "textures": canonical_test_textures(color),
                "meshes": mesh_items,
            }
            adoption = speedtree.prepare_source_material_adoption(
                target,
                manifest,
                "M_branch",
                8,
            )

            # Reproduce a retry boundary where the source material carries the
            # current scope marker but its authored Mesh ID is still live.
            speedtree.tag_spm_asset(source, manifest, "material")
            speedtree.tag_spm_asset(source_mesh, manifest, "mesh")
            write_spm(target, root)
            adoption = speedtree.prepare_source_material_adoption(
                target,
                manifest,
                "M_branch",
                8,
                {
                    **manifest,
                    "source_material_adoption": adoption,
                },
            )

            original_material_sample = speedtree.SPEEDTREE_101_MATERIAL_SAMPLE
            original_mesh_sample = speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE
            speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = sample
            speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = sample
            try:
                _, action, material_id, mesh_ids = (
                    speedtree.upsert_speedtree_assets_in_spm(
                        target,
                        manifest,
                        "M_branch",
                        adopt_source_material_id=8,
                        adopt_reserved_mesh_ids=(
                            speedtree.adoption_original_mesh_ids(adoption)
                        ),
                    )
                )
                groups = [{
                    "material": "M_branch",
                    "material_id": material_id,
                    "mesh_ids": mesh_ids,
                    "meshes": mesh_items,
                }]
                connection = speedtree.connect_atlas_generators_in_spm(
                    target,
                    ["M_branch"],
                    groups,
                    [8],
                    source_mesh_ids_by_name={"M_branch": [3]},
                    generator_variant_policy="ensure_all_material_cutouts",
                )
                finalized = speedtree.finalize_source_material_adoption(
                    target,
                    adoption,
                    mesh_ids,
                    connection,
                )
            finally:
                speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = (
                    original_material_sample
                )
                speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = (
                    original_mesh_sample
                )

            self.assertEqual(action, "updated")
            self.assertEqual(mesh_ids, [4])
            self.assertEqual(finalized["original_mesh_ids"], [3])
            self.assertEqual(finalized["generated_mesh_ids"], [4])

    def test_adoption_migrates_previous_separate_material_in_the_same_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 8, "M_branch", [1, 2, 9])
            marker = json.dumps(
                {
                    "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                    "scope": "scope-adopt",
                    "kind": "material",
                    "group": "Plans",
                }
            )
            add_material(assets, 9, "M_branch_plan", [10, 11, 12], marker)
            for mesh_id in (1, 2, 9):
                mesh = add_mesh(assets, mesh_id)
                ET.SubElement(mesh, "Embedded").text = "true"
            for mesh_id in (10, 11, 12):
                mesh = add_mesh(assets, mesh_id, marker.replace('"material"', '"mesh"'))
                ET.SubElement(mesh, "Embedded").text = "false"
            add_generator(root, "Frond", "Frond 36", [(9, 10)])
            write_spm(target, root)

            previous = {
                "export_scope_id": "scope-adopt",
                "speedtree_material_groups": [
                    {
                        "collection": "Plans",
                        "material": "M_branch_plan",
                        "material_id": 9,
                        "mesh_ids": [10, 11, 12],
                    }
                ],
                "generator_connection": {
                    "bindings": [
                        {
                            "generator_index": 0,
                            "generator_name": "Frond 36",
                            "generator_type": "Frond",
                            "slot_prefix": "Material:Frond:0",
                            "source_material_id": 8,
                            "source_material_name": "M_branch",
                            "source_mesh_id": 1,
                            "target_material_id": 9,
                            "target_mesh_id": 10,
                        }
                    ]
                },
            }
            migration = speedtree.migrate_previous_scope_material_for_adoption(
                target, previous, "M_branch", 8
            )
            migrated_assets = speedtree.read_spm_xml(target).find("Assets")
            self.assertEqual(migration["legacy_material_id"], 9)
            self.assertEqual(migration["reusable_mesh_ids"], [10, 11, 12])
            self.assertIsNone(migrated_assets.find("Material_v8[@ID='9']"))
            self.assertTrue(
                all(migrated_assets.find(f"Mesh[@ID='{mesh_id}']") is not None for mesh_id in (10, 11, 12))
            )
            self.assertEqual(
                generator_values(target),
                {("Frond 36", "Material:Frond:0"): (8, 1)},
            )

    def test_adoption_migrates_same_name_managed_output_to_live_source(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 8, "M_leaf", [1])
            marker = json.dumps(
                {
                    "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                    "scope": "scope-adopt",
                    "kind": "material",
                    "group": "Plans",
                }
            )
            add_material(assets, 19, "M_leaf", [20], marker)
            add_mesh(assets, 1)
            add_mesh(
                assets,
                20,
                marker.replace('"material"', '"mesh"'),
            )
            add_generator(root, "Leaf Mesh", "Leaf 1", [(8, 1)])
            write_spm(target, root)

            selected = speedtree.source_material_for_adoption(
                root,
                "M_leaf",
                8,
            )
            self.assertEqual(selected.attrib.get("ID"), "8")

            previous = {
                "export_scope_id": "scope-adopt",
                "speedtree_material_groups": [
                    {
                        "collection": "Plans",
                        "material": "M_leaf",
                        "material_id": 19,
                        "mesh_ids": [20],
                    }
                ],
                "generator_connection": {
                    "requested": False,
                    "complete": False,
                    "bindings": [],
                },
            }
            migration = (
                speedtree.migrate_previous_scope_material_for_adoption(
                    target,
                    previous,
                    "M_leaf",
                    8,
                )
            )

            migrated_assets = speedtree.read_spm_xml(target).find("Assets")
            self.assertEqual(migration["legacy_material_id"], 19)
            self.assertEqual(migration["reusable_mesh_ids"], [20])
            self.assertIsNotNone(
                migrated_assets.find("Material_v8[@ID='8']")
            )
            self.assertIsNone(
                migrated_assets.find("Material_v8[@ID='19']")
            )
            self.assertEqual(
                generator_values(target),
                {("Leaf 1", "Leaves:Type:0"): (8, 1)},
            )

    def test_adoption_migration_accepts_managed_output_already_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 8, "M_leaf", [1])
            add_mesh(assets, 1)
            write_spm(target, root)
            previous = {
                "export_scope_id": "scope-adopt",
                "speedtree_material_groups": [{
                    "collection": "Plans",
                    "material": "M_leaf",
                    "material_id": 19,
                    "mesh_ids": [20],
                }],
                "generator_connection": {"bindings": []},
            }

            migration = speedtree.migrate_previous_scope_material_for_adoption(
                target,
                previous,
                "M_leaf",
                8,
            )

            self.assertTrue(migration["already_removed"])
            self.assertEqual(migration["legacy_material_id"], 19)
            self.assertEqual(migration["reusable_mesh_ids"], [])

    def test_untagged_unused_same_name_with_different_mesh_paths_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as folder:
            target, sample, manifest, material_name = self._write_upsert_fixture(folder, None)
            manifest["meshes"] = [{"asset": str(Path(folder) / "meshes" / "different.fbx")}]
            original_material_sample = speedtree.SPEEDTREE_101_MATERIAL_SAMPLE
            original_mesh_sample = speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE
            speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = sample
            speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = sample
            try:
                _, action, material_id, mesh_ids = (
                    speedtree.upsert_speedtree_assets_in_spm(
                        target,
                        manifest,
                        material_name,
                    )
                )
            finally:
                speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = original_material_sample
                speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = original_mesh_sample

            assets = speedtree.read_spm_xml(target).find("Assets")
            material = assets.find("Material_v8[@ID='8']")
            self.assertEqual(action, "reclaimed")
            self.assertEqual(material_id, 8)
            self.assertEqual(mesh_ids, [19])
            self.assertEqual(speedtree.spm_material_mesh_ids(material), [19])
            self.assertIsNone(assets.find("Mesh[@ID='18']"))
            self.assertIsNotNone(assets.find("Mesh[@ID='19']"))
            self.assertEqual(
                speedtree.parse_atlas_leaf_spm_user_data(
                    material.findtext("UserData")
                )["scope"],
                "new-uuid-scope",
            )

    def test_untagged_used_same_name_with_different_mesh_paths_still_conflicts(self):
        with tempfile.TemporaryDirectory() as folder:
            target, sample, manifest, material_name = self._write_upsert_fixture(
                folder,
                None,
            )
            root = speedtree.read_spm_xml(target)
            add_generator(root, "Leaf Mesh", "Leaf 5", [(8, 18)])
            write_spm(target, root)
            manifest["meshes"] = [
                {"asset": str(Path(folder) / "meshes" / "different.fbx")}
            ]
            before = target.read_bytes()
            original_material_sample = speedtree.SPEEDTREE_101_MATERIAL_SAMPLE
            original_mesh_sample = speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE
            speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = sample
            speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = sample
            try:
                with self.assertRaisesRegex(RuntimeError, "Material name conflict"):
                    speedtree.upsert_speedtree_assets_in_spm(
                        target,
                        manifest,
                        material_name,
                    )
            finally:
                speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = original_material_sample
                speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = original_mesh_sample
            self.assertEqual(target.read_bytes(), before)

    def test_untagged_hidden_generator_same_name_is_reclaimed_as_assets_only(self):
        with tempfile.TemporaryDirectory() as folder:
            target, sample, manifest, material_name = self._write_upsert_fixture(
                folder,
                None,
            )
            root = speedtree.read_spm_xml(target)
            hidden = add_generator(
                root,
                "Frond",
                "Hidden stale Frond",
                [(8, 18)],
            )
            ET.SubElement(hidden, "Hidden").text = "true"
            write_spm(target, root)
            manifest["meshes"] = [
                {"asset": str(Path(folder) / "meshes" / "replacement.fbx")}
            ]
            original_material_sample = speedtree.SPEEDTREE_101_MATERIAL_SAMPLE
            original_mesh_sample = speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE
            speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = sample
            speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = sample
            try:
                _, action, material_id, mesh_ids = (
                    speedtree.upsert_speedtree_assets_in_spm(
                        target,
                        manifest,
                        material_name,
                    )
                )
            finally:
                speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = original_material_sample
                speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = original_mesh_sample

            parsed = speedtree.read_spm_xml(target)
            assets = parsed.find("Assets")
            material = assets.find("Material_v8[@ID='8']")
            self.assertEqual(
                speedtree.spm_generator_referenced_material_ids(parsed),
                {8},
            )
            self.assertEqual(
                speedtree.spm_visible_generator_referenced_material_ids(parsed),
                set(),
            )
            self.assertEqual(action, "reclaimed")
            self.assertEqual(material_id, 8)
            self.assertEqual(mesh_ids, [19])
            self.assertEqual(
                speedtree.spm_material_mesh_ids(material),
                [19],
            )
            # Destructive cleanup still protects the stale hidden slot's mesh.
            self.assertIsNotNone(assets.find("Mesh[@ID='18']"))
            self.assertIsNotNone(assets.find("Mesh[@ID='19']"))

    def test_legacy_name_scope_is_updated_and_retagged_with_uuid(self):
        with tempfile.TemporaryDirectory() as folder:
            target, sample, manifest, material_name = self._write_upsert_fixture(
                folder, "M_cluster_lauraceae_atlas_01"
            )
            original_material_sample = speedtree.SPEEDTREE_101_MATERIAL_SAMPLE
            original_mesh_sample = speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE
            speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = sample
            speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = sample
            try:
                _, action, material_id, mesh_ids = speedtree.upsert_speedtree_assets_in_spm(
                    target, manifest, material_name
                )
            finally:
                speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = original_material_sample
                speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = original_mesh_sample

            material = speedtree.find_material_by_name(
                speedtree.read_spm_xml(target).find("Assets"), material_name
            )
            self.assertEqual(action, "updated")
            self.assertEqual(material_id, 8)
            self.assertEqual(mesh_ids, [18])
            self.assertEqual(
                speedtree.parse_atlas_leaf_spm_user_data(material.findtext("UserData"))["scope"],
                "new-uuid-scope",
            )

    def test_different_uuid_scope_still_blocks_same_name_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            target, sample, manifest, material_name = self._write_upsert_fixture(
                folder, "another-uuid-scope"
            )
            before = target.read_bytes()
            original_material_sample = speedtree.SPEEDTREE_101_MATERIAL_SAMPLE
            original_mesh_sample = speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE
            speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = sample
            speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = sample
            try:
                with self.assertRaisesRegex(RuntimeError, "Material name conflict"):
                    speedtree.upsert_speedtree_assets_in_spm(target, manifest, material_name)
            finally:
                speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = original_material_sample
                speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = original_mesh_sample
            self.assertEqual(target.read_bytes(), before)

    def test_legacy_scope_must_equal_the_requested_material_name(self):
        with tempfile.TemporaryDirectory() as folder:
            target, sample, manifest, material_name = self._write_upsert_fixture(
                folder, "generic-source-collection"
            )
            manifest["source_collection"] = "generic-source-collection"
            before = target.read_bytes()
            original_material_sample = speedtree.SPEEDTREE_101_MATERIAL_SAMPLE
            original_mesh_sample = speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE
            speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = sample
            speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = sample
            try:
                with self.assertRaisesRegex(RuntimeError, "Material name conflict"):
                    speedtree.upsert_speedtree_assets_in_spm(target, manifest, material_name)
            finally:
                speedtree.SPEEDTREE_101_MATERIAL_SAMPLE = original_material_sample
                speedtree.SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = original_mesh_sample
            self.assertEqual(target.read_bytes(), before)

    def test_cleanup_preserves_generator_referenced_material_and_mesh(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "target.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            marker = json.dumps(
                {"generator": speedtree.ATLAS_LEAF_SPM_GENERATOR, "scope": "scope-a", "kind": "material"}
            )
            add_material(assets, 5, "old_atlas", [99], marker)
            add_mesh(assets, 99, marker.replace('"material"', '"mesh"'))
            add_generator(root, "Leaf Mesh", "Leaf", [(5, 99)])
            write_spm(path, root)
            result = speedtree.cleanup_stale_spm_assets(
                path, {"export_scope_id": "scope-a", "meshes": []}, set()
            )
            parsed_assets = speedtree.read_spm_xml(path).find("Assets")
            self.assertEqual(result["removed_materials"], [])
            self.assertIsNotNone(parsed_assets.find("Material_v8[@ID='5']"))
            self.assertIsNotNone(parsed_assets.find("Mesh[@ID='99']"))
            self.assertNotIn(99, result["removed_mesh_ids"])

    def test_remove_target_without_manifest_fails_when_managed_material_remains(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            blend = folder / "M_leaf_test_atlas_01.blend"
            target = folder / "SK_test.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            marker = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": "scope-a",
                "kind": "material",
            })
            add_material(assets, 5, blend.stem, [99], marker)
            add_mesh(assets, 99, marker.replace('"material"', '"mesh"'))
            write_spm(target, root)

            with self.assertRaisesRegex(RuntimeError, "per-scope manifest is missing"):
                speedtree.remove_blend_target_from_spm(blend, target)

    def test_remove_unbuilt_target_without_manifest_is_a_safe_noop(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            blend = folder / "M_leaf_test_atlas_01.blend"
            target = folder / "SK_test.spm"
            root = ET.Element("SpeedTreeModel")
            ET.SubElement(root, "Assets")
            write_spm(target, root)

            result = speedtree.remove_blend_target_from_spm(blend, target)

            self.assertEqual(result["status"], "no_managed_manifest")
            self.assertIsNone(result["backup"])

    def test_remove_target_retires_only_exact_operational_manifests(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            blend = folder / "M_leaf_test_atlas_01.blend"
            target = folder / "SK_test.spm"
            root = ET.Element("SpeedTreeModel")
            ET.SubElement(root, "Assets")
            write_spm(target, root)
            manifest = {
                "export_scope_id": "scope-a",
                "blend_file": str(blend),
                "spm": str(target),
                "speedtree_material_groups": [],
                "meshes": [],
            }
            target_scope = speedtree.write_scope_manifest(
                folder,
                manifest,
                target,
            )
            scope_identity = speedtree.write_scope_manifest(folder, manifest)
            per_target = speedtree.target_manifest_path(target)
            per_target.parent.mkdir(parents=True, exist_ok=True)
            per_target.write_text(json.dumps(manifest), encoding="utf-8")
            global_manifest = folder / "speedtree_import_manifest.json"
            global_manifest.write_text(json.dumps(manifest), encoding="utf-8")

            result = speedtree.remove_blend_target_from_spm(blend, target)

            self.assertEqual(result["status"], "already_clean")
            self.assertEqual(
                set(result["retired_contract_manifests"]),
                {str(target_scope), str(per_target), str(global_manifest)},
            )
            self.assertFalse(target_scope.exists())
            self.assertFalse(per_target.exists())
            self.assertFalse(global_manifest.exists())
            self.assertTrue(scope_identity.exists())

    def test_on_refresh_preserves_exact_scope_adoption_history_only(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            blend = folder / "SK_cluster_test_01.blend"
            target = folder / "SK_tree_01.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            marker = json.dumps({
                "generator": speedtree.ATLAS_LEAF_SPM_GENERATOR,
                "scope": "scope-a",
                "kind": "material",
            })
            add_material(
                assets,
                8,
                "M_cluster_test_01",
                [10],
                marker,
            )
            add_mesh(
                assets,
                10,
                marker.replace('"material"', '"mesh"'),
            )
            write_spm(target, root)
            original_assets = ET.Element("Assets")
            original_material = add_material(
                original_assets,
                8,
                "M_cluster_test_01",
                [1],
            )
            original_mesh = add_mesh(original_assets, 1)
            manifest = {
                "export_scope_id": "scope-a",
                "blend_file": str(blend),
                "spm": str(target),
                "speedtree_material_groups": [{
                    "material": "M_cluster_test_01",
                    "material_id": 8,
                    "mesh_ids": [10],
                }],
                "meshes": [{"asset": str(folder / "mesh.fbx")}],
                "source_material_adoption": {
                    "version": speedtree.SOURCE_MATERIAL_ADOPTION_VERSION,
                    "material_name": "M_cluster_test_01",
                    "material_id": 8,
                    "original_mesh_ids": [1],
                    "original_material_snapshot": (
                        speedtree.encode_spm_node_snapshot(
                            original_material
                        )
                    ),
                    "original_mesh_snapshots": [
                        {
                            "mesh_id": 1,
                            "snapshot": (
                                speedtree.encode_spm_node_snapshot(
                                    original_mesh
                                )
                            ),
                        },
                    ],
                    "generated_mesh_ids": [10],
                },
                "generator_connection": {
                    "complete": True,
                    "bindings": [{
                        "generator_guid": "stale-guid",
                        "slot_prefix": "Leaves:Type:2",
                    }],
                },
            }
            target_scope = speedtree.write_scope_manifest(
                folder,
                manifest,
                target,
            )
            per_target = speedtree.target_manifest_path(target)
            per_target.parent.mkdir(parents=True, exist_ok=True)
            per_target.write_text(json.dumps(manifest), encoding="utf-8")
            global_manifest = folder / "speedtree_import_manifest.json"
            global_manifest.write_text(json.dumps(manifest), encoding="utf-8")

            result = speedtree.remove_blend_target_from_spm(
                blend,
                target,
                preserve_scope_history=True,
            )
            first_restored_xml = target.read_bytes()
            repeated = speedtree.remove_blend_target_from_spm(
                blend,
                target,
                preserve_scope_history=True,
            )

            self.assertEqual(
                result["retained_scope_manifests"],
                [str(target_scope)],
            )
            self.assertEqual(
                set(result["retired_contract_manifests"]),
                {str(per_target), str(global_manifest)},
            )
            self.assertTrue(target_scope.exists())
            retained = json.loads(
                target_scope.read_text(encoding="utf-8")
            )
            self.assertNotIn("generator_connection", retained)
            self.assertIn("source_material_adoption", retained)
            self.assertFalse(per_target.exists())
            self.assertFalse(global_manifest.exists())
            self.assertEqual(target.read_bytes(), first_restored_xml)
            self.assertEqual(
                repeated["cleanup"]["restored_adopted_materials"],
                [{
                    "material_id": 8,
                    "material_name": "M_cluster_test_01",
                    "mesh_ids": [1],
                    "mesh_states": [{
                        "mesh_id": 1,
                        "state": "already_restored",
                    }],
                }],
            )

    def test_adoption_restore_rejects_different_mesh_id_occupant_with_diagnostics(self):
        assets = ET.Element("Assets")
        managed = add_material(
            assets,
            8,
            "M_cluster_test_01",
            [10],
        )
        original_assets = ET.Element("Assets")
        original_material = add_material(
            original_assets,
            8,
            "M_cluster_test_01",
            [1],
        )
        original_mesh = add_mesh(original_assets, 1)
        original_mesh.find("Filename").text = "authored.fbx"
        occupied = add_mesh(assets, 1)
        occupied.find("Filename").text = "unrelated.fbx"
        adoption = {
            "version": speedtree.SOURCE_MATERIAL_ADOPTION_VERSION,
            "scope": "scope-conflict",
            "material_name": "M_cluster_test_01",
            "material_id": 8,
            "original_mesh_ids": [1],
            "original_material_snapshot": (
                speedtree.encode_spm_node_snapshot(original_material)
            ),
            "original_mesh_snapshots": [{
                "mesh_id": 1,
                "snapshot": speedtree.encode_spm_node_snapshot(original_mesh),
            }],
        }

        with self.assertRaisesRegex(
            RuntimeError,
            (
                r"occupied by a different node .*"
                r"material_id=8.*scope='scope-conflict'.*"
                r"existing_filename='unrelated\.fbx'.*"
                r"existing_sha256=.*expected_sha256="
            ),
        ):
            speedtree.restore_adopted_source_nodes(
                assets,
                {8: adoption},
            )

        self.assertIsNotNone(managed)

    def test_adoption_restore_reuses_exact_retained_shared_source_mesh(self):
        assets = ET.Element("Assets")
        add_material(
            assets,
            2,
            "M_authored_shared_source",
            [1],
        )
        add_material(
            assets,
            19,
            "M_cluster_test_01",
            [21],
        )
        retained = add_mesh(assets, 1)
        generated = add_mesh(assets, 21)
        original_assets = ET.Element("Assets")
        original_material = add_material(
            original_assets,
            19,
            "M_cluster_test_01",
            [1],
        )
        adoption = {
            "version": speedtree.SOURCE_MATERIAL_ADOPTION_VERSION,
            "scope": "scope-shared-source",
            "material_name": "M_cluster_test_01",
            "material_id": 19,
            "original_mesh_ids": [1],
            "original_material_snapshot": (
                speedtree.encode_spm_node_snapshot(original_material)
            ),
            "original_mesh_snapshots": [{
                "mesh_id": 1,
                "snapshot": speedtree.encode_spm_node_snapshot(retained),
            }],
            "retained_shared_original_mesh_ids": [1],
            "generated_mesh_ids": [21],
        }

        restored = speedtree.restore_adopted_source_nodes(
            assets,
            {19: adoption},
        )

        self.assertEqual(
            speedtree.spm_material_mesh_ids(
                assets.find("Material_v8[@ID='19']")
            ),
            [1],
        )
        self.assertEqual(
            len([
                node
                for node in assets.findall("Mesh")
                if node.attrib.get("ID") == "1"
            ]),
            1,
        )
        self.assertIsNotNone(generated)
        self.assertEqual(
            restored[0]["mesh_states"],
            [{"mesh_id": 1, "state": "already_restored"}],
        )

    def test_scope_manifest_without_exact_spm_is_not_cleanup_input(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            blend = folder / "SK_cluster_test_01.blend"
            target = folder / "SK_tree_01.spm"
            scope_dir = folder / ".atlas_leaf_speedtree_scopes"
            scope_dir.mkdir()
            legacy_scope = (
                scope_dir
                / f"legacy__{speedtree.target_manifest_key(target)}.json"
            )
            legacy_scope.write_text(
                json.dumps({
                    "export_scope_id": "legacy",
                    "blend_file": str(blend),
                    "generator_connection": {"complete": True},
                }),
                encoding="utf-8",
            )

            manifests = speedtree.target_scope_manifests_for_blend(
                target,
                blend,
            )

            self.assertEqual(manifests, [])
            self.assertIn(
                "generator_connection",
                json.loads(legacy_scope.read_text(encoding="utf-8")),
            )

    def test_cleanup_preserves_mesh_referenced_by_a_different_material(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "target.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            marker = json.dumps(
                {"generator": speedtree.ATLAS_LEAF_SPM_GENERATOR, "scope": "scope-a", "kind": "material"}
            )
            add_material(assets, 5, "old_atlas", [99], marker)
            add_material(assets, 6, "current_atlas", [100])
            add_mesh(assets, 99, marker.replace('"material"', '"mesh"'))
            add_mesh(assets, 100)
            add_generator(root, "Leaf Mesh", "Leaf", [(6, 99)])
            write_spm(path, root)

            result = speedtree.cleanup_stale_spm_assets(
                path,
                {"export_scope_id": "scope-a", "meshes": []},
                {"current_atlas"},
            )

            parsed_assets = speedtree.read_spm_xml(path).find("Assets")
            self.assertEqual(result["removed_materials"], ["old_atlas"])
            self.assertIsNone(parsed_assets.find("Material_v8[@ID='5']"))
            self.assertIsNotNone(parsed_assets.find("Mesh[@ID='99']"))
            self.assertNotIn(99, result["removed_mesh_ids"])

    def test_explicit_cluster_output_is_canonicalized_but_legacy_is_not(self):
        self.assertEqual(
            speedtree.canonical_new_atlas_asset_name("M_cluster_ladyfern_atlas_01"),
            "M_leaf_ladyfern_atlas_01",
        )
        root_collection = types.SimpleNamespace(name="M_cluster_legacy")
        original_bpy = speedtree.bpy
        speedtree.bpy = types.SimpleNamespace(
            data=types.SimpleNamespace(filepath="D:/atlas/M_cluster_legacy.blend")
        )
        try:
            self.assertEqual(speedtree.blender_material_base_name(root_collection), "M_cluster_legacy")
        finally:
            speedtree.bpy = original_bpy

    def test_cluster_handoff_can_preserve_explicit_cluster_material_name(self):
        root_collection = types.SimpleNamespace(name="Atlas_Branch_Plans")
        self.assertEqual(
            speedtree.blender_material_base_name(
                root_collection,
                "M_cluster_densiflora_01",
                preserve_explicit_material_name=True,
            ),
            "M_cluster_densiflora_01",
        )

    def test_missing_target_requires_explicit_create(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "explicit create"):
                speedtree.upsert_speedtree_assets_in_spm(
                    Path(folder) / "missing.spm", {}, "M_leaf_test"
                )

    def test_public_update_restores_target_when_implementation_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "target.spm"
            path.write_bytes(b"original")
            original_impl = speedtree._export_or_update_speedtree_spm_path_impl

            def fail_after_write(_props, target, **_kwargs):
                Path(target).write_bytes(b"partial")
                raise RuntimeError("forced failure")

            speedtree._export_or_update_speedtree_spm_path_impl = fail_after_write
            try:
                with self.assertRaisesRegex(RuntimeError, "forced failure"):
                    speedtree.export_or_update_speedtree_spm_path(object(), path)
                self.assertEqual(path.read_bytes(), b"original")
            finally:
                speedtree._export_or_update_speedtree_spm_path_impl = original_impl

    def test_scope_forks_for_different_blend_when_old_file_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            collection = {speedtree.ATLAS_LEAF_COLLECTION_SCOPE_KEY: "shared-scope"}
            speedtree.write_scope_manifest(
                folder,
                {
                    "export_scope_id": "shared-scope",
                    "blend_file": str(Path(folder) / "missing_original.blend"),
                    "texture_signature": "same-textures",
                },
            )
            original_bpy = speedtree.bpy
            speedtree.bpy = types.SimpleNamespace(
                data=types.SimpleNamespace(filepath=str(Path(folder) / "copied.blend"))
            )
            try:
                scope = speedtree.resolve_export_scope_id(collection, folder, "same-textures")
            finally:
                speedtree.bpy = original_bpy
            self.assertNotEqual(scope, "shared-scope")

    def test_scope_is_preserved_when_textures_change_in_the_same_blend(self):
        with tempfile.TemporaryDirectory() as folder:
            collection = {speedtree.ATLAS_LEAF_COLLECTION_SCOPE_KEY: "shared-scope"}
            blend_path = str(Path(folder) / "atlas.blend")
            speedtree.write_scope_manifest(
                folder,
                {
                    "export_scope_id": "shared-scope",
                    "blend_file": blend_path,
                    "texture_signature": "old-textures",
                },
            )
            original_bpy = speedtree.bpy
            speedtree.bpy = types.SimpleNamespace(data=types.SimpleNamespace(filepath=blend_path))
            try:
                scope = speedtree.resolve_export_scope_id(collection, folder, "new-textures")
            finally:
                speedtree.bpy = original_bpy
            self.assertEqual(scope, "shared-scope")

    def test_copied_collection_with_the_same_scope_gets_a_new_uuid(self):
        class FakeCollection(dict):
            def __init__(self, name, scope):
                super().__init__({speedtree.ATLAS_LEAF_COLLECTION_SCOPE_KEY: scope})
                self.name = name

        with tempfile.TemporaryDirectory() as folder:
            blend_path = str(Path(folder) / "atlas.blend")
            owner = FakeCollection("Original", "shared-scope")
            duplicate = FakeCollection("Original.001", "shared-scope")
            speedtree.write_scope_manifest(
                folder,
                {
                    "export_scope_id": "shared-scope",
                    "source_collection": owner.name,
                    "blend_file": blend_path,
                    "texture_signature": "old-textures",
                },
            )
            original_bpy = speedtree.bpy
            speedtree.bpy = types.SimpleNamespace(
                data=types.SimpleNamespace(
                    filepath=blend_path,
                    collections=[owner, duplicate],
                )
            )
            try:
                owner_scope = speedtree.resolve_export_scope_id(owner, folder, "new-textures")
                duplicate_scope = speedtree.resolve_export_scope_id(
                    duplicate, folder, "different-textures"
                )
            finally:
                speedtree.bpy = original_bpy
            self.assertEqual(owner_scope, "shared-scope")
            self.assertNotEqual(duplicate_scope, "shared-scope")

    def test_copied_collection_forks_even_when_scope_manifest_is_missing(self):
        class FakeCollection(dict):
            def __init__(self, name, scope):
                super().__init__({speedtree.ATLAS_LEAF_COLLECTION_SCOPE_KEY: scope})
                self.name = name

        with tempfile.TemporaryDirectory() as folder:
            owner = FakeCollection("Original", "shared-scope")
            duplicate = FakeCollection("Original.001", "shared-scope")
            original_bpy = speedtree.bpy
            speedtree.bpy = types.SimpleNamespace(
                data=types.SimpleNamespace(
                    filepath=str(Path(folder) / "atlas.blend"),
                    collections=[owner, duplicate],
                )
            )
            try:
                duplicate_scope = speedtree.resolve_export_scope_id(
                    duplicate, folder, "different-textures"
                )
            finally:
                speedtree.bpy = original_bpy
            self.assertNotEqual(duplicate_scope, "shared-scope")


class DeletedMeshLifecycleTests(unittest.TestCase):
    def _managed_manifest(self, target, blend, scope="scope-current"):
        return {
            "export_scope_id": scope,
            "blend_file": str(blend),
            "source_collection": "Atlas_Cluster_Cards",
            "spm": str(target),
            "material_groups": [{
                "collection": "Atlas_Cluster_Cards",
                "material": "M_leaf_test",
                "meshes": [
                    {"source_object": f"leaf_{ordinal:02d}"}
                    for ordinal in range(1, 4)
                ],
            }],
            "speedtree_material_groups": [{
                "collection": "Atlas_Cluster_Cards",
                "material": "M_leaf_test",
                "material_id": 8,
                "mesh_ids": [10, 11, 12],
            }],
        }

    def _binding(self, generator, slot, ordinal, *, source=True, created=False):
        binding = {
            "generator_index": 0,
            "generator_name": "Leaf",
            "generator_guid": speedtree.generator_guid(generator),
            "generator_type": "Leaf Mesh",
            "slot_prefix": f"Leaves:Type:{slot}",
            "source_material_id": 4 if source else None,
            "source_material_name": "M_source" if source else None,
            "source_mesh_id": ordinal if source else None,
            "target_material_id": 8,
            "target_mesh_id": 9 + ordinal,
            "leaf_ordinal": ordinal,
            "created_slot": created,
        }
        return binding

    def test_middle_deleted_ordinal_restores_authored_generator_binding(self):
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_source", [1, 2, 3])
        add_material(assets, 8, "M_leaf_test", [10, 11, 12])
        for mesh_id in (1, 2, 3, 10, 11, 12):
            add_mesh(assets, mesh_id)
        generator = add_variant_generator(
            root,
            "Leaf Mesh",
            "Leaf",
            [(8, 10), (8, 11), (8, 12)],
        )
        bindings = [
            self._binding(generator, slot, ordinal)
            for slot, ordinal in enumerate((1, 2, 3))
        ]

        result = speedtree.retire_deleted_generator_bindings(
            root,
            bindings,
            {
                1: {"target_material_id": 8, "target_mesh_id": 10},
                3: {"target_material_id": 8, "target_mesh_id": 12},
            },
            {4: {"material": assets.find("Material_v8[@ID='4']"), "mesh_ids": [1, 2, 3]}},
        )

        pairs = {
            pair["slot_prefix"]: (
                speedtree.positive_int(pair["material_property"].findtext("Value")),
                speedtree.integer_value(pair["mesh_property"].findtext("Value")),
            )
            for pair in speedtree.spm_generator_property_pairs(root)
        }
        self.assertEqual(pairs["Leaves:Type:1"], (4, 2))
        self.assertEqual(
            [binding["leaf_ordinal"] for binding in result["active_bindings"]],
            [1, 3],
        )
        self.assertEqual(
            result["retired_bindings"][0]["mode"],
            "restored_original_binding",
        )

    def test_connect_tombstones_middle_deleted_ordinal_under_default_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 4, "M_source", [1, 2, 3])
            add_material(assets, 8, "M_leaf_test", [10, 11, 12])
            for mesh_id in (1, 2, 3, 10, 11, 12):
                add_mesh(assets, mesh_id)
            generator = add_variant_generator(
                root,
                "Leaf Mesh",
                "Leaf",
                [(8, 10), (8, 11), (8, 12)],
            )
            write_spm(target, root)
            previous = [
                self._binding(generator, slot, ordinal)
                for slot, ordinal in enumerate((1, 2, 3))
            ]
            groups = [{
                "material": "M_leaf_test",
                "material_id": 8,
                "mesh_ids": [10, 12],
                "meshes": [
                    {"source_object": "leaf_01", "source_ordinal": 1},
                    {"source_object": "leaf_03", "source_ordinal": 3},
                ],
            }]

            result = speedtree.connect_atlas_generators_in_spm(
                target,
                ["M_source"],
                groups,
                [4],
                previous_bindings=previous,
            )

            self.assertEqual(
                result["generator_variant_policy"],
                "preserve_existing_slots",
            )
            self.assertEqual(
                result["retired_deleted_bindings"][0]["mode"],
                "restored_original_binding",
            )
            self.assertEqual(
                generator_values(target)[("Leaf", "Leaves:Type:1")],
                (4, 2),
            )

    def test_ensure_all_policy_does_not_resurrect_deleted_ordinal(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 4, "M_source", [1, 2])
            add_material(assets, 8, "M_leaf_test", [10, 11])
            for mesh_id in (1, 2, 10, 11):
                add_mesh(assets, mesh_id)
            generator = add_variant_generator(
                root,
                "Leaf Mesh",
                "Leaf",
                [(8, 10), (8, 11)],
            )
            write_spm(target, root)
            previous = [
                self._binding(generator, slot, ordinal)
                for slot, ordinal in enumerate((1, 2))
            ]

            result = speedtree.connect_atlas_generators_in_spm(
                target,
                ["M_source"],
                [{
                    "material": "M_leaf_test",
                    "material_id": 8,
                    "mesh_ids": [10],
                    "meshes": [{
                        "source_object": "leaf_01",
                        "source_ordinal": 1,
                    }],
                }],
                [4],
                previous_bindings=previous,
                generator_variant_policy="ensure_all_material_cutouts",
            )

            self.assertEqual(
                generator_values(target)[("Leaf", "Leaves:Type:1")],
                (4, 2),
            )
            self.assertEqual(
                result["retired_deleted_bindings"][0]["target_mesh_id"],
                11,
            )

    def test_adopted_deleted_ordinal_restores_captured_source_mesh_snapshot(self):
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        material = add_material(assets, 4, "M_source", [10, 11])
        for mesh_id in (10, 11):
            add_mesh(assets, mesh_id)
        generator = add_variant_generator(
            root,
            "Leaf Mesh",
            "Leaf",
            [(4, 10), (4, 11)],
        )
        original_mesh = ET.Element("Mesh", {"ID": "2", "Name": "source_02"})
        ET.SubElement(original_mesh, "Embedded").text = "true"
        previous_binding = self._binding(generator, 1, 2)
        previous_binding["target_material_id"] = 4
        previous_binding["target_mesh_id"] = 11
        ownership_manifest = {
            "export_scope_id": "scope-current",
            "source_material_adoption": {
                "material_id": 4,
                "original_mesh_snapshots": [{
                    "mesh_id": 2,
                    "snapshot": speedtree.encode_spm_node_snapshot(original_mesh),
                }],
            },
        }

        result = speedtree.retire_deleted_generator_bindings(
            root,
            [previous_binding],
            {1: {"target_material_id": 4, "target_mesh_id": 10}},
            {4: {"material": material, "mesh_ids": [1, 2]}},
            ownership_manifest=ownership_manifest,
        )

        self.assertIsNotNone(assets.find("Mesh[@ID='2']"))
        pair = speedtree.spm_generator_property_pairs(root)[1]
        self.assertEqual(pair["material_property"].findtext("Value"), "4")
        self.assertEqual(pair["mesh_property"].findtext("Value"), "2")
        self.assertEqual(
            result["retired_bindings"][0]["mode"],
            "restored_original_binding_and_mesh_snapshot",
        )

    def test_last_created_tail_outputs_are_removed_only_with_full_provenance(self):
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_source", [1, 2, 3])
        add_material(assets, 8, "M_leaf_test", [10, 11, 12])
        for mesh_id in (1, 2, 3, 10, 11, 12):
            add_mesh(assets, mesh_id)
        generator = add_variant_generator(
            root,
            "Leaf Mesh",
            "Leaf",
            [(8, 10), (8, 11), (8, 12)],
        )
        bindings = [self._binding(generator, 0, 1)]
        for slot, ordinal in ((1, 2), (2, 3)):
            binding = self._binding(
                generator,
                slot,
                ordinal,
                created=True,
            )
            binding.update({
                "variant_parent_property": "Leaves:Type",
                "variant_parent_children_before": 1,
                "variant_parent_children_after": 3,
                "created_material_property": f"Leaves:Type:{slot}:Material",
                "created_mesh_property": f"Leaves:Type:{slot}:Mesh",
            })
            bindings.append(binding)

        result = speedtree.retire_deleted_generator_bindings(
            root,
            bindings,
            {1: {"target_material_id": 8, "target_mesh_id": 10}},
            {4: {"material": assets.find("Material_v8[@ID='4']"), "mesh_ids": [1, 2, 3]}},
        )

        state = speedtree.generator_variant_parent_state(
            generator,
            "Leaves:Type",
        )
        self.assertEqual(state["child_count"], 1)
        self.assertEqual(
            [item["mode"] for item in result["retired_bindings"]],
            ["removed_created_variant_slot", "removed_created_variant_slot"],
        )

    def test_surviving_created_interval_rebases_after_generator_slot_contraction(self):
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 1, "M_cluster", [106, 108, 109])
        for mesh_id in (106, 108, 109):
            add_mesh(assets, mesh_id)
        generator = add_variant_generator(
            root,
            "Leaf Mesh",
            "Leaf 4",
            [(1, 106), (1, 108), (1, 109)],
        )
        bindings = []
        for slot, ordinal, mesh_id in ((1, 3, 108), (2, 4, 109)):
            binding = self._binding(generator, slot, ordinal, created=True)
            binding.update({
                "variant_parent_property": "Leaves:Type",
                "variant_parent_children_before": 1,
                "variant_parent_children_after": 4,
                "created_material_property": f"Leaves:Type:{slot}:Material",
                "created_mesh_property": f"Leaves:Type:{slot}:Mesh",
                "target_material_id": 1,
                "target_mesh_id": mesh_id,
            })
            bindings.append(binding)

        repairs = speedtree.repair_created_generator_variant_slots(
            root,
            bindings,
        )

        self.assertEqual(len(repairs), 2)
        self.assertEqual(
            [item["slot_prefix"] for item in repairs],
            ["Leaves:Type:1", "Leaves:Type:2"],
        )
        self.assertEqual(
            {item["variant_parent_children_after"] for item in repairs},
            {3},
        )

    def test_remove_created_interval_rebases_after_generator_slot_contraction(self):
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 1, "M_cluster", [106, 108, 109])
        for mesh_id in (106, 108, 109):
            add_mesh(assets, mesh_id)
        generator = add_variant_generator(
            root,
            "Leaf Mesh",
            "Leaf 4",
            [(1, 106), (1, 108), (1, 109)],
        )
        bindings = []
        for slot, ordinal, mesh_id in (
            (1, 2, 107),
            (2, 3, 108),
            (3, 4, 109),
        ):
            binding = self._binding(generator, slot, ordinal, created=True)
            binding.update({
                "variant_parent_property": "Leaves:Type",
                "variant_parent_children_before": 1,
                "variant_parent_children_after": 4,
                "created_material_property": f"Leaves:Type:{slot}:Material",
                "created_mesh_property": f"Leaves:Type:{slot}:Mesh",
                "target_material_id": 1,
                "target_mesh_id": mesh_id,
            })
            bindings.append(binding)

        removed = speedtree.remove_created_generator_variant_slots(
            root,
            bindings,
        )

        state = speedtree.generator_variant_parent_state(
            generator,
            "Leaves:Type",
        )
        self.assertEqual(state["child_count"], 1)
        self.assertEqual(
            [item["slot_prefix"] for item in removed],
            ["Leaves:Type:1", "Leaves:Type:2"],
        )
        self.assertEqual(
            {item["mode"] for item in removed},
            {"removed_created_variant_slot"},
        )

    def test_scope_cleanup_removes_complete_created_interval_after_owned_pair_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 4, "M_source", [1, 2, 3, 4])
            managed = add_material(
                assets,
                8,
                "M_leaf_test",
                [10, 11, 12, 13],
            )
            for mesh_id in (1, 2, 3, 4, 10, 11, 12, 13):
                add_mesh(assets, mesh_id)
            leaf = add_variant_generator(
                root,
                "Leaf Mesh",
                "Leaf 4",
                [(8, 10), (8, 12), (8, 13)],
            )
            frond = add_variant_generator(
                root,
                "Frond",
                "Frond 2",
                [(8, 11)],
            )
            manifest = {
                "export_scope_id": "scope-current",
                "blend_file": str(Path(folder) / "atlas.blend"),
                "source_collection": "Atlas_Cluster_Cards",
                "spm": str(target),
                "mesh_ids": [10, 11, 12, 13],
                "meshes": [],
            }
            speedtree.tag_spm_asset(managed, manifest, "material")
            for mesh_id in (10, 11, 12, 13):
                speedtree.tag_spm_asset(
                    assets.find(f"Mesh[@ID='{mesh_id}']"),
                    manifest,
                    "mesh",
                )

            bindings = [{
                "generator_index": 0,
                "generator_name": "Leaf 4",
                "generator_guid": speedtree.generator_guid(leaf),
                "generator_type": "Leaf Mesh",
                "slot_prefix": "Leaves:Type:0",
                "source_material_id": 4,
                "source_mesh_id": 1,
                "target_material_id": 8,
                "target_mesh_id": 10,
                "leaf_ordinal": 1,
                "created_slot": False,
            }]
            for slot, ordinal, source_mesh_id, target_mesh_id in (
                (1, 2, 2, 11),
                (2, 3, 3, 12),
            ):
                prefix = f"Leaves:Type:{slot}"
                bindings.append({
                    "generator_index": 0,
                    "generator_name": "Leaf 4",
                    "generator_guid": speedtree.generator_guid(leaf),
                    "generator_type": "Leaf Mesh",
                    "slot_prefix": prefix,
                    "source_material_id": 4,
                    "source_mesh_id": source_mesh_id,
                    "target_material_id": 8,
                    "target_mesh_id": target_mesh_id,
                    "leaf_ordinal": ordinal,
                    "created_slot": True,
                    "variant_parent_property": "Leaves:Type",
                    "variant_parent_children_before": 1,
                    "variant_parent_children_after": 3,
                    "created_material_property": f"{prefix}:Material",
                    "created_mesh_property": f"{prefix}:Mesh",
                    "created_property_names": [
                        f"{prefix}:Material",
                        f"{prefix}:Mesh",
                    ],
                })
            bindings.append({
                "generator_index": 1,
                "generator_name": "Frond 2",
                "generator_guid": speedtree.generator_guid(frond),
                "generator_type": "Frond",
                "slot_prefix": "Material:Frond:0",
                "source_material_id": 4,
                "source_mesh_id": 4,
                "target_material_id": 8,
                "target_mesh_id": 13,
                "leaf_ordinal": 4,
                "created_slot": False,
            })
            manifest["generator_connection"] = {"bindings": bindings}
            write_spm(target, root)

            result = speedtree.remove_atlas_scope_assets_from_spm(
                target,
                [manifest],
            )

            cleaned = speedtree.read_spm_xml(target)
            cleaned_leaf = next(
                generator
                for generator in cleaned.iter("Generator")
                if generator.findtext("Name") == "Leaf 4"
            )
            self.assertEqual(
                speedtree.generator_variant_parent_state(
                    cleaned_leaf,
                    "Leaves:Type",
                )["child_count"],
                1,
            )
            self.assertEqual(
                generator_values(target),
                {
                    ("Leaf 4", "Leaves:Type:0"): (4, 1),
                    ("Frond 2", "Material:Frond:0"): (-1, -10),
                },
            )
            cleaned_assets = cleaned.find("Assets")
            self.assertIsNone(cleaned_assets.find("Material_v8[@ID='8']"))
            for mesh_id in (10, 11, 12, 13):
                self.assertIsNone(cleaned_assets.find(f"Mesh[@ID='{mesh_id}']"))
            self.assertEqual(
                sum(
                    item["mode"] == "removed_created_variant_slot"
                    for item in result["restored_generator_slots"]
                ),
                2,
            )
            second = speedtree.remove_atlas_scope_assets_from_spm(
                target,
                [manifest],
            )
            self.assertFalse(second["changed"])

    def test_normalize_reindexes_created_slots_by_unique_target_pair(self):
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 1, "M_cluster", [106, 108, 109])
        for mesh_id in (106, 108, 109):
            add_mesh(assets, mesh_id)
        generator = add_variant_generator(
            root,
            "Leaf Mesh",
            "Leaf 4",
            [(1, 106), (1, 108), (1, 109)],
        )
        bindings = []
        for slot, ordinal, mesh_id in (
            (1, 2, 107),
            (2, 3, 108),
            (3, 4, 109),
        ):
            binding = self._binding(generator, slot, ordinal, created=True)
            binding.update({
                "variant_parent_property": "Leaves:Type",
                "variant_parent_children_before": 1,
                "variant_parent_children_after": 4,
                "created_material_property": f"Leaves:Type:{slot}:Material",
                "created_mesh_property": f"Leaves:Type:{slot}:Mesh",
                "created_property_names": [
                    f"Leaves:Type:{slot}:Material",
                    f"Leaves:Type:{slot}:Mesh",
                ],
                "target_material_id": 1,
                "target_mesh_id": mesh_id,
            })
            bindings.append(binding)

        normalized = speedtree.normalize_generator_bindings(
            root,
            bindings,
            allow_missing=True,
        )

        self.assertEqual(
            [item["target_mesh_id"] for item in normalized],
            [108, 109],
        )
        self.assertEqual(
            [item["slot_prefix"] for item in normalized],
            ["Leaves:Type:1", "Leaves:Type:2"],
        )
        self.assertEqual(
            normalized[0]["created_material_property"],
            "Leaves:Type:1:Material",
        )
        self.assertEqual(
            normalized[1]["created_mesh_property"],
            "Leaves:Type:2:Mesh",
        )

    def test_deleted_ordinal_is_retired_when_target_id_is_reused(self):
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 4, "M_source", [1, 2])
        add_material(assets, 8, "M_generated", [10, 11])
        for mesh_id in (1, 2, 10, 11):
            add_mesh(assets, mesh_id)
        generator = add_variant_generator(
            root,
            "Leaf Mesh",
            "Leaf 4",
            [(4, 1), (8, 11)],
        )
        binding = self._binding(generator, 1, 2)
        binding.update({
            "source_material_id": 4,
            "source_mesh_id": 2,
            "target_material_id": 8,
            "target_mesh_id": 11,
        })

        result = speedtree.retire_deleted_generator_bindings(
            root,
            [binding],
            {
                1: {"target_material_id": 8, "target_mesh_id": 10},
                3: {"target_material_id": 8, "target_mesh_id": 11},
            },
            {4: {"material": assets.find("Material_v8[@ID='4']"), "mesh_ids": [1, 2]}},
        )

        self.assertEqual(
            [item["mode"] for item in result["retired_bindings"]],
            ["restored_original_binding"],
        )
        self.assertEqual(
            speedtree._generator_slot_pair(generator, "Leaves:Type:1"),
            (4, 2),
        )

    def test_active_ordinal_survives_owned_pair_id_shift(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            manifest = self._managed_manifest(target, folder / "atlas.blend")
            manifest["speedtree_material_groups"][0]["mesh_ids"] = [
                10, 11, 12, 13,
            ]
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 4, "M_source", [1, 2, 3, 4])
            generated = add_material(
                assets,
                8,
                "M_leaf_test",
                [10, 11, 12, 13],
            )
            speedtree.tag_spm_asset(generated, manifest, "material")
            for mesh_id in (1, 2, 3, 4):
                add_mesh(assets, mesh_id)
            for mesh_id in (10, 11, 12, 13):
                mesh = add_mesh(assets, mesh_id)
                speedtree.tag_spm_asset(mesh, manifest, "mesh")
            generator = add_variant_generator(
                root,
                "Leaf Mesh",
                "Leaf",
                [(8, 10), (8, 12), (8, 13)],
            )
            bindings = []
            for slot, ordinal in ((1, 2), (2, 3), (3, 4)):
                binding = self._binding(
                    generator,
                    slot,
                    ordinal,
                    created=True,
                )
                binding.update({
                    "variant_parent_property": "Leaves:Type",
                    "variant_parent_children_before": 1,
                    "variant_parent_children_after": 4,
                })
                bindings.append(binding)

            result = speedtree.retire_deleted_generator_bindings(
                root,
                bindings,
                {
                    1: {"target_material_id": 8, "target_mesh_id": 10},
                    3: {"target_material_id": 8, "target_mesh_id": 11},
                    4: {"target_material_id": 8, "target_mesh_id": 12},
                },
                {
                    4: {
                        "material": assets.find("Material_v8[@ID='4']"),
                        "mesh_ids": [1, 2, 3, 4],
                    }
                },
                ownership_manifest=manifest,
                spm_path=target,
            )

            self.assertEqual(
                [item["target_mesh_id"] for item in result["active_bindings"]],
                [12, 13],
            )
            self.assertEqual(
                speedtree._generator_slot_pair(generator, "Leaves:Type:2"),
                (8, 13),
            )

    def test_previous_ordinal_wins_when_old_target_id_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(
                assets,
                1,
                "M_cluster",
                [106, 107, 108],
            )
            for mesh_id in (2, 3, 4, 5, 106, 107, 108, 109):
                add_mesh(assets, mesh_id)
            generator = add_variant_generator(
                root,
                "Leaf Mesh",
                "Leaf 4",
                [(1, 106), (1, 108), (1, 109)],
            )
            write_spm(target, root)
            previous_bindings = []
            for slot, ordinal, source_mesh_id, target_mesh_id in (
                (1, 3, 4, 108),
                (2, 4, 5, 109),
            ):
                previous_bindings.append({
                    "generator_index": 0,
                    "generator_name": "Leaf 4",
                    "generator_guid": speedtree.generator_guid(generator),
                    "generator_type": "Leaf Mesh",
                    "slot_prefix": f"Leaves:Type:{slot}",
                    "source_material_id": 1,
                    "source_material_name": "M_cluster",
                    "source_mesh_id": source_mesh_id,
                    "target_material_id": 1,
                    "target_mesh_id": target_mesh_id,
                    "leaf_ordinal": ordinal,
                    "created_slot": False,
                })
            groups = [{
                "material": "M_cluster",
                "material_id": 1,
                "mesh_ids": [106, 107, 108],
                "meshes": [
                    {
                        "source_object": f"cluster_{ordinal}",
                        "source_ordinal": ordinal,
                    }
                    for ordinal in (1, 3, 4)
                ],
            }]

            result = speedtree.connect_atlas_generators_in_spm(
                target,
                ["M_cluster"],
                groups,
                [1],
                previous_bindings=previous_bindings,
                source_mesh_ids_by_name={"M_cluster": [2, 3, 4, 5]},
                generator_variant_policy="ensure_all_material_cutouts",
            )

            self.assertEqual(result["created_slot_pairs"], 0)
            self.assertEqual(
                generator_values(target),
                {
                    ("Leaf 4", "Leaves:Type:0"): (1, 106),
                    ("Leaf 4", "Leaves:Type:1"): (1, 107),
                    ("Leaf 4", "Leaves:Type:2"): (1, 108),
                },
            )

    def test_completely_empty_collection_publishes_idempotent_target_tombstone(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            blend = folder / "atlas.blend"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 4, "M_source", [1])
            add_mesh(assets, 1)
            previous = self._managed_manifest(target, blend)
            previous["material_groups"][0]["meshes"] = [
                {"source_object": "leaf_01", "asset": str(folder / "meshes" / "leaf_01.fbx")}
            ]
            previous["speedtree_material_groups"][0]["mesh_ids"] = [10]
            managed_material = add_material(assets, 8, "M_leaf_test", [10])
            managed_mesh = add_mesh(assets, 10)
            speedtree.tag_spm_asset(managed_material, previous, "material")
            speedtree.tag_spm_asset(managed_mesh, previous, "mesh")
            generator = add_variant_generator(
                root,
                "Leaf Mesh",
                "Leaf",
                [(8, 10)],
            )
            previous["generator_connection"] = {
                "bindings": [self._binding(generator, 0, 1)]
            }
            write_spm(target, root)
            speedtree.write_scope_manifest(folder, previous, target)
            per_target = speedtree.target_manifest_path(target)
            per_target.parent.mkdir(parents=True)
            per_target.write_text(json.dumps(previous), encoding="utf-8")
            tombstone = {
                "source_collection": "Atlas_Cluster_Cards",
                "export_scope_id": "scope-current",
                "blend_file": str(blend),
                "material_groups": [],
                "meshes": [],
                "collection_tombstone": {
                    "state": "empty",
                    "reason": "no_live_mesh_objects",
                },
            }
            original_export = speedtree.export_speedtree_assets

            def fake_export(_props, export_dir, *_args, **_kwargs):
                manifest_path = Path(export_dir) / "speedtree_import_manifest.json"
                manifest_path.write_text(json.dumps(tombstone), encoding="utf-8")
                return manifest_path, Path(export_dir) / "README.md", []

            props = types.SimpleNamespace(
                collection_name="Atlas_Cluster_Cards",
                speedtree_atlas_asset_name="M_leaf_test",
                speedtree_canonical_texture_manifest_path="",
            )
            speedtree.export_speedtree_assets = fake_export
            try:
                first = speedtree.export_or_update_speedtree_spm_path(
                    props,
                    target,
                    source_material_names=["M_source"],
                    adopt_source_material=True,
                )
                first_spm = target.read_bytes()
                first_manifest = per_target.read_bytes()
                second = speedtree.export_or_update_speedtree_spm_path(
                    props,
                    target,
                    source_material_names=["M_source"],
                    adopt_source_material=True,
                )
            finally:
                speedtree.export_speedtree_assets = original_export

            self.assertEqual(first[3], "tombstoned")
            self.assertEqual(second[3], "tombstoned")
            self.assertEqual(target.read_bytes(), first_spm)
            self.assertEqual(per_target.read_bytes(), first_manifest)
            final_root = speedtree.read_spm_xml(target)
            final_assets = final_root.find("Assets")
            self.assertIsNone(final_assets.find("Material_v8[@ID='8']"))
            self.assertIsNone(final_assets.find("Mesh[@ID='10']"))
            self.assertEqual(
                generator_values(target)[("Leaf", "Leaves:Type:0")],
                (4, 1),
            )
            final_manifest = json.loads(per_target.read_text(encoding="utf-8"))
            self.assertEqual(final_manifest["mesh_ids"], [])
            self.assertEqual(final_manifest["speedtree_material_groups"], [])

    def test_last_group_output_without_binding_provenance_is_detached(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            blend = folder / "atlas.blend"
            manifest = self._managed_manifest(target, blend)
            manifest["speedtree_material_groups"][0]["mesh_ids"] = [10]
            manifest["material_groups"][0]["meshes"] = [
                {"source_object": "leaf_01"}
            ]
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            material = add_material(assets, 8, "M_leaf_test", [10])
            mesh = add_mesh(assets, 10)
            speedtree.tag_spm_asset(material, manifest, "material")
            speedtree.tag_spm_asset(mesh, manifest, "mesh")
            add_variant_generator(root, "Leaf Mesh", "Leaf", [(8, 10)])

            result = speedtree.retire_deleted_generator_bindings(
                root,
                [],
                {},
                {},
                ownership_manifest=manifest,
                spm_path=target,
            )

            pair = speedtree.spm_generator_property_pairs(root)[0]
            self.assertEqual(pair["material_property"].findtext("Value"), "-1")
            self.assertEqual(pair["mesh_property"].findtext("Value"), "-10")
            self.assertEqual(
                result["retired_bindings"][0]["mode"],
                "detached_unassigned_missing_original_binding",
            )

    def test_partial_legacy_deletion_detaches_tagged_mesh_removed_from_cutout_list(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            blend = folder / "atlas.blend"
            manifest = self._managed_manifest(target, blend)
            manifest["speedtree_material_groups"][0]["mesh_ids"] = [10, 11]
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            material = add_material(assets, 8, "M_leaf_test", [10])
            current_mesh = add_mesh(assets, 10)
            deleted_mesh = add_mesh(assets, 11)
            speedtree.tag_spm_asset(material, manifest, "material")
            speedtree.tag_spm_asset(current_mesh, manifest, "mesh")
            speedtree.tag_spm_asset(deleted_mesh, manifest, "mesh")
            add_variant_generator(
                root,
                "Leaf Mesh",
                "Leaf",
                [(8, 10), (8, 11)],
            )

            result = speedtree.retire_deleted_generator_bindings(
                root,
                [],
                {1: {"target_material_id": 8, "target_mesh_id": 10}},
                {},
                ownership_manifest=manifest,
                spm_path=target,
            )

            pairs = speedtree.spm_generator_property_pairs(root)
            self.assertEqual(
                (
                    pairs[1]["material_property"].findtext("Value"),
                    pairs[1]["mesh_property"].findtext("Value"),
                ),
                ("-1", "-10"),
            )
            self.assertEqual(len(result["retired_bindings"]), 1)

    def test_same_source_legacy_scope_is_selected_and_retired_idempotently(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            blend = folder / "atlas.blend"
            current = self._managed_manifest(target, blend)
            current["material_groups"][0]["meshes"] = [
                {"source_object": "leaf_02"}
            ]
            previous = self._managed_manifest(target, blend, "M_leaf_test")
            previous["material_groups"][0]["meshes"] = [
                {"source_object": "leaf_01"},
                {"source_object": "leaf_02"},
            ]
            scope_path = speedtree.write_scope_manifest(folder, previous, target)

            selected, diagnostics = speedtree.superseded_scope_manifests_for_update(
                target,
                current,
            )

            self.assertEqual([item["export_scope_id"] for item in selected], ["M_leaf_test"])
            self.assertEqual(diagnostics, [])
            first = speedtree.retire_scope_manifest_records(selected, current)
            first_bytes = scope_path.read_bytes()
            second = speedtree.retire_scope_manifest_records(selected, current)
            self.assertEqual(scope_path.read_bytes(), first_bytes)
            self.assertEqual(first, second)
            payload = json.loads(scope_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["atlas_scope_lifecycle"]["state"], "retired")
            self.assertEqual(payload["speedtree_material_groups"], [])

    def test_retirement_merges_ownership_rewrite_without_scope_resurrection(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            blend = folder / "atlas.blend"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 9, "M_successor", [20])
            add_mesh(assets, 20)
            generator = add_variant_generator(
                root,
                "Leaf Mesh",
                "Leaf",
                [(9, 20)],
            )
            write_spm(target, root)
            row = self._binding(generator, 0, 1)
            row.update({
                "target_material_id": 9,
                "target_material_name": "M_successor",
                "target_mesh_id": 20,
            })

            previous = self._managed_manifest(
                target,
                blend,
                "scope-previous",
            )
            previous["generator_connection"] = {
                "requested": True,
                "complete": True,
                "bindings": [copy.deepcopy(row)],
            }
            previous = speedtree.manifest_with_binding_contracts(
                previous,
                [row],
            )
            previous_path = speedtree.write_scope_manifest(
                folder,
                previous,
                target,
            )
            selected = copy.deepcopy(previous)
            selected["_scope_manifest_path"] = str(previous_path)

            relinquished = {
                **copy.deepcopy(row),
                "relinquished_reason": "superseded_same_source_generation",
            }
            prepared = speedtree.manifest_with_binding_contracts(
                previous,
                [],
                relinquished_rows=[relinquished],
            )
            prepared_rewrites = [{
                "path": previous_path,
                "payload": prepared,
            }]
            successor = self._managed_manifest(
                target,
                blend,
                "scope-current",
            )
            successor["generator_connection"] = {
                "requested": True,
                "complete": True,
                "bindings": [copy.deepcopy(row)],
            }
            successor = speedtree.manifest_with_binding_contracts(
                successor,
                [row],
            )
            speedtree.write_scope_manifest(folder, successor, target)

            speedtree.retire_scope_manifest_records(
                [selected],
                successor,
                prepared_rewrites=prepared_rewrites,
            )
            retired_keys = speedtree.retired_scope_receipt_path_keys(
                [selected]
            )
            for rewrite in prepared_rewrites:
                if speedtree._receipt_path_key(
                    rewrite["path"]
                ) in retired_keys:
                    continue
                speedtree._write_json_if_changed(
                    rewrite["path"],
                    rewrite["payload"],
                )

            retired = json.loads(
                previous_path.read_text(encoding="utf-8")
            )
            connection = retired["generator_connection"]
            self.assertEqual(
                retired["atlas_scope_lifecycle"]["state"],
                "retired",
            )
            self.assertEqual(connection["bindings"], [])
            self.assertEqual(len(connection["authored_bindings"]), 1)
            self.assertEqual(
                connection["relinquished_bindings"][0][
                    "relinquished_reason"
                ],
                "superseded_same_source_generation",
            )
            self.assertEqual(
                retired["generator_binding_ownership"]["binding_count"],
                0,
            )
            speedtree.validate_target_generator_ownership_receipts(target)

            resurrected = copy.deepcopy(previous)
            resurrected.pop("atlas_scope_lifecycle", None)
            speedtree._write_json_if_changed(previous_path, resurrected)
            with self.assertRaisesRegex(
                RuntimeError,
                "receipts still overlap or differ",
            ):
                speedtree.validate_target_generator_ownership_receipts(target)

    def test_ambiguous_legacy_scope_is_preserved_with_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            blend = folder / "atlas.blend"
            current = self._managed_manifest(target, blend)
            previous = self._managed_manifest(target, blend, "legacy-scope")
            previous["material_groups"][0]["meshes"] = [{}]
            scope_path = speedtree.write_scope_manifest(folder, previous, target)

            selected, diagnostics = speedtree.superseded_scope_manifests_for_update(
                target,
                current,
            )

            self.assertEqual(selected, [])
            self.assertEqual(diagnostics[0]["scope"], "legacy-scope")
            self.assertEqual(diagnostics[0]["reason"], "asset_lineage_unproven")
            self.assertNotIn(
                "atlas_scope_lifecycle",
                json.loads(scope_path.read_text(encoding="utf-8")),
            )


class GeneratorSlotOwnershipIntegrationTests(unittest.TestCase):
    def _binding(
        self,
        generator,
        slot_index,
        material_id,
        mesh_id,
        *,
        created=False,
    ):
        slot_prefix = f"Leaves:Type:{slot_index}"
        row = {
            "state": "changed",
            "generator_index": 0,
            "generator_name": str(generator.findtext("Name") or ""),
            "generator_guid": speedtree.generator_guid(generator),
            "generator_type": "Leaf Mesh",
            "slot_prefix": slot_prefix,
            "source_material_id": 4,
            "source_material_name": "M_source",
            "source_mesh_id": slot_index + 1,
            "source_object": f"leaf_{slot_index + 1:02d}",
            "leaf_ordinal": slot_index + 1,
            "target_material_id": material_id,
            "target_material_name": f"M_provider_{material_id}",
            "target_mesh_id": mesh_id,
            "created_slot": created,
        }
        if created:
            row.update({
                "variant_parent_property": "Leaves:Type",
                "variant_parent_children_before": 1,
                "variant_parent_children_after": 4,
                "created_material_property": f"{slot_prefix}:Material",
                "created_mesh_property": f"{slot_prefix}:Mesh",
                "created_property_names": [
                    f"{slot_prefix}:Material",
                    f"{slot_prefix}:Mesh",
                ],
            })
        return row

    def _manifest(self, target, name, scope, rows):
        return {
            "spm": str(target),
            "blend_file": str(target.parent / f"{name}.blend"),
            "source_collection": name,
            "export_scope_id": scope,
            "generator_connection": {
                "requested": True,
                "complete": True,
                "bindings": copy.deepcopy(rows),
            },
        }

    def _seal_delivery_scope(
        self,
        target,
        payload,
        authored_rows,
        *,
        slot_identities=None,
        target_spm=None,
        provider_blend=None,
        provider_scope_id=None,
    ):
        authored_rows = copy.deepcopy(authored_rows)
        identities = slot_identities or [
            list(delivery_scope.canonical_slot_identity(row))
            for row in authored_rows
        ]
        material_ids = {
            row["target_material_id"] for row in authored_rows
        }
        self.assertEqual(len(material_ids), 1)
        intent = {
            "kind": delivery_scope.INTENT_KIND,
            "schema_version": delivery_scope.SCHEMA_VERSION,
            "authority": {
                "kind": "test_recipe",
                "id": "explicit-successor-delivery",
                "provenance": {"fixture": "ownership-handoff"},
            },
            "target": {
                "spm": str(target_spm or target),
                "provider_blend": str(
                    provider_blend or payload["blend_file"]
                ),
                "provider_scope_id": str(
                    provider_scope_id or payload["export_scope_id"]
                ),
                "material_id": next(iter(material_ids)),
            },
            "authored_slots": [
                {
                    "slot_identity": list(identity),
                    "target_material_id": row["target_material_id"],
                    "target_mesh_id": row["target_mesh_id"],
                }
                for identity, row in zip(identities, authored_rows)
            ],
            "required_live_slot_identities": [
                list(identity) for identity in identities
            ],
            "continuity_only_slots": [],
            "runtime_inactive_policy": (
                delivery_scope.RUNTIME_INACTIVE_POLICY
            ),
        }
        intent["intent_sha256"] = delivery_scope.canonical_sha256(intent)
        connection = payload["generator_connection"]
        connection["authored_bindings"] = copy.deepcopy(authored_rows)
        connection["delivery_scope"] = (
            delivery_scope.build_resolved_delivery_scope(
                intent,
                authored_rows,
                speedtree.spm_text_sha256(target),
            )
        )
        return intent

    def test_staging_gate_ignores_only_the_current_providers_own_cleanup(self):
        provider_a = ("a.blend", "cards-a", "scope-a")
        provider_b = ("b.blend", "cards-b", "scope-b")
        plan = {
            "provider_updates": {
                provider_a: {"relinquished_bindings": [{"slot": "Type0"}]},
                provider_b: {"relinquished_bindings": []},
            }
        }

        self.assertFalse(
            speedtree.ownership_reconciliation_has_relinquishments(
                plan,
                excluding_provider_key=provider_a,
            )
        )
        self.assertTrue(
            speedtree.ownership_reconciliation_has_relinquishments(
                plan,
                excluding_provider_key=provider_b,
            )
        )

    def test_live_ownership_includes_only_valid_material_default_sentinel(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 8, "M_frond", [-10])
            add_variant_generator(
                root,
                "Frond",
                "Frond sentinel",
                [(8, -10), (8, -9), (8, 0)],
            )
            write_spm(target, root)

            rows = speedtree.live_generator_ownership_bindings(target)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["slot_prefix"], "Material:Frond:0")
            self.assertEqual(rows[0]["target_mesh_id"], -10)
            binding_row = {
                **rows[0],
                "state": "already_connected",
                "source_material_id": 8,
                "source_material_name": "M_frond",
                "source_mesh_id": -10,
                "created_slot": False,
            }
            provider = self._manifest(
                target,
                "provider_sentinel",
                "scope-sentinel",
                [binding_row],
            )
            speedtree.write_scope_manifest(folder, provider, target)
            preflight = (
                speedtree.prepare_target_generator_ownership_reconciliation(
                    target
                )
            )
            finalized = (
                speedtree.finalize_target_generator_ownership_reconciliation(
                    target,
                    provider,
                    provider["generator_connection"],
                    preflight,
                )
            )
            self.assertEqual(
                finalized["manifest"]["generator_binding_ownership"][
                    "bindings"
                ][0]["target_mesh_id"],
                -10,
            )

    def test_live_split_rewrites_prior_scope_and_preserves_creator_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            for material_id, name, mesh_ids in (
                (4, "M_source", [1, 2, 3, 4]),
                (8, "M_provider_a", [89, 90, 91, 92]),
                (10, "M_provider_b", [93, 94]),
            ):
                add_material(assets, material_id, name, mesh_ids)
            for mesh_id in (1, 2, 3, 4, 89, 90, 91, 92, 93, 94):
                add_mesh(assets, mesh_id)
            generator = add_variant_generator(
                root,
                "Leaf Mesh",
                "Leaf 11",
                [(8, 89), (10, 93), (10, 94), (8, 92)],
            )
            write_spm(target, root)

            provider_a_rows = [
                self._binding(generator, 0, 8, 89),
                self._binding(generator, 1, 8, 90, created=True),
                self._binding(generator, 2, 8, 91, created=True),
                self._binding(generator, 3, 8, 92, created=True),
            ]
            provider_b_rows = [
                self._binding(generator, 1, 10, 93),
                self._binding(generator, 2, 10, 94),
            ]
            provider_a = self._manifest(
                target, "provider_a", "scope-a", provider_a_rows
            )
            provider_b = self._manifest(
                target, "provider_b", "scope-b", provider_b_rows
            )
            provider_a_path = speedtree.write_scope_manifest(
                folder, provider_a, target
            )
            speedtree.write_scope_manifest(folder, provider_b, target)

            preflight = (
                speedtree.prepare_target_generator_ownership_reconciliation(
                    target
                )
            )
            result = (
                speedtree.finalize_target_generator_ownership_reconciliation(
                    target,
                    provider_b,
                    provider_b["generator_connection"],
                    preflight,
                )
            )

            self.assertEqual(
                result["manifest"]["generator_binding_ownership"][
                    "binding_count"
                ],
                2,
            )
            rewrite = next(
                row
                for row in result["receipt_rewrites"]
                if row["path"] == provider_a_path.absolute()
            )["payload"]
            self.assertEqual(
                [
                    row["slot_prefix"]
                    for row in rewrite["generator_connection"]["bindings"]
                ],
                ["Leaves:Type:0", "Leaves:Type:3"],
            )
            self.assertEqual(
                len(
                    rewrite["generator_connection"]["authored_bindings"]
                ),
                4,
            )
            self.assertEqual(
                {
                    row["slot_prefix"]
                    for row in rewrite[
                        "generator_slot_creation_provenance"
                    ]["slots"]
                },
                {
                    "Leaves:Type:1",
                    "Leaves:Type:2",
                    "Leaves:Type:3",
                },
            )

    def test_live_split_receipts_commit_inside_one_staged_transaction(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            for material_id, name, mesh_ids in (
                (4, "M_source", [1, 2, 3, 4]),
                (8, "M_provider_a", [89, 90, 91, 92]),
                (10, "M_provider_b", [93, 94]),
            ):
                add_material(assets, material_id, name, mesh_ids)
            for mesh_id in (1, 2, 3, 4, 89, 90, 91, 92, 93, 94):
                mesh = add_mesh(assets, mesh_id)
                ET.SubElement(mesh, "Embedded").text = "true"
            generator = add_variant_generator(
                root,
                "Leaf Mesh",
                "Leaf 11",
                [(8, 89), (10, 93), (10, 94), (8, 92)],
            )
            write_spm(target, root)

            provider_a = self._manifest(
                target,
                "provider_a",
                "scope-a",
                [
                    self._binding(generator, 0, 8, 89),
                    self._binding(generator, 1, 8, 90, created=True),
                    self._binding(generator, 2, 8, 91, created=True),
                    self._binding(generator, 3, 8, 92, created=True),
                ],
            )
            provider_b = self._manifest(
                target,
                "provider_b",
                "scope-b",
                [
                    self._binding(generator, 1, 10, 93),
                    self._binding(generator, 2, 10, 94),
                ],
            )
            provider_a_path = speedtree.write_scope_manifest(
                folder, provider_a, target
            )
            provider_b_path = speedtree.write_scope_manifest(
                folder, provider_b, target
            )

            def build(staged_target, _production_target):
                preflight = (
                    speedtree.prepare_target_generator_ownership_reconciliation(
                        staged_target
                    )
                )
                staged_records = (
                    speedtree.target_scope_generator_ownership_records(
                        staged_target
                    )
                )
                staged_b = next(
                    record["payload"]
                    for record in staged_records
                    if record["payload"]["export_scope_id"] == "scope-b"
                )
                result = (
                    speedtree.finalize_target_generator_ownership_reconciliation(
                        staged_target,
                        staged_b,
                        staged_b["generator_connection"],
                        preflight,
                    )
                )
                for rewrite in result["receipt_rewrites"]:
                    speedtree._write_json_if_changed(
                        rewrite["path"], rewrite["payload"]
                    )
                payload = result["manifest"]
                speedtree._write_json_if_changed(
                    speedtree.target_manifest_path(staged_target),
                    payload,
                )
                speedtree.write_scope_manifest(
                    staged_target.parent,
                    payload,
                    staged_target,
                )
                return result

            speedtree.execute_atomic_target_update(
                [target],
                build,
                speedtree._validate_staged_speedtree_targets,
            )

            committed_a = json.loads(
                provider_a_path.read_text(encoding="utf-8")
            )
            committed_b = json.loads(
                provider_b_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                [
                    row["slot_prefix"]
                    for row in committed_a["generator_connection"]["bindings"]
                ],
                ["Leaves:Type:0", "Leaves:Type:3"],
            )
            self.assertEqual(
                committed_a["generator_binding_ownership"]["binding_count"],
                2,
            )
            self.assertEqual(
                committed_b["generator_binding_ownership"]["binding_count"],
                2,
            )
            self.assertEqual(
                len(committed_a["generator_connection"]["authored_bindings"]),
                4,
            )

    def test_sealed_delivery_intent_authorizes_exact_staged_foreign_takeover(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 8, "M_provider_a", [89])
            add_material(assets, 10, "M_provider_b", [93])
            add_mesh(assets, 89)
            add_mesh(assets, 93)
            generator = add_variant_generator(
                root, "Leaf Mesh", "Leaf 11", [(8, 89)]
            )
            write_spm(target, root)
            provider_a_rows = [self._binding(generator, 0, 8, 89)]
            provider_a = self._manifest(
                target, "provider_a", "scope-a", provider_a_rows
            )
            self._seal_delivery_scope(
                target,
                provider_a,
                provider_a_rows,
            )
            provider_a_path = speedtree.write_scope_manifest(
                folder, provider_a, target
            )
            # A pre-issued B claim names the intended final pair but is not a
            # transfer grant from A.  It participates in preflight so this
            # covers the strongest currently available staged handoff proof.
            provider_b_rows = [self._binding(generator, 0, 10, 93)]
            provider_b = self._manifest(
                target, "provider_b", "scope-b", provider_b_rows
            )
            speedtree.write_scope_manifest(folder, provider_b, target)
            preflight = (
                speedtree.prepare_target_generator_ownership_reconciliation(
                    target
                )
            )

            changed = speedtree.read_spm_xml(target)
            pair = speedtree.spm_generator_property_pairs(changed)[0]
            pair["material_property"].find("Value").text = "10"
            pair["mesh_property"].find("Value").text = "93"
            speedtree.write_spm_xml(target, changed)
            slot_identity = list(
                delivery_scope.canonical_slot_identity(provider_b_rows[0])
            )
            intent = {
                "kind": delivery_scope.INTENT_KIND,
                "schema_version": delivery_scope.SCHEMA_VERSION,
                "authority": {
                    "kind": "test_recipe",
                    "id": "foreign-handoff-attempt",
                    "provenance": {"fixture": "self-declared-authority"},
                },
                "target": {
                    "spm": str(target),
                    "provider_blend": provider_b["blend_file"],
                    "provider_scope_id": provider_b["export_scope_id"],
                    "material_id": 10,
                },
                "authored_slots": [{
                    "slot_identity": slot_identity,
                    "target_material_id": 10,
                    "target_mesh_id": 93,
                }],
                "required_live_slot_identities": [slot_identity],
                "continuity_only_slots": [],
                "runtime_inactive_policy": (
                    delivery_scope.RUNTIME_INACTIVE_POLICY
                ),
            }
            intent["intent_sha256"] = delivery_scope.canonical_sha256(
                intent
            )
            postwrite_sha256 = speedtree.spm_text_sha256(target)
            provider_b["generator_connection"]["delivery_scope"] = (
                delivery_scope.build_resolved_delivery_scope(
                    intent,
                    provider_b_rows,
                    postwrite_sha256,
                )
            )
            delivery_scope.validate_resolved_delivery_scope(
                provider_b["generator_connection"],
                target_spm=target,
                material_id=10,
                provider_blend=provider_b["blend_file"],
                target_spm_postwrite_sha256=postwrite_sha256,
            )

            result = (
                speedtree.finalize_target_generator_ownership_reconciliation(
                    target,
                    provider_b,
                    provider_b["generator_connection"],
                    preflight,
                    contract_target_spm=target,
                    ownership_transaction_is_staged=True,
                )
            )

            predecessor = next(
                rewrite["payload"]
                for rewrite in result["receipt_rewrites"]
                if rewrite["path"] == provider_a_path.absolute()
            )
            self.assertEqual(
                predecessor["generator_connection"]["bindings"],
                [],
            )
            self.assertEqual(
                len(predecessor["generator_connection"]["authored_bindings"]),
                1,
            )
            authorization = predecessor["generator_connection"][
                "relinquished_bindings"
            ][0]["successor_authorization"]
            self.assertEqual(
                authorization["basis"],
                "sealed_prewrite_owner_plus_resolved_delivery_scope",
            )
            self.assertEqual(
                authorization["predecessor_provider"]["export_scope_id"],
                "scope-a",
            )
            self.assertEqual(
                authorization["successor_provider"]["export_scope_id"],
                "scope-b",
            )
            historical = delivery_scope.validate_resolved_delivery_scope(
                predecessor["generator_connection"],
                target_spm=target,
                material_id=8,
                provider_blend=provider_a["blend_file"],
                target_spm_postwrite_sha256=(
                    speedtree.spm_text_sha256(target)
                ),
                postwrite_validation_mode=(
                    delivery_scope.POSTWRITE_MODE_HISTORICAL_PROOF
                ),
            )
            self.assertFalse(
                historical["target_spm_postwrite_matches_current"]
            )

    def test_foreign_takeover_rejects_missing_or_drifted_successor_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            add_material(assets, 8, "M_provider_a", [89])
            add_material(assets, 10, "M_provider_b", [93, 94])
            add_mesh(assets, 89)
            add_mesh(assets, 93)
            add_mesh(assets, 94)
            generator = add_variant_generator(
                root, "Leaf Mesh", "Leaf 11", [(8, 89)]
            )
            write_spm(target, root)
            provider_a = self._manifest(
                target,
                "provider_a",
                "scope-a",
                [self._binding(generator, 0, 8, 89)],
            )
            provider_b_row = self._binding(generator, 0, 10, 93)
            provider_b = self._manifest(
                target, "provider_b", "scope-b", [provider_b_row]
            )
            speedtree.write_scope_manifest(folder, provider_a, target)
            speedtree.write_scope_manifest(folder, provider_b, target)
            preflight = (
                speedtree.prepare_target_generator_ownership_reconciliation(
                    target
                )
            )
            changed = speedtree.read_spm_xml(target)
            pair = speedtree.spm_generator_property_pairs(changed)[0]
            pair["material_property"].find("Value").text = "10"
            pair["mesh_property"].find("Value").text = "93"
            speedtree.write_spm_xml(target, changed)
            self._seal_delivery_scope(target, provider_b, [provider_b_row])

            with self.assertRaisesRegex(
                speedtree.GeneratorSlotOwnershipError,
                "atomic staged",
            ):
                speedtree.finalize_target_generator_ownership_reconciliation(
                    target,
                    provider_b,
                    provider_b["generator_connection"],
                    preflight,
                    contract_target_spm=target,
                    ownership_transaction_is_staged=False,
                )

            no_intent = copy.deepcopy(provider_b)
            no_intent["generator_connection"].pop("delivery_scope")
            with self.assertRaisesRegex(
                speedtree.GeneratorSlotOwnershipError,
                "requires a resolved delivery scope",
            ):
                speedtree.finalize_target_generator_ownership_reconciliation(
                    target,
                    no_intent,
                    no_intent["generator_connection"],
                    preflight,
                    contract_target_spm=target,
                    ownership_transaction_is_staged=True,
                )

            named = copy.deepcopy(provider_b)
            named_authored = copy.deepcopy(provider_b_row)
            named_authored["generator_guid"] = ""
            named_identity = [[
                "named",
                named_authored["generator_type"],
                named_authored["generator_name"],
                named_authored["slot_prefix"],
            ]]
            self._seal_delivery_scope(
                target,
                named,
                [named_authored],
                slot_identities=named_identity,
            )
            with self.assertRaisesRegex(
                speedtree.GeneratorSlotOwnershipError,
                "requires exact GUID slot identities",
            ):
                speedtree.finalize_target_generator_ownership_reconciliation(
                    target,
                    named,
                    named["generator_connection"],
                    preflight,
                    contract_target_spm=target,
                    ownership_transaction_is_staged=True,
                )

            pair_drift = copy.deepcopy(provider_b)
            drifted_authored = {
                **copy.deepcopy(provider_b_row),
                "target_mesh_id": 94,
            }
            self._seal_delivery_scope(
                target,
                pair_drift,
                [drifted_authored],
            )
            with self.assertRaisesRegex(
                speedtree.GeneratorSlotOwnershipError,
                "does not match.*final delivery",
            ):
                speedtree.finalize_target_generator_ownership_reconciliation(
                    target,
                    pair_drift,
                    pair_drift["generator_connection"],
                    preflight,
                    contract_target_spm=target,
                    ownership_transaction_is_staged=True,
                )

            provider_drift = copy.deepcopy(provider_b)
            provider_drift["blend_file"] = str(folder / "provider_c.blend")
            with self.assertRaisesRegex(
                speedtree.GeneratorSlotOwnershipError,
                "delivery seal is invalid",
            ):
                speedtree.finalize_target_generator_ownership_reconciliation(
                    target,
                    provider_drift,
                    provider_drift["generator_connection"],
                    preflight,
                    contract_target_spm=target,
                    ownership_transaction_is_staged=True,
                )

            target_drift = copy.deepcopy(provider_b)
            self._seal_delivery_scope(
                target,
                target_drift,
                [provider_b_row],
                target_spm=folder / "foreign.spm",
            )
            with self.assertRaisesRegex(
                speedtree.GeneratorSlotOwnershipError,
                "delivery seal is invalid",
            ):
                speedtree.finalize_target_generator_ownership_reconciliation(
                    target,
                    target_drift,
                    target_drift["generator_connection"],
                    preflight,
                    contract_target_spm=target,
                    ownership_transaction_is_staged=True,
                )

            preflight_drift = copy.deepcopy(preflight)
            preflight_drift["fingerprint"] = "0" * 64
            with self.assertRaisesRegex(
                speedtree.GeneratorSlotOwnershipError,
                "preflight fingerprint drifted",
            ):
                speedtree.finalize_target_generator_ownership_reconciliation(
                    target,
                    provider_b,
                    provider_b["generator_connection"],
                    preflight_drift,
                    contract_target_spm=target,
                    ownership_transaction_is_staged=True,
                )

    def test_repeated_foreign_handoffs_preserve_exact_generation_history(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "SK_tree.spm"
            root = ET.Element("SpeedTreeModel")
            assets = ET.SubElement(root, "Assets")
            providers = {
                "a": {
                    "scope": "scope-a",
                    "material_id": 8,
                    "initial_mesh_id": 80,
                    "return_mesh_id": 110,
                },
                "b": {
                    "scope": "scope-b",
                    "material_id": 9,
                    "initial_mesh_id": 90,
                },
                "c": {
                    "scope": "scope-c",
                    "material_id": 10,
                    "initial_mesh_id": 100,
                },
            }
            for name, provider in providers.items():
                mesh_ids = [provider["initial_mesh_id"]]
                if "return_mesh_id" in provider:
                    mesh_ids.append(provider["return_mesh_id"])
                add_material(
                    assets,
                    provider["material_id"],
                    f"M_provider_{name}",
                    mesh_ids,
                )
                for mesh_id in mesh_ids:
                    add_mesh(assets, mesh_id)
            generator = add_variant_generator(
                root,
                "Leaf Mesh",
                "Leaf repeated handoff",
                [(8, 80)],
            )
            for prop in generator.iter("Property"):
                name = prop.find("Name")
                if name is not None and name.text:
                    name.text = name.text.replace(
                        "Leaves:Type:0:",
                        "Leaves:Type:42:",
                    )
            write_spm(target, root)

            def binding(provider_name, mesh_id):
                provider = providers[provider_name]
                row = self._binding(
                    generator,
                    42,
                    provider["material_id"],
                    mesh_id,
                )
                row["source_mesh_id"] = 1
                row["leaf_ordinal"] = 1
                return row

            initial_a_row = binding("a", 80)
            initial_a = self._manifest(
                target,
                "provider_a",
                providers["a"]["scope"],
                [initial_a_row],
            )
            self._seal_delivery_scope(
                target,
                initial_a,
                [initial_a_row],
            )
            receipt_paths = {
                "a": speedtree.write_scope_manifest(
                    folder,
                    initial_a,
                    target,
                )
            }

            def transition(predecessor_name, successor_name, mesh_id):
                successor = providers[successor_name]
                successor_row = binding(successor_name, mesh_id)
                successor_path = receipt_paths.get(successor_name)
                if successor_path is not None and successor_path.is_file():
                    previous_payload = json.loads(
                        successor_path.read_text(encoding="utf-8")
                    )
                else:
                    previous_payload = self._manifest(
                        target,
                        f"provider_{successor_name}",
                        successor["scope"],
                        [successor_row],
                    )
                previous_connection = copy.deepcopy(
                    previous_payload.get("generator_connection") or {}
                )
                preclaim = speedtree.manifest_with_binding_contracts(
                    previous_payload,
                    [successor_row],
                )
                receipt_paths[successor_name] = (
                    speedtree.write_scope_manifest(
                        folder,
                        preclaim,
                        target,
                    )
                )
                preflight = (
                    speedtree.prepare_target_generator_ownership_reconciliation(
                        target
                    )
                )

                changed = speedtree.read_spm_xml(target)
                pair = next(
                    item
                    for item in speedtree.spm_generator_property_pairs(changed)
                    if item["slot_prefix"] == "Leaves:Type:42"
                )
                pair["material_property"].find("Value").text = str(
                    successor["material_id"]
                )
                pair["mesh_property"].find("Value").text = str(mesh_id)
                speedtree.write_spm_xml(target, changed)

                current = copy.deepcopy(preclaim)
                connection = current["generator_connection"]
                history = copy.deepcopy(
                    previous_connection.get("delivery_scope_history") or []
                )
                previous_scope = previous_connection.get("delivery_scope")
                if previous_scope is not None:
                    historical_row = {
                        "state": "historical_production_proof",
                        "authored_bindings": copy.deepcopy(
                            previous_connection.get("authored_bindings")
                            or previous_connection.get("bindings")
                            or []
                        ),
                        "delivery_scope": copy.deepcopy(previous_scope),
                    }
                    previous_hash = str(
                        (previous_scope.get("resolved") or {}).get(
                            "resolved_sha256"
                        )
                        or ""
                    )
                    known_hashes = {
                        str(
                            (
                                (row.get("delivery_scope") or {}).get(
                                    "resolved"
                                )
                                or {}
                            ).get("resolved_sha256")
                            or ""
                        )
                        for row in history
                        if isinstance(row, dict)
                    }
                    if previous_hash not in known_hashes:
                        history.append(historical_row)
                connection["bindings"] = [copy.deepcopy(successor_row)]
                connection["authored_bindings"] = [
                    copy.deepcopy(successor_row)
                ]
                if history:
                    connection["delivery_scope_history"] = history
                self._seal_delivery_scope(
                    target,
                    current,
                    [successor_row],
                )
                result = (
                    speedtree.finalize_target_generator_ownership_reconciliation(
                        target,
                        current,
                        current["generator_connection"],
                        preflight,
                        contract_target_spm=target,
                        ownership_transaction_is_staged=True,
                    )
                )
                for rewrite in result["receipt_rewrites"]:
                    speedtree._write_json_if_changed(
                        rewrite["path"],
                        rewrite["payload"],
                    )
                receipt_paths[successor_name] = (
                    speedtree.write_scope_manifest(
                        folder,
                        result["manifest"],
                        target,
                    )
                )
                predecessor_payload = json.loads(
                    receipt_paths[predecessor_name].read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    predecessor_payload["generator_connection"]["bindings"],
                    [],
                )
                authorization = predecessor_payload[
                    "generator_connection"
                ]["relinquished_bindings"][-1][
                    "successor_authorization"
                ]
                self.assertEqual(
                    authorization["predecessor_provider"][
                        "export_scope_id"
                    ],
                    providers[predecessor_name]["scope"],
                )
                self.assertEqual(
                    authorization["successor_provider"][
                        "export_scope_id"
                    ],
                    successor["scope"],
                )

            transition("a", "b", 90)
            transition("b", "c", 100)
            transition("c", "a", 110)

            current_sha256 = speedtree.spm_text_sha256(target)
            final_a = json.loads(
                receipt_paths["a"].read_text(encoding="utf-8")
            )
            final_a_connection = final_a["generator_connection"]
            self.assertEqual(
                [
                    (
                        row["target_material_id"],
                        row["target_mesh_id"],
                    )
                    for row in final_a_connection["authored_bindings"]
                ],
                [(8, 110)],
            )
            self.assertEqual(
                final_a_connection["bindings"][0]["slot_prefix"],
                "Leaves:Type:42",
            )
            self.assertEqual(
                len(final_a_connection["delivery_scope_history"]),
                1,
            )
            current_a = delivery_scope.validate_resolved_delivery_scope(
                final_a_connection,
                target_spm=target,
                material_id=8,
                provider_blend=final_a["blend_file"],
                target_spm_postwrite_sha256=current_sha256,
            )
            self.assertTrue(
                current_a["target_spm_postwrite_matches_current"]
            )

            historical_receipts = [
                (
                    final_a_connection["delivery_scope_history"][0],
                    final_a,
                    8,
                )
            ]
            for provider_name in ("b", "c"):
                payload = json.loads(
                    receipt_paths[provider_name].read_text(encoding="utf-8")
                )
                historical_receipts.append(
                    (
                        payload["generator_connection"],
                        payload,
                        providers[provider_name]["material_id"],
                    )
                )
            for connection, payload, material_id in historical_receipts:
                validated = delivery_scope.validate_resolved_delivery_scope(
                    connection,
                    target_spm=target,
                    material_id=material_id,
                    provider_blend=payload["blend_file"],
                    target_spm_postwrite_sha256=current_sha256,
                    postwrite_validation_mode=(
                        delivery_scope.POSTWRITE_MODE_HISTORICAL_PROOF
                    ),
                )
                self.assertFalse(
                    validated["target_spm_postwrite_matches_current"]
                )


class SpmWriterTests(unittest.TestCase):
    def sample_root(self):
        root = ET.Element("SpeedTreeRaw")
        assets = ET.SubElement(root, "Assets")
        material = ET.SubElement(assets, "Material_v8", {"ID": "6", "Name": "M_leaf"})
        ET.SubElement(material, "CutoutMeshID").text = "13"
        supplemental = ET.SubElement(
            material, "SupplementalCutoutMeshIDs", {"Count": "1"}
        )
        ET.SubElement(supplemental, "CutoutMesh", {"ID": "9"})
        ET.SubElement(material, "Filename").text = ""
        generator = ET.SubElement(root, "Generator", {"Type": "Leaf Mesh"})
        ET.SubElement(generator, "Name").text = "Leaf 65"
        prop = ET.SubElement(generator, "Property")
        ET.SubElement(prop, "Name").text = "Leaves:Type:0:Mesh"
        ET.SubElement(prop, "Value").text = "-10"
        return root

    def fingerprint(self, node, out=None):
        out = [] if out is None else out
        out.append((node.tag, tuple(sorted(node.attrib.items())), (node.text or "").strip()))
        for child in node:
            self.fingerprint(child, out)
        return out

    def test_repeated_saves_do_not_grow_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.spm"
            speedtree.write_spm_xml(path, self.sample_root())
            first = gzip.decompress(path.read_bytes())
            for _ in range(3):
                speedtree.write_spm_xml(path, speedtree.read_spm_xml(path))
            self.assertEqual(gzip.decompress(path.read_bytes()), first)

    def test_round_trip_preserves_every_authored_value(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.spm"
            expected = self.fingerprint(self.sample_root())
            speedtree.write_spm_xml(path, self.sample_root())
            for _ in range(3):
                speedtree.write_spm_xml(path, speedtree.read_spm_xml(path))
            self.assertEqual(self.fingerprint(speedtree.read_spm_xml(path)), expected)

    def test_layout_whitespace_is_stripped_but_authored_text_is_kept(self):
        root = ET.Element("Generator")
        root.text = "\n\t\t"
        value = ET.SubElement(root, "Value")
        value.text = " 13 "
        value.tail = "\n\t"
        speedtree.strip_spm_layout_whitespace(root)
        self.assertIsNone(root.text)
        self.assertIsNone(value.tail)
        self.assertEqual(value.text, " 13 ")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
