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
    texture_paths.atlas_texture_paths = lambda value: {}
    sys.modules[texture_paths.__name__] = texture_paths

    name = f"{package_name}.speedtree"
    spec = importlib.util.spec_from_file_location(name, PACKAGE_DIR / "speedtree.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


speedtree = load_speedtree_module()


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


class MeshAssetScaleTests(unittest.TestCase):
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
        props = types.SimpleNamespace(
            speedtree_source_materials_json=json.dumps(
                {
                    target: {
                        "source_material_names": ["M_branch"],
                        "source_material_ids": [8],
                        "generator_variant_policy": (
                            "ensure_all_material_cutouts"
                        ),
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

    def test_parsley_cutout_ordinals_connect_generator_slots(self):
        result = speedtree.connect_atlas_generators_in_spm(
            self.spm_path, ["M_leaf_parsley_02"], self.groups, [4]
        )
        self.assertTrue(result["complete"])

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

    def test_ladyfern_mesh_minus_ten_uses_first_generated_leaf(self):
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
        self.assertEqual(binding["target_mesh_id"], 63)
        self.assertEqual(binding["sentinel_policy"], "mesh_-10_to_first_generated_leaf")

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

    def test_variant_coverage_preserves_source_ordinals_without_generated_outputs(self):
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
        self.assertEqual(len(result["bindings"]), 1)
        self.assertEqual(
            generator_values(target),
            {
                ("Frond 36", "Material:Frond:0"): (8, 10),
                ("Frond 36", "Material:Frond:1"): (4, 2),
                ("Frond 36", "Material:Frond:2"): (4, 3),
            },
        )

    def test_partial_source_adoption_deletes_only_replaced_ordinal(self):
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
            [10, 2, 3],
        )
        self.assertIsNone(final_assets.find("Mesh[@ID='1']"))
        self.assertIsNotNone(final_assets.find("Mesh[@ID='2']"))
        self.assertIsNotNone(final_assets.find("Mesh[@ID='3']"))
        self.assertIsNotNone(final_assets.find("Mesh[@ID='10']"))
        self.assertEqual(adoption["removed_original_mesh_ids"], [1])
        self.assertEqual(adoption["preserved_original_mesh_ids"], [2, 3])
        self.assertEqual(adoption["final_material_mesh_ids"], [10, 2, 3])
        self.assertEqual(
            generator_values(target),
            {
                ("Frond 36", "Material:Frond:0"): (4, 10),
                ("Frond 36", "Material:Frond:1"): (4, 2),
                ("Frond 36", "Material:Frond:2"): (4, 3),
            },
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
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            speedtree.normalize_generator_bindings(
                root,
                [{**stale, "created_slot": True}],
                allow_missing=True,
            )

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

    def test_variant_coverage_malformed_parent_fails_without_write(self):
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
            "textures": {},
            "meshes": [{"asset": str(folder / "meshes" / "18.fbx")}],
        }
        return target, sample, manifest, material_name

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
                "textures": {"albedo": str(color)},
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

    def test_untagged_same_name_with_different_mesh_paths_still_conflicts(self):
        with tempfile.TemporaryDirectory() as folder:
            target, sample, manifest, material_name = self._write_upsert_fixture(folder, None)
            manifest["meshes"] = [{"asset": str(Path(folder) / "meshes" / "different.fbx")}]
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
