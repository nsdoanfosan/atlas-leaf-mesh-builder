import json
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup

from .constants import DEFAULT_ALBEDO, DEFAULT_ALPHA, DEFAULT_PAIRS, default_output_dir, default_speedtree_spm_path
from .target_registry import load_target_registry, save_target_registry
from .texture_paths import matching_alpha_path


def read_pair_json(text):
    data = json.loads(text)
    pairs = []
    for item in data:
        if isinstance(item, dict):
            pairs.append({"front": int(item["front"])})
        else:
            pairs.append({"front": int(item[0])})
    return pairs


def write_pair_json(pairs):
    return json.dumps(pairs, separators=(",", ":"))


def pair_dict_from_values(front):
    return {"front": int(front)}


def pair_items_to_json(props):
    pairs = [pair_dict_from_values(item.front) for item in props.pair_items]
    props.pair_json = write_pair_json(pairs)
    return props.pair_json


def fill_pair_items(props, pairs):
    props.pair_items.clear()
    for pair in pairs:
        item = props.pair_items.add()
        item.front = int(pair["front"])
    pair_items_to_json(props)


def ensure_pair_items(props):
    if len(props.pair_items) == 0:
        fill_pair_items(props, read_pair_json(props.pair_json or json.dumps(DEFAULT_PAIRS)))


def normalized_path_text(path_text):
    return str(Path(bpy.path.abspath(path_text)).resolve()).lower()


def ensure_spm_target_items(props):
    if len(props.speedtree_spm_items) > 0:
        return
    path = bpy.path.abspath(props.speedtree_spm_path)
    if path and Path(path).suffix.lower() == ".spm":
        item = props.speedtree_spm_items.add()
        item.path = path


def save_spm_target_registry(props):
    blend_path = bpy.data.filepath
    if not blend_path or Path(blend_path).suffix.lower() != ".blend":
        return None
    targets = [
        str(Path(bpy.path.abspath(item.path)).absolute())
        for item in props.speedtree_spm_items
        if item.path
    ]
    return save_target_registry(blend_path, targets)


def sync_spm_target_registry(props, initialize_missing=False):
    """Make the scene list follow its per-blend JSON sidecar when present."""
    blend_path = bpy.data.filepath
    if not blend_path or Path(blend_path).suffix.lower() != ".blend":
        ensure_spm_target_items(props)
        return None

    registry = load_target_registry(blend_path)
    if registry is None:
        ensure_spm_target_items(props)
        if initialize_missing and len(props.speedtree_spm_items):
            return save_spm_target_registry(props)
        return None

    current = [
        normalized_path_text(item.path)
        for item in props.speedtree_spm_items
        if item.path
    ]
    target_keys = [normalized_path_text(path) for path in registry["target_spms"]]
    if current != target_keys:
        props.speedtree_spm_items.clear()
        for path in registry["target_spms"]:
            item = props.speedtree_spm_items.add()
            item.path = path
    return registry


def add_spm_target_item(props, path_text):
    path = Path(bpy.path.abspath(path_text))
    if path.suffix.lower() != ".spm":
        raise ValueError("SPM path must end with .spm")

    normalized = normalized_path_text(str(path))
    for item in props.speedtree_spm_items:
        if item.path and normalized_path_text(item.path) == normalized:
            return False, str(path)

    item = props.speedtree_spm_items.add()
    item.path = str(path)
    return True, str(path)


def speedtree_spm_targets(props):
    ensure_spm_target_items(props)
    targets = []
    seen = set()
    for item in props.speedtree_spm_items:
        if not item.path:
            continue
        path = Path(bpy.path.abspath(item.path))
        if path.suffix.lower() != ".spm":
            continue
        normalized = normalized_path_text(str(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        targets.append(path)
    return targets


def sync_alpha_path(props):
    albedo = bpy.path.abspath(props.albedo_path)
    alpha = matching_alpha_path(albedo) if albedo else None
    alpha_text = str(alpha) if alpha is not None else ""
    if props.alpha_path != alpha_text:
        props.alpha_path = alpha_text
    return alpha_text


def update_alpha_from_albedo(props, _context):
    sync_alpha_path(props)


def mesh_object_poll(_self, obj):
    return (
        obj is not None
        and obj.type == "MESH"
        and not obj.get("atlas_leaf_projected_source_backup")
    )


class ATLASLEAF_PairItem(PropertyGroup):
    front: IntProperty(name="F", default=1, min=1, description="Front island number")


class ATLASLEAF_SpmTargetItem(PropertyGroup):
    path: StringProperty(name="SPM", subtype="FILE_PATH", default="")


class ATLASLEAF_Properties(PropertyGroup):
    albedo_path: StringProperty(
        name="Albedo",
        subtype="FILE_PATH",
        default=DEFAULT_ALBEDO,
        update=update_alpha_from_albedo,
    )
    alpha_path: StringProperty(
        name="Alpha",
        subtype="FILE_PATH",
        default=DEFAULT_ALPHA,
    )
    output_dir: StringProperty(
        name="Output Dir",
        subtype="DIR_PATH",
        default=default_output_dir(),
    )
    external_python: StringProperty(
        name="Python",
        default="python",
        description="External Python with Pillow, OpenCV, scikit-image, and triangle installed",
    )
    collection_name: StringProperty(
        name="Collection",
        default="Atlas_Leaf_Meshes",
    )
    pair_json: StringProperty(
        name="Front Islands JSON",
        default=json.dumps(DEFAULT_PAIRS),
        description="Front alpha island entries used for leaf generation",
    )
    pair_items: CollectionProperty(type=ATLASLEAF_PairItem)
    new_pair_front: IntProperty(
        name="F",
        default=1,
        min=1,
        description="Front island number to add",
    )
    quality: EnumProperty(
        name="Quality",
        items=[
            ("FAST", "Fast", "Lower density, less internal regularity"),
            ("SPEEDTREE_LOW", "SpeedTree Low", "Low triangle count for SpeedTree leaf cards"),
            ("BALANCED", "Balanced", "Recommended q18 balanced triangulation"),
            ("HIGH", "High", "Higher density q22 triangulation"),
        ],
        default="BALANCED",
    )
    alpha_threshold: IntProperty(name="Alpha Threshold", default=127, min=1, max=254)
    min_area: IntProperty(name="Min Island Area", default=400, min=1)
    shell_gap: FloatProperty(
        name="Shell Gap",
        default=0.012,
        min=0.0,
        soft_max=0.08,
        precision=4,
        description="Distance between front and back surfaces before bridging the boundary",
    )
    surface_mode: EnumProperty(
        name="Plate Mode",
        items=[
            ("DOUBLE", "Two Plates", "Create front and back surfaces with identical UVs"),
            ("SINGLE", "One Plate", "Create one front surface only"),
        ],
        default="DOUBLE",
    )
    side_uv_inset: FloatProperty(
        name="Side UV Inset",
        default=0.035,
        min=0.0,
        soft_max=0.2,
        precision=4,
        description="Moves shell bridge UVs inward from the front island boundary toward the island center",
    )
    no_shell: BoolProperty(
        name="No Shell Side Faces",
        default=False,
        description="Keep only separated front/back surfaces at Shell Gap and omit the bridged side faces",
    )
    shell_side_sharp_angle: FloatProperty(
        name="Shell Sharp Angle",
        default=35.0,
        min=0.0,
        max=180.0,
        precision=1,
        description="Mark shell side edges sharp when adjacent side face normals exceed this angle",
    )
    place_at_origin: BoolProperty(
        name="Stem Pivot At Origin",
        default=True,
        description="Move each generated leaf mesh so the stem-side pivot is the object origin at 0,0,0",
    )
    projected_shell_front: PointerProperty(
        name="Front",
        type=bpy.types.Object,
        poll=mesh_object_poll,
        description="One-plate mesh whose geometry, topology, pivot, and front UVs define the projected shell",
    )
    projected_shell_back: PointerProperty(
        name="Back Projection",
        type=bpy.types.Object,
        poll=mesh_object_poll,
        description="Manually aligned one-plate mesh used only to project back atlas UVs",
    )
    clear_existing: BoolProperty(name="Clear Collection", default=True)
    speedtree_spm_path: StringProperty(
        name="SPM To Add",
        subtype="FILE_PATH",
        default=default_speedtree_spm_path(),
        description="SpeedTree 10.1 SPM path to add to the update list",
    )
    speedtree_spm_items: CollectionProperty(type=ATLASLEAF_SpmTargetItem)
    speedtree_material_name: StringProperty(
        name="Material Name",
        default="Elm01_Atlas_Leaf",
        description="Legacy field kept for older blend files",
    )
    speedtree_atlas_asset_name: StringProperty(
        name="Atlas Asset Name",
        default="",
        description=(
            "Explicit base name for newly exported atlas materials; M_cluster_ is canonicalized to M_leaf_. "
            "Leave blank to preserve a legacy blend/file-derived name"
        ),
    )
    speedtree_source_materials_json: StringProperty(
        name="Generator Source Mapping",
        default="{}",
        description="JSON mapping from absolute target SPM path to source Material_v8 names to replace",
    )
    speedtree_create_missing_spm: BoolProperty(
        name="Create Missing Target SPM",
        default=False,
        description="Explicitly allow creating a blank SpeedTree target when a listed SPM does not exist",
    )
    speedtree_mesh_scale: FloatProperty(
        name="FBX Geometry Scale",
        default=0.01,
        min=0.000001,
        soft_max=1.0,
        precision=6,
        description="Multiplier applied to exported FBX mesh geometry; SpeedTree Mesh Scale stays 1",
    )
    speedtree_mesh_asset_scale: FloatProperty(
        name="SpeedTree Mesh Asset Scale",
        default=1.0,
        min=0.000001,
        soft_max=1.0,
        precision=6,
        description=(
            "Value written to each generated SpeedTree Mesh asset Scale field; "
            "keep separate from FBX geometry scaling to avoid applying the conversion twice"
        ),
    )
    speedtree_unit_probe_contract_json: StringProperty(
        name="Verified Unit Probe Contract",
        default="",
        description=(
            "Verified role-independent Blender FBX to SpeedTree unit contract. "
            "Production physical-capture builds require this receipt and reject "
            "duplicate geometry, Mesh asset, or generator scaling."
        ),
    )
    speedtree_anchor_export_mode: EnumProperty(
        name="Anchor Export",
        items=[
            ("XML", "SpeedTree XML", "Export child anchor empties as SpeedTree XML LeafReferences"),
            ("FBX_EMPTY", "FBX Empties", "Include child anchor empties in each exported FBX for probing"),
            ("OFF", "Off", "Ignore anchor empties during SpeedTree mesh export"),
        ],
        default="XML",
    )
    speedtree_anchor_prefix: StringProperty(
        name="Anchor Prefix",
        default="anchor",
        description="Anchor empty name prefix used when collecting anchors under a mesh container",
    )
    speedtree_anchor_material_id: IntProperty(
        name="Anchor ID",
        default=0,
        min=0,
        description="Material/id value written to SpeedTree XML LeafReferences for these anchors",
    )
    speedtree_anchor_scale: FloatProperty(
        name="Anchor Scale",
        default=1.0,
        min=0.000001,
        soft_max=10.0,
        precision=4,
        description="Scale value written to SpeedTree XML LeafReferences",
    )
    last_report: StringProperty(name="Last Report", default="")

