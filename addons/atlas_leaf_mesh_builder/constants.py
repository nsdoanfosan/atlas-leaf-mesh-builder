from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
HELPER_PATH = ADDON_DIR / "helper_pipeline.py"
DEFAULT_ALBEDO = r"D:/OneDrive/Forestportfolio/Texture/Elm/TCom_Leaves_Elm01/TCom_Leaves_Elm01_4K_albedo.tif"
DEFAULT_ALPHA = r"D:/OneDrive/Forestportfolio/Texture/Elm/TCom_Leaves_Elm01/TCom_Leaves_Elm01_4K_alpha.tif"
SPEEDTREE_101_DIR = Path(r"C:/Program Files/SpeedTree/SpeedTree Modeler v10.1.0")
SPEEDTREE_101_BLANK_SPM = SPEEDTREE_101_DIR / "tree_templates" / "Games" / "Blank.spm"
SPEEDTREE_101_MATERIAL_SAMPLE = SPEEDTREE_101_DIR / "samples" / "Games" / "Bush" / "Cluster" / "Leaf_Cluster_1.spm"
SPEEDTREE_101_EXTERNAL_MESH_SAMPLE = SPEEDTREE_101_DIR / "samples" / "Games" / "Broadleaf" / "Oak_Desktop_Forest.spm"
DEFAULT_FRONTS = [1, 2, 3, 4, 10, 6, 7, 8, 9, 11, 12, 13]
DEFAULT_PAIRS = [{"front": index} for index in DEFAULT_FRONTS]

def default_output_dir():
    return str(Path.home() / "Documents" / "Codex" / "atlas_leaf_mesh_builder_output")


def default_speedtree_export_dir():
    return str(Path.home() / "Documents" / "Codex" / "atlas_leaf_speedtree_export")


def default_speedtree_spm_path():
    return str(Path(default_speedtree_export_dir()) / "Elm01_Atlas_Leaf_MeshAssets_v10_1.spm")

