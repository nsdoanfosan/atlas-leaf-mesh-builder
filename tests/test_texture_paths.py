import importlib.util
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
    def test_plain_albedo_finds_gloss_and_ao_siblings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            albedo = root / "branch_elm_01.tga"
            gloss = root / "branch_elm_01_Gloss.tga"
            ao = root / "branch_elm_01_AO.tga"
            unrelated = root / "branch_scotspine_01_AO.tga"
            for path in (albedo, gloss, ao, unrelated):
                path.touch()

            result = texture_paths.atlas_texture_paths(albedo)

            self.assertEqual(result["gloss"], gloss)
            self.assertEqual(result["ao"], ao)

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


if __name__ == "__main__":
    unittest.main()
