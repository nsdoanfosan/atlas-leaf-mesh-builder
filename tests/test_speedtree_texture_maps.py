import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from test_speedtree_xml import speedtree


def material_with_maps(*map_names):
    material = ET.Element("Material_v8")
    for name in map_names:
        node = ET.SubElement(material, "Map", {"Name": name})
        ET.SubElement(node, "TexFilename")
        ET.SubElement(node, "TexEnabled").text = "false"
    return material


def map_values(material, name):
    node = next(node for node in material.findall("Map") if node.attrib.get("Name") == name)
    return node.findtext("TexFilename"), node.findtext("TexEnabled")


def write_tga_header(path, width, height):
    header = bytearray(18)
    header[2] = 2
    header[12:14] = int(width).to_bytes(2, "little")
    header[14:16] = int(height).to_bytes(2, "little")
    header[16] = 24
    Path(path).write_bytes(header)


class SpeedTreeTextureMapTests(unittest.TestCase):
    def test_gloss_and_ao_are_written_to_speedtree_material_maps(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spm = root / "branch_elm_01.spm"
            gloss = root / "branch_elm_01_Gloss.tga"
            ao = root / "branch_elm_01_AO.tga"
            material = material_with_maps(
                "Color",
                "Opacity",
                "Normal",
                "Gloss",
                "Height",
                "SubsurfaceColor",
                "SubsurfaceAmount",
                "Specular",
                "Metallic",
                "AO",
                "Custom",
                "Custom2",
            )

            speedtree.update_spm_material(
                material,
                spm,
                {"gloss": gloss, "ao": ao},
                [1],
            )

            self.assertEqual(map_values(material, "Gloss"), (gloss.name, "true"))
            self.assertEqual(map_values(material, "AO"), (ao.name, "true"))

    def test_missing_ao_still_disables_the_existing_map(self):
        material = material_with_maps(
            "Color",
            "Opacity",
            "Normal",
            "Gloss",
            "Height",
            "SubsurfaceColor",
            "SubsurfaceAmount",
            "Specular",
            "Metallic",
            "AO",
            "Custom",
            "Custom2",
        )

        speedtree.update_spm_material(material, Path("tree.spm"), {}, [1])

        self.assertEqual(map_values(material, "AO"), ("", "false"))

    def test_material_and_map_metadata_follow_actual_texture_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spm = root / "tree.spm"
            color = root / "tree.tga"
            opacity = root / "tree_Opacity.tga"
            write_tga_header(color, 1024, 1024)
            write_tga_header(opacity, 1024, 1024)
            material = material_with_maps("Color", "Opacity")
            ET.SubElement(material, "Width").text = "2048"
            ET.SubElement(material, "Height").text = "2048"

            speedtree.update_spm_material(
                material,
                spm,
                {"albedo": color, "alpha": opacity},
                [1],
            )

            self.assertEqual(material.findtext("Width"), "1024")
            self.assertEqual(material.findtext("Height"), "1024")
            for name in ("Color", "Opacity"):
                node = next(
                    node
                    for node in material.findall("Map")
                    if node.attrib.get("Name") == name
                )
                self.assertEqual(node.findtext("TexSizeX"), "1024")
                self.assertEqual(node.findtext("TexSizeY"), "1024")

    def test_mixed_atlas_resolutions_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            color = root / "tree.tga"
            opacity = root / "tree_Opacity.tga"
            write_tga_header(color, 1024, 1024)
            write_tga_header(opacity, 512, 512)
            material = material_with_maps("Color", "Opacity")

            with self.assertRaisesRegex(
                RuntimeError,
                "must share one pixel resolution",
            ):
                speedtree.update_spm_material(
                    material,
                    root / "tree.spm",
                    {"albedo": color, "alpha": opacity},
                    [1],
                )


if __name__ == "__main__":
    unittest.main()
