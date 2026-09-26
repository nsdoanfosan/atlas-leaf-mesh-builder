"""Material-default slots cover the first adopted mesh without losing -10."""
import copy
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from test_speedtree_xml import (
    add_material, add_mesh, add_variant_generator, generator_values,
    speedtree, write_spm,
)


class AdoptionDefaultMeshCoverageTests(unittest.TestCase):
    def prepare(self, target, generator_type):
        root = ET.Element("SpeedTreeModel")
        assets = ET.SubElement(root, "Assets")
        add_material(assets, 8, "M_cluster", [12, 13, 27, 29])
        for mesh_id in (12, 13, 27, 29, 89, 90, 91, 92, 95):
            add_mesh(assets, mesh_id)
        add_variant_generator(root, generator_type, "Cluster", [(8, -10)])
        write_spm(target, root)
        adoption = speedtree.prepare_source_material_adoption(
            target, {"export_scope_id": "default-mesh-coverage"}, "M_cluster", 8,
        )
        root = speedtree.read_spm_xml(target)
        speedtree.update_spm_material_mesh_ids(
            root.find("Assets/Material_v8[@ID='8']"), [89, 90, 91, 92, 95],
        )
        speedtree.write_spm_xml(target, root)
        groups = [{
            "material": "M_cluster", "material_id": 8,
            "mesh_ids": [89, 90, 91, 92, 95],
            "meshes": [{"source_object": f"cluster_{n:02d}", "source_ordinal": n}
                       for n in range(1, 6)],
        }]
        return adoption, groups

    def connect(self, target, groups, previous=None):
        return speedtree.connect_atlas_generators_in_spm(
            target, ["M_cluster"], groups, [8], previous_bindings=previous,
            source_mesh_ids_by_name={"M_cluster": [12, 13, 27, 29]},
            generator_variant_policy="ensure_all_material_cutouts",
        )

    def test_default_first_mesh_and_explicit_tail_adopt_and_repeat(self):
        for generator_type in ("Leaf Mesh", "Frond"):
            with self.subTest(generator_type=generator_type), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "cluster.spm"
                adoption, groups = self.prepare(target, generator_type)
                connection = self.connect(target, groups)
                self.assertEqual(connection["created_slot_pairs"], 4)
                for attempt in range(2):
                    with self.subTest(attempt=attempt):
                        finalized = speedtree.finalize_source_material_adoption(
                            target, adoption, groups[0]["mesh_ids"], connection,
                        )
                        self.assertEqual(finalized["final_material_mesh_ids"], [89, 90, 91, 92, 95])
                        self.assertEqual(set(generator_values(target).values()),
                                         {(8, -10), (8, 90), (8, 91), (8, 92), (8, 95)})
                        root = speedtree.read_spm_xml(target)
                        self.assertEqual({int(node.attrib["ID"]) for node in root.findall("Assets/Mesh")},
                                         {89, 90, 91, 92, 95})
                        connection = self.connect(target, groups, connection["bindings"])
                        self.assertEqual(connection["created_slot_pairs"], 0)

    def test_missing_or_unrelated_default_binding_does_not_cover_first_mesh(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "cluster.spm"
            adoption, groups = self.prepare(target, "Leaf Mesh")
            connection = self.connect(target, groups)
            for invalid in ("missing", "other_material", "other_ordinal"):
                with self.subTest(invalid=invalid):
                    broken = copy.deepcopy(connection)
                    default = next(b for b in broken["bindings"] if b["target_mesh_id"] == -10)
                    if invalid == "missing":
                        broken["bindings"].remove(default)
                    elif invalid == "other_material":
                        default["target_material_id"] = 9
                    else:
                        default["leaf_ordinal"] = 6
                    before = target.read_bytes()
                    with self.assertRaisesRegex(RuntimeError, "does not cover.*89"):
                        speedtree.finalize_source_material_adoption(
                            target, adoption, groups[0]["mesh_ids"], broken,
                        )
                    self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
