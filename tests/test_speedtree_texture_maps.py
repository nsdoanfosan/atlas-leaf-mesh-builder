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


def map_contract_values(material, name):
    node = next(
        node
        for node in material.findall("Map")
        if node.attrib.get("Name") == name
    )
    return {
        "filename": node.findtext("TexFilename"),
        "enabled": node.findtext("TexEnabled"),
        "source": node.findtext("TexSource"),
        "invert": node.findtext("TexInvert"),
    }


def write_tga_header(path, width, height):
    header = bytearray(18)
    header[2] = 2
    header[12:14] = int(width).to_bytes(2, "little")
    header[14:16] = int(height).to_bytes(2, "little")
    header[16] = 24
    Path(path).write_bytes(header)


class SpeedTreeTextureMapTests(unittest.TestCase):
    def test_cluster_bake_provisional_maps_keep_distinct_subsurface_amount(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            textures = {}
            for role in (
                "albedo",
                "alpha",
                "normal",
                "gloss",
                "height",
                "translucency",
                "subsurface_amount",
                "ao",
            ):
                textures[role] = root / f"branch_{role}.tga"
                write_tga_header(textures[role], 1024, 1024)
            material = material_with_maps(
                "Color",
                "Opacity",
                "Normal",
                "Gloss",
                "Height",
                "SubsurfaceColor",
                "SubsurfaceAmount",
                "AO",
                "Specular",
                "Metallic",
                "Custom",
                "Custom2",
            )

            speedtree.update_spm_material(
                material,
                root / "tree.spm",
                textures,
                [1],
                texture_contract_status=(
                    speedtree.SOURCE_FALLBACK_STATUS
                ),
            )

            self.assertEqual(
                map_values(material, "SubsurfaceColor"),
                (textures["translucency"].name, "true"),
            )
            self.assertEqual(
                map_values(material, "SubsurfaceAmount"),
                (textures["subsurface_amount"].name, "true"),
            )

    def test_provisional_source_maps_are_rewired_when_canonical_arrives(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "Leaf_Albedo.tga"
            write_tga_header(original, 1024, 1024)
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
                root / "tree.spm",
                {"albedo": original},
                [1],
                texture_contract_status=(
                    speedtree.SOURCE_FALLBACK_STATUS
                ),
            )
            self.assertEqual(
                map_values(material, "Color"),
                (original.name, "true"),
            )

            canonical = {}
            for role in (
                "color",
                "opacity",
                "normal",
                "extra",
                "height",
                "subsurface",
            ):
                canonical[role] = root / f"T_leaf_test_{role}.tga"
                write_tga_header(canonical[role], 1024, 1024)
            speedtree.update_spm_material(
                material,
                root / "tree.spm",
                canonical,
                [1],
                texture_contract_status=(
                    speedtree.CANONICAL_TEXTURE_STATUS
                ),
            )

            self.assertEqual(
                map_values(material, "Color"),
                (canonical["color"].name, "true"),
            )
            self.assertNotEqual(
                map_values(material, "Color")[0],
                original.name,
            )

    def test_canonical_six_role_contract_maps_extra_channels_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            texture_base = "T_leaf_test_atlas_01"
            textures = {}
            for role in (
                "color",
                "opacity",
                "normal",
                "extra",
                "height",
                "subsurface",
            ):
                textures[role] = root / f"{texture_base}_{role}.tga"
                write_tga_header(textures[role], 1024, 1024)
            material = material_with_maps(
                "Color",
                "Opacity",
                "Normal",
                "Gloss",
                "Height",
                "SubsurfaceColor",
                "SubsurfaceAmount",
                "AO",
                "Specular",
                "Metallic",
                "Custom",
                "Custom2",
            )

            speedtree.update_spm_material(
                material,
                root / "tree.spm",
                textures,
                [1],
            )

            self.assertEqual(
                map_contract_values(material, "Color"),
                {
                    "filename": textures["color"].name,
                    "enabled": "true",
                    "source": "0",
                    "invert": "false",
                },
            )
            self.assertEqual(
                map_contract_values(material, "Opacity"),
                {
                    "filename": textures["opacity"].name,
                    "enabled": "false",
                    "source": "1",
                    "invert": "false",
                },
            )
            self.assertEqual(
                map_contract_values(material, "Gloss"),
                {
                    "filename": textures["extra"].name,
                    "enabled": "true",
                    "source": "2",
                    "invert": "true",
                },
            )
            self.assertEqual(
                map_contract_values(material, "AO"),
                {
                    "filename": textures["extra"].name,
                    "enabled": "true",
                    "source": "1",
                    "invert": "false",
                },
            )

    def test_optional_generated_ao_overrides_extra_ao_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spm = root / "tree.spm"
            textures = {}
            for role in (
                "color",
                "opacity",
                "normal",
                "extra",
                "height",
                "subsurface",
            ):
                textures[role] = root / f"T_leaf_test_{role}.tga"
                write_tga_header(textures[role], 1024, 1024)
            textures["ao"] = root / "T_leaf_test_ao_from_height.tga"
            write_tga_header(textures["ao"], 1024, 1024)
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
                textures,
                [1],
            )

            self.assertEqual(
                map_values(material, "Gloss"),
                (textures["extra"].name, "true"),
            )
            self.assertEqual(
                map_values(material, "AO"),
                (textures["ao"].name, "true"),
            )
            self.assertEqual(
                map_contract_values(material, "AO")["source"],
                "0",
            )

    def test_missing_optional_ao_uses_canonical_extra_red_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            textures = {}
            for role in (
                "color",
                "opacity",
                "normal",
                "extra",
                "height",
                "subsurface",
            ):
                textures[role] = root / f"T_leaf_test_{role}.tga"
                write_tga_header(textures[role], 1024, 1024)
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
                root / "tree.spm",
                textures,
                [1],
            )

            self.assertEqual(
                map_values(material, "AO"),
                (textures["extra"].name, "true"),
            )
            self.assertEqual(
                map_contract_values(material, "AO")["source"],
                "1",
            )

    def test_material_and_map_metadata_follow_actual_texture_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spm = root / "tree.spm"
            color = root / "tree.tga"
            opacity = root / "tree_Opacity.tga"
            write_tga_header(color, 1024, 1024)
            write_tga_header(opacity, 1024, 1024)
            textures = {
                "color": color,
                "opacity": opacity,
            }
            for role in ("normal", "extra", "height", "subsurface"):
                textures[role] = root / f"T_tree_{role}.tga"
                write_tga_header(textures[role], 1024, 1024)
            material = material_with_maps("Color", "Opacity")
            ET.SubElement(material, "Width").text = "2048"
            ET.SubElement(material, "Height").text = "2048"

            speedtree.update_spm_material(
                material,
                spm,
                textures,
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
            textures = {
                "color": color,
                "opacity": opacity,
            }
            for role in ("normal", "extra", "height", "subsurface"):
                textures[role] = root / f"T_tree_{role}.tga"
                write_tga_header(textures[role], 1024, 1024)
            material = material_with_maps("Color", "Opacity")

            with self.assertRaisesRegex(
                RuntimeError,
                "must share one pixel resolution",
            ):
                speedtree.update_spm_material(
                    material,
                    root / "tree.spm",
                    textures,
                    [1],
                )


if __name__ == "__main__":
    unittest.main()
