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
    properties = ET.SubElement(generator, "Properties")
    for index, (material_id, mesh_id) in enumerate(slots):
        prefix = f"Leaves:Type:{index}"
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
        self.assertEqual(result["changed_slot_pairs"], 4)
        self.assertEqual(
            generator_values(self.spm_path),
            {
                ("Leaf", "Leaves:Type:0"): (7, 63),
                ("Leaf", "Leaves:Type:1"): (7, 64),
                ("Leaf 2", "Leaves:Type:0"): (8, 71),
                ("Frond", "Leaves:Type:0"): (9, 78),
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


class SafetyTests(unittest.TestCase):
    def _write_upsert_fixture(self, folder, existing_scope):
        folder = Path(folder)
        target = folder / "target.spm"
        material_name = "M_cluster_lauraceae_atlas_01"
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
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


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
