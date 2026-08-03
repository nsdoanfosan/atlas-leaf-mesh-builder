bl_info = {
    "name": "Atlas Leaf Mesh Builder",
    "author": "Codex for PARK",
    "version": (0, 5, 5),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Atlas Leaf",
    "description": "Build no-opacity leaf meshes from atlas alpha islands.",
    "category": "Object",
}

import importlib
import sys

import bpy
from bpy.app.handlers import persistent


_SUBMODULE_NAMES = (
    "constants",
    "utils",
    "generator_delivery_scope",
    "generator_slot_ownership",
    "texture_paths",
    "target_registry",
    "props",
    "integration_api",
    "materials",
    "speedtree",
    "operators",
)


def _load_submodules():
    loaded = {}
    for name in _SUBMODULE_NAMES:
        full_name = f"{__name__}.{name}"
        if full_name in sys.modules:
            loaded[name] = importlib.reload(sys.modules[full_name])
        else:
            loaded[name] = importlib.import_module(f".{name}", __name__)
    return loaded


_modules = _load_submodules()

ATLASLEAF_PairItem = _modules["props"].ATLASLEAF_PairItem
ATLASLEAF_SpmTargetItem = _modules["props"].ATLASLEAF_SpmTargetItem
ATLASLEAF_Properties = _modules["props"].ATLASLEAF_Properties
fill_pair_items = _modules["props"].fill_pair_items
ensure_spm_target_items = _modules["props"].ensure_spm_target_items
sync_spm_target_registry = _modules["props"].sync_spm_target_registry
DEFAULT_PAIRS = _modules["constants"].DEFAULT_PAIRS

ATLASLEAF_OT_check_dependencies = _modules["operators"].ATLASLEAF_OT_check_dependencies
ATLASLEAF_OT_install_dependencies = _modules["operators"].ATLASLEAF_OT_install_dependencies
ATLASLEAF_OT_reset_elm01_pairs = _modules["operators"].ATLASLEAF_OT_reset_elm01_pairs
ATLASLEAF_OT_add_pair = _modules["operators"].ATLASLEAF_OT_add_pair
ATLASLEAF_OT_remove_pair = _modules["operators"].ATLASLEAF_OT_remove_pair
ATLASLEAF_OT_clear_pairs = _modules["operators"].ATLASLEAF_OT_clear_pairs
ATLASLEAF_OT_build_label_preview = _modules["operators"].ATLASLEAF_OT_build_label_preview
ATLASLEAF_OT_generate = _modules["operators"].ATLASLEAF_OT_generate
ATLASLEAF_OT_set_projected_shell_front = _modules["operators"].ATLASLEAF_OT_set_projected_shell_front
ATLASLEAF_OT_set_projected_shell_back = _modules["operators"].ATLASLEAF_OT_set_projected_shell_back
ATLASLEAF_OT_build_projected_shell = _modules["operators"].ATLASLEAF_OT_build_projected_shell
ATLASLEAF_OT_straight_mesh = _modules["operators"].ATLASLEAF_OT_straight_mesh
ATLASLEAF_OT_split_below_x_axis = _modules["operators"].ATLASLEAF_OT_split_below_x_axis
ATLASLEAF_OT_auto_split_material_collections = _modules["operators"].ATLASLEAF_OT_auto_split_material_collections
ATLASLEAF_OT_create_anchor_container = _modules["operators"].ATLASLEAF_OT_create_anchor_container
ATLASLEAF_OT_build_speedtree_spm = _modules["operators"].ATLASLEAF_OT_build_speedtree_spm
ATLASLEAF_OT_add_speedtree_spm = _modules["operators"].ATLASLEAF_OT_add_speedtree_spm
ATLASLEAF_OT_remove_speedtree_spm = _modules["operators"].ATLASLEAF_OT_remove_speedtree_spm
ATLASLEAF_OT_clear_speedtree_spms = _modules["operators"].ATLASLEAF_OT_clear_speedtree_spms
ATLASLEAF_PT_panel = _modules["operators"].ATLASLEAF_PT_panel


classes = (
    ATLASLEAF_PairItem,
    ATLASLEAF_SpmTargetItem,
    ATLASLEAF_Properties,
    ATLASLEAF_OT_check_dependencies,
    ATLASLEAF_OT_install_dependencies,
    ATLASLEAF_OT_reset_elm01_pairs,
    ATLASLEAF_OT_add_pair,
    ATLASLEAF_OT_remove_pair,
    ATLASLEAF_OT_clear_pairs,
    ATLASLEAF_OT_build_label_preview,
    ATLASLEAF_OT_generate,
    ATLASLEAF_OT_set_projected_shell_front,
    ATLASLEAF_OT_set_projected_shell_back,
    ATLASLEAF_OT_build_projected_shell,
    ATLASLEAF_OT_straight_mesh,
    ATLASLEAF_OT_split_below_x_axis,
    ATLASLEAF_OT_auto_split_material_collections,
    ATLASLEAF_OT_create_anchor_container,
    ATLASLEAF_OT_build_speedtree_spm,
    ATLASLEAF_OT_add_speedtree_spm,
    ATLASLEAF_OT_remove_speedtree_spm,
    ATLASLEAF_OT_clear_speedtree_spms,
    ATLASLEAF_PT_panel,
)


STALE_CLASS_NAMES = (
    "ATLASLEAF_OT_export_speedtree",
    "ATLASLEAF_OT_export_speedtree_spm",
    "ATLASLEAF_OT_inject_speedtree_spm",
    "ATLASLEAF_OT_load_pair_editor",
    "ATLASLEAF_OT_apply_pair_editor",
    "ATLASLEAF_OT_auto_match_pairs",
    "ATLASLEAF_OT_open_speedtree_spm",
)


def unregister_class_if_registered(class_name):
    cls = getattr(bpy.types, class_name, None)
    if cls is not None:
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


def initialize_scene_items():
    for scene in bpy.data.scenes:
        props = getattr(scene, "atlas_leaf_builder", None)
        if props is not None and len(props.pair_items) == 0:
            fill_pair_items(props, DEFAULT_PAIRS)
        if props is not None:
            try:
                sync_spm_target_registry(props, initialize_missing=False)
            except Exception as exc:
                props.last_report = f"Target JSON sync error: {exc}"
    return None


@persistent
def atlas_leaf_registry_load_post(_unused):
    if not bpy.app.timers.is_registered(initialize_scene_items):
        bpy.app.timers.register(initialize_scene_items, first_interval=0.1)


def register():
    if hasattr(bpy.types.Scene, "atlas_leaf_builder"):
        del bpy.types.Scene.atlas_leaf_builder
    for class_name in STALE_CLASS_NAMES:
        unregister_class_if_registered(class_name)
    for cls in classes:
        unregister_class_if_registered(cls.__name__)
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.atlas_leaf_builder = bpy.props.PointerProperty(type=ATLASLEAF_Properties)
    for handler in list(bpy.app.handlers.load_post):
        if getattr(handler, "__name__", "") == "atlas_leaf_registry_load_post":
            bpy.app.handlers.load_post.remove(handler)
    bpy.app.handlers.load_post.append(atlas_leaf_registry_load_post)
    bpy.app.timers.register(initialize_scene_items, first_interval=0.1)


def unregister():
    for handler in list(bpy.app.handlers.load_post):
        if getattr(handler, "__name__", "") == "atlas_leaf_registry_load_post":
            bpy.app.handlers.load_post.remove(handler)
    if hasattr(bpy.types.Scene, "atlas_leaf_builder"):
        del bpy.types.Scene.atlas_leaf_builder
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
