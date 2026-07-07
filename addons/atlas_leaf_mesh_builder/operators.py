import json
from pathlib import Path

import bpy
from bpy.props import IntProperty
from bpy.types import Operator, Panel
from mathutils import Vector

from .constants import DEFAULT_PAIRS, HELPER_PATH
from .materials import build_mesh_object, ensure_collection, make_atlas_material, make_side_material, show_preview_images_in_view
from .props import ensure_pair_items, fill_pair_items, pair_items_to_json, add_spm_target_item
from .speedtree import export_or_update_speedtree_spm_targets
from .utils import dependency_status, run_external_python, write_report


AUTO_SPLIT_COLLECTIONS = ("Green", "Dead", "Twig", "Stem")
LEGACY_AUTO_SPLIT_COLLECTIONS = {
    "Grouped_By_Color_Form",
    "Green_Leaves",
    "Yellow_Olive_Leaves",
    "Dead_Brown_Leaves",
    "Dead_Leaves",
    "Stems",
    "Twigs",
    "Stem_or_Twig",
}


def remove_collection_tree(collection):
    for child in list(collection.children):
        remove_collection_tree(child)
    bpy.data.collections.remove(collection)


def ensure_child_collection(parent, name):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if not any(child.name == collection.name for child in parent.children):
        parent.children.link(collection)
    return collection


def collect_auto_split_sources(collection, output):
    for obj in collection.objects:
        if obj.type == "MESH":
            output[obj.name] = obj
    for child in collection.children:
        collect_auto_split_sources(child, output)


def unlink_from_auto_split_collections(obj, keep_collection=None):
    for collection in list(obj.users_collection):
        if keep_collection is not None and collection == keep_collection:
            continue
        if collection.name in AUTO_SPLIT_COLLECTIONS or collection.name in LEGACY_AUTO_SPLIT_COLLECTIONS:
            collection.objects.unlink(obj)


def simple_shape_material_group(obj):
    x_size, y_size, _z_size = obj.dimensions
    long_side = max(float(x_size), float(y_size))
    short_side = max(0.0001, min(float(x_size), float(y_size)))
    area = float(x_size) * float(y_size)
    aspect = long_side / short_side

    if aspect >= 4.0:
        return "Stem" if long_side >= 1.0 else "Twig"
    if short_side <= 0.18 and area <= 0.35:
        return "Twig"
    return None


def front_uv_bounds(obj):
    if not obj.data.uv_layers.active:
        return None
    uv_layer = obj.data.uv_layers.active.data
    uv_min = [1.0e9, 1.0e9]
    uv_max = [-1.0e9, -1.0e9]
    for poly in obj.data.polygons:
        if poly.material_index != 0:
            continue
        for loop_index in poly.loop_indices:
            uv = uv_layer[loop_index].uv
            uv_min[0] = min(uv_min[0], float(uv.x))
            uv_min[1] = min(uv_min[1], float(uv.y))
            uv_max[0] = max(uv_max[0], float(uv.x))
            uv_max[1] = max(uv_max[1], float(uv.y))
    if uv_max[0] < uv_min[0] or uv_max[1] < uv_min[1]:
        return None
    return uv_min, uv_max


def auto_split_classifications(objects, props):
    payload = {
        "albedo": bpy.path.abspath(props.albedo_path),
        "alpha": bpy.path.abspath(props.alpha_path),
        "alpha_threshold": int(props.alpha_threshold),
        "items": [],
    }
    for obj in objects:
        bounds = front_uv_bounds(obj)
        payload["items"].append(
            {
                "name": obj.name,
                "dims": [float(value) for value in obj.dimensions],
                "uv_min": bounds[0] if bounds else None,
                "uv_max": bounds[1] if bounds else None,
            }
        )

    code = r"""
import colorsys, json
from pathlib import Path
from PIL import Image

payload = json.loads(ARG_PAYLOAD)
albedo = Image.open(payload["albedo"]).convert("RGB")
alpha = Image.open(payload["alpha"]).convert("L") if Path(payload["alpha"]).exists() else None
w, h = albedo.size
threshold = int(payload.get("alpha_threshold", 127))

def color(uv_min, uv_max):
    if uv_min is None or uv_max is None:
        return None
    r = g = b = n = 0
    for yi in range(9):
        v = uv_min[1] + (uv_max[1] - uv_min[1]) * ((yi + 0.5) / 9)
        py = max(0, min(h - 1, int((1.0 - v) * (h - 1))))
        for xi in range(9):
            u = uv_min[0] + (uv_max[0] - uv_min[0]) * ((xi + 0.5) / 9)
            px = max(0, min(w - 1, int(u * (w - 1))))
            if alpha is not None and alpha.getpixel((px, py)) < threshold:
                continue
            cr, cg, cb = albedo.getpixel((px, py))
            r += cr; g += cg; b += cb; n += 1
    if n == 0:
        return None
    return r / (n * 255.0), g / (n * 255.0), b / (n * 255.0)

def classify(item):
    x, y = float(item["dims"][0]), float(item["dims"][1])
    long_side = max(x, y)
    short_side = max(0.0001, min(x, y))
    aspect = long_side / short_side
    # First-pass shape: narrow/small islands are branch-like material candidates.
    if aspect >= 4.0 or short_side <= 0.16:
        return "Twig" if long_side < 1.0 else "Stem"
    rgb = color(item.get("uv_min"), item.get("uv_max"))
    if rgb is None:
        return "Green"
    red, green, blue = rgb
    hue, _lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    hue = hue * 360.0
    # Brown/yellow dry leaves: red is close to or above green, blue is low, hue is warm.
    if saturation >= 0.12 and hue < 80.0 and red >= green * 0.92 and green >= blue * 1.05:
        return "Dead"
    return "Green"

print(json.dumps({item["name"]: classify(item) for item in payload["items"]}))
""".replace("ARG_PAYLOAD", repr(json.dumps(payload)))
    result = run_external_python(props.external_python, ["-c", code], timeout=60)
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {}


def selected_mesh_objects(context):
    return [obj for obj in context.selected_objects if obj.type == "MESH"]


def average_vertex_uvs(obj):
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return None

    material_names = [slot.material.name if slot.material else "" for slot in obj.material_slots]
    preferred = {vertex.index: [] for vertex in mesh.vertices}
    fallback = {vertex.index: [] for vertex in mesh.vertices}

    for poly in mesh.polygons:
        material_name = material_names[poly.material_index] if poly.material_index < len(material_names) else ""
        is_surface = "shell" not in material_name.lower() and "edge" not in material_name.lower()
        for loop_index in poly.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            uv = uv_layer.data[loop_index].uv.copy()
            fallback[vertex_index].append(uv)
            if is_surface:
                preferred[vertex_index].append(uv)

    vertex_uvs = {}
    for vertex in mesh.vertices:
        values = preferred[vertex.index] or fallback[vertex.index]
        if not values:
            return None
        total = Vector((0.0, 0.0))
        for uv in values:
            total += uv
        vertex_uvs[vertex.index] = total / len(values)
    return vertex_uvs


def percentile(sorted_values, pct):
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * pct
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    factor = position - lower
    return float(sorted_values[lower] * (1.0 - factor) + sorted_values[upper] * factor)


def fill_missing_profile_values(values):
    known = [index for index, value in enumerate(values) if value is not None]
    if not known:
        return []
    filled = list(values)
    first = known[0]
    for index in range(0, first):
        filled[index] = filled[first]
    last = known[-1]
    for index in range(last + 1, len(filled)):
        filled[index] = filled[last]
    for left, right in zip(known, known[1:]):
        left_value = filled[left]
        right_value = filled[right]
        span = right - left
        for index in range(left + 1, right):
            factor = (index - left) / span
            filled[index] = left_value * (1.0 - factor) + right_value * factor
    return [float(value) for value in filled]


def smooth_profile_values(values, radius, passes=3):
    if not values:
        return []
    smoothed = list(values)
    radius = max(1, int(radius))
    for _pass_index in range(passes):
        next_values = []
        for index in range(len(smoothed)):
            total = 0.0
            total_weight = 0.0
            start = max(0, index - radius)
            end = min(len(smoothed), index + radius + 1)
            for sample_index in range(start, end):
                weight = radius + 1 - abs(sample_index - index)
                total += smoothed[sample_index] * weight
                total_weight += weight
            next_values.append(total / total_weight if total_weight else smoothed[index])
        smoothed = next_values
    return smoothed


def interpolated_profile_field(profile, field, long_value):
    if not profile:
        return 0.0
    if long_value <= profile[0]["long"]:
        return profile[0][field]
    if long_value >= profile[-1]["long"]:
        return profile[-1][field]
    for index in range(len(profile) - 1):
        current = profile[index]
        next_item = profile[index + 1]
        if current["long"] <= long_value <= next_item["long"]:
            span = next_item["long"] - current["long"]
            factor = 0.0 if span <= 1.0e-12 else (long_value - current["long"]) / span
            return current[field] * (1.0 - factor) + next_item[field] * factor
    return profile[-1][field]


def build_stem_center_profile(samples, long_min, long_range, cross_range, long_scale, cross_scale, stem_is_min, stem_cross):
    bin_count = max(24, min(96, len(samples) // 2))
    bins = [[] for _index in range(bin_count)]
    for long_value, cross_value, _index in samples:
        normalized = (long_value - long_min) / long_range
        bin_index = min(bin_count - 1, max(0, int(normalized * bin_count)))
        bins[bin_index].append(cross_value)

    centers = []
    previous_center = stem_cross
    base_window = max(cross_range * 0.035, 0.004)
    max_step = max(cross_range * 0.05, 0.004)
    ordered_bins = range(bin_count) if stem_is_min else range(bin_count - 1, -1, -1)
    for bin_index in ordered_bins:
        values = bins[bin_index]
        if not values:
            centers.append((bin_index, previous_center))
            continue
        ordered = sorted(values)

        window = base_window
        nearby = [value for value in ordered if abs(value - previous_center) <= window]
        while len(nearby) < 4 and window < cross_range * 0.22:
            window *= 1.6
            nearby = [value for value in ordered if abs(value - previous_center) <= window]
        if nearby:
            nearby.sort()
            low = percentile(nearby, 0.25)
            high = percentile(nearby, 0.75)
            center = (low + high) * 0.5
        else:
            center = min(ordered, key=lambda value: abs(value - previous_center))

        delta = center - previous_center
        if abs(delta) > max_step:
            center = previous_center + max_step * (1.0 if delta > 0.0 else -1.0)
        centers.append((bin_index, center))
        previous_center = center

    centers_by_bin = [None] * bin_count
    for bin_index, center in centers:
        centers_by_bin[bin_index] = center
    filled_centers = fill_missing_profile_values(centers_by_bin)
    if not filled_centers:
        filled_centers = [stem_cross] * bin_count

    radius = max(2, bin_count // 24)
    smoothed_centers = smooth_profile_values(filled_centers, radius=radius, passes=3)

    profile = []
    cumulative = 0.0
    previous_long = None
    previous_center = None
    for bin_index, center in enumerate(smoothed_centers):
        long_value = long_min + long_range * ((bin_index + 0.5) / bin_count)
        if previous_long is not None:
            d_long = (long_value - previous_long) * long_scale
            d_cross = (center - previous_center) * cross_scale
            cumulative += (d_long * d_long + d_cross * d_cross) ** 0.5
        profile.append({"long": long_value, "center": center, "arc": cumulative})
        previous_long = long_value
        previous_center = center
    return profile


def straighten_mesh_by_uv(obj, center_window_pct=0.04, end_window_pct=0.015):
    if obj.data.users > 1:
        obj.data = obj.data.copy()

    mesh = obj.data
    vertex_uvs = average_vertex_uvs(obj)
    if vertex_uvs is None:
        return False, "No usable active UVs"

    u_values = [uv.x for uv in vertex_uvs.values()]
    v_values = [uv.y for uv in vertex_uvs.values()]
    u_range = max(u_values) - min(u_values)
    v_range = max(v_values) - min(v_values)
    long_axis = "U" if u_range >= v_range else "V"

    long_values = {
        index: (uv.x if long_axis == "U" else uv.y)
        for index, uv in vertex_uvs.items()
    }
    cross_values = {
        index: (uv.y if long_axis == "U" else uv.x)
        for index, uv in vertex_uvs.items()
    }
    long_min = min(long_values.values())
    long_max = max(long_values.values())
    long_range = max(long_max - long_min, 1.0e-12)

    source_positions = {vertex.index: vertex.co.copy() for vertex in mesh.vertices}
    local_values = {
        "X": [position.x for position in source_positions.values()],
        "Y": [position.y for position in source_positions.values()],
    }
    if long_axis == "U":
        local_long_values = local_values["X"]
        local_cross_values = local_values["Y"]
    else:
        local_long_values = local_values["Y"]
        local_cross_values = local_values["X"]

    local_long_range = max(local_long_values) - min(local_long_values)
    local_cross_range = max(local_cross_values) - min(local_cross_values)
    cross_range = max(max(cross_values.values()) - min(cross_values.values()), 1.0e-12)
    if local_long_range <= 1.0e-12:
        return False, f"Mesh has no usable local {'X' if long_axis == 'U' else 'Y'} length"
    if local_cross_range <= 1.0e-12:
        local_cross_range = local_long_range * 0.05

    def endpoint_info(use_min):
        window = long_range * end_window_pct
        if use_min:
            indices = [index for index, value in long_values.items() if value <= long_min + window]
        else:
            indices = [index for index, value in long_values.items() if value >= long_max - window]
        positions = [source_positions[index] for index in indices]
        center = Vector(
            (
                (min(position.x for position in positions) + max(position.x for position in positions)) * 0.5,
                (min(position.y for position in positions) + max(position.y for position in positions)) * 0.5,
                (min(position.z for position in positions) + max(position.z for position in positions)) * 0.5,
            )
        )
        cross_center = (
            min(cross_values[index] for index in indices) + max(cross_values[index] for index in indices)
        ) * 0.5
        return center, cross_center

    min_endpoint, min_cross_center = endpoint_info(True)
    max_endpoint, max_cross_center = endpoint_info(False)
    stem_is_min = Vector((min_endpoint.x, min_endpoint.y, 0.0)).length <= Vector(
        (max_endpoint.x, max_endpoint.y, 0.0)
    ).length
    stem_cross = min_cross_center if stem_is_min else max_cross_center

    samples = [(long_values[index], cross_values[index], index) for index in vertex_uvs]
    long_scale = local_long_range / long_range
    cross_scale = local_cross_range / cross_range
    center_profile = build_stem_center_profile(
        samples,
        long_min,
        long_range,
        cross_range,
        long_scale,
        cross_scale,
        stem_is_min,
        stem_cross,
    )
    if len(center_profile) < 2:
        return False, "Could not build a usable stem centerline"

    new_positions = {}
    for long_value, cross_value, index in samples:
        arc = interpolated_profile_field(center_profile, "arc", long_value)
        center = interpolated_profile_field(center_profile, "center", long_value)
        total_length = center_profile[-1]["arc"]
        long_position = arc if stem_is_min else -(total_length - arc)
        cross_position = (cross_value - center) * cross_scale
        if long_axis == "U":
            new_positions[index] = Vector((long_position, cross_position, source_positions[index].z))
        else:
            new_positions[index] = Vector((cross_position, long_position, source_positions[index].z))

    window = long_range * end_window_pct
    if stem_is_min:
        stem_indices = [index for index, value in long_values.items() if value <= long_min + window]
    else:
        stem_indices = [index for index, value in long_values.items() if value >= long_max - window]
    anchor_x = (
        min(new_positions[index].x for index in stem_indices) + max(new_positions[index].x for index in stem_indices)
    ) * 0.5
    anchor_y = (
        min(new_positions[index].y for index in stem_indices) + max(new_positions[index].y for index in stem_indices)
    ) * 0.5

    for index, position in new_positions.items():
        mesh.vertices[index].co = Vector((position.x - anchor_x, position.y - anchor_y, position.z))
    mesh.update()
    local_axis = "X" if long_axis == "U" else "Y"
    return True, f"{long_axis} axis to local {local_axis}, stem centerline, stem {'min' if stem_is_min else 'max'}"


class ATLASLEAF_OT_check_dependencies(Operator):
    bl_idname = "atlas_leaf.check_dependencies"
    bl_label = "Check Dependencies"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        ok, stdout, stderr = dependency_status(props.external_python)
        if ok:
            self.report({"INFO"}, "External Python dependencies are available.")
            return {"FINISHED"}
        self.report({"ERROR"}, stderr or stdout or "Dependency check failed.")
        return {"CANCELLED"}


class ATLASLEAF_OT_install_dependencies(Operator):
    bl_idname = "atlas_leaf.install_dependencies"
    bl_label = "Install Dependencies"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        result = run_external_python(
            props.external_python,
            ["-m", "pip", "install", "pillow", "opencv-python", "scikit-image", "triangle"],
            timeout=300,
        )
        if result.returncode != 0:
            self.report({"ERROR"}, result.stderr[-800:] or "pip install failed.")
            return {"CANCELLED"}
        self.report({"INFO"}, "Dependencies installed.")
        return {"FINISHED"}


class ATLASLEAF_OT_reset_elm01_pairs(Operator):
    bl_idname = "atlas_leaf.reset_elm01_pairs"
    bl_label = "Reset Elm01 Leaves"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        fill_pair_items(props, DEFAULT_PAIRS)
        self.report({"INFO"}, "Elm01 front island preset restored.")
        return {"FINISHED"}


class ATLASLEAF_OT_add_pair(Operator):
    bl_idname = "atlas_leaf.add_pair"
    bl_label = "Add Leaf"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        ensure_pair_items(props)
        item = props.pair_items.add()
        item.front = int(props.new_pair_front)
        pair_items_to_json(props)
        self.report({"INFO"}, f"Added front island F{item.front:02d}.")
        return {"FINISHED"}


class ATLASLEAF_OT_remove_pair(Operator):
    bl_idname = "atlas_leaf.remove_pair"
    bl_label = "Remove Pair"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        if self.index < 0 or self.index >= len(props.pair_items):
            self.report({"ERROR"}, "Pair index is outside the current pair list.")
            return {"CANCELLED"}
        props.pair_items.remove(self.index)
        pair_items_to_json(props)
        self.report({"INFO"}, f"Removed leaf #{self.index + 1}.")
        return {"FINISHED"}


class ATLASLEAF_OT_clear_pairs(Operator):
    bl_idname = "atlas_leaf.clear_pairs"
    bl_label = "Clear Leaves"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        props.pair_items.clear()
        props.pair_json = "[]"
        self.report({"INFO"}, "Cleared front island list.")
        return {"FINISHED"}


class ATLASLEAF_OT_build_label_preview(Operator):
    bl_idname = "atlas_leaf.build_label_preview"
    bl_label = "Build Label Preview"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        albedo = bpy.path.abspath(props.albedo_path)
        alpha = bpy.path.abspath(props.alpha_path)
        output_dir = Path(bpy.path.abspath(props.output_dir))
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "atlas_leaf_label_preview_result.json"

        if not Path(albedo).exists():
            self.report({"ERROR"}, f"Albedo not found: {albedo}")
            return {"CANCELLED"}
        if not Path(alpha).exists():
            self.report({"ERROR"}, f"Alpha not found: {alpha}")
            return {"CANCELLED"}
        ok, _stdout, _stderr = dependency_status(props.external_python)
        if not ok:
            self.report({"ERROR"}, "Missing external Python dependencies. Run Install Dependencies.")
            return {"CANCELLED"}
        pair_json = pair_items_to_json(props)

        args = [
            str(HELPER_PATH),
            "--albedo",
            albedo,
            "--alpha",
            alpha,
            "--output",
            str(json_path),
            "--pairs-json",
            pair_json,
            "--quality",
            props.quality,
            "--alpha-threshold",
            str(props.alpha_threshold),
            "--min-area",
            str(props.min_area),
            "--preview-dir",
            str(output_dir),
            "--preview-only",
        ]
        result = run_external_python(props.external_python, args, timeout=300)
        if result.returncode != 0:
            message = result.stderr[-1200:] or result.stdout[-1200:] or "Preview helper failed."
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        data = json.loads(json_path.read_text(encoding="utf-8"))
        preview_data = data.get("preview", {})
        shown = show_preview_images_in_view(preview_data)
        pair_preview = preview_data.get("pair_preview", str(output_dir))
        props.last_report = pair_preview
        self.report({"INFO"}, f"Label previews written and showed {len(shown)} image planes: {pair_preview}")
        return {"FINISHED"}


class ATLASLEAF_OT_generate(Operator):
    bl_idname = "atlas_leaf.generate"
    bl_label = "Generate Leaf Meshes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        albedo = bpy.path.abspath(props.albedo_path)
        alpha = bpy.path.abspath(props.alpha_path)
        output_dir = Path(bpy.path.abspath(props.output_dir))
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "atlas_leaf_mesh_builder_result.json"

        if not Path(albedo).exists():
            self.report({"ERROR"}, f"Albedo not found: {albedo}")
            return {"CANCELLED"}
        if not Path(alpha).exists():
            self.report({"ERROR"}, f"Alpha not found: {alpha}")
            return {"CANCELLED"}
        ok, stdout, stderr = dependency_status(props.external_python)
        if not ok:
            self.report({"ERROR"}, "Missing external Python dependencies. Run Install Dependencies.")
            return {"CANCELLED"}
        pair_json = pair_items_to_json(props)

        args = [
            str(HELPER_PATH),
            "--albedo",
            albedo,
            "--alpha",
            alpha,
            "--output",
            str(json_path),
            "--pairs-json",
            pair_json,
            "--quality",
            props.quality,
            "--alpha-threshold",
            str(props.alpha_threshold),
            "--min-area",
            str(props.min_area),
            "--shell-gap",
            str(props.shell_gap),
            "--side-uv-inset",
            str(props.side_uv_inset),
            "--surface-mode",
            props.surface_mode,
        ]
        if props.no_shell:
            args.append("--no-shell")
        if props.place_at_origin:
            args.append("--place-at-origin")
        result = run_external_python(props.external_python, args, timeout=600)
        if result.returncode != 0:
            message = result.stderr[-1200:] or result.stdout[-1200:] or "Helper failed."
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        data = json.loads(json_path.read_text(encoding="utf-8"))
        collection = ensure_collection(props.collection_name, props.clear_existing)
        front_material = make_atlas_material("elm01_leaf_front_atlas", albedo)
        back_material = make_atlas_material("elm01_leaf_back_atlas", albedo)
        side_material = make_side_material("elm01_leaf_shell_edge")
        materials = {"front": front_material, "back": back_material, "side": side_material}
        for mesh_data in data["objects"]:
            build_mesh_object(mesh_data, materials, collection, props.shell_side_sharp_angle)

        report_path = write_report(data, output_dir)
        props.last_report = str(report_path)
        self.report({"INFO"}, f"Generated {len(data['objects'])} objects. Report: {report_path}")
        return {"FINISHED"}


class ATLASLEAF_OT_straight_mesh(Operator):
    bl_idname = "atlas_leaf.straight_mesh"
    bl_label = "Straight Mesh"
    bl_description = "Straighten selected leaf meshes from their UVs while preserving UVs and object transforms"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        objects = selected_mesh_objects(context)
        if not objects:
            self.report({"ERROR"}, "Select one or more mesh objects.")
            return {"CANCELLED"}

        straightened = 0
        skipped = []
        for obj in objects:
            ok, message = straighten_mesh_by_uv(obj)
            if ok:
                straightened += 1
            else:
                skipped.append(f"{obj.name}: {message}")

        context.view_layer.update()
        if straightened == 0:
            self.report({"ERROR"}, "; ".join(skipped) or "No meshes were straightened.")
            return {"CANCELLED"}
        if skipped:
            self.report({"WARNING"}, f"Straightened {straightened}; skipped {len(skipped)}.")
        else:
            self.report({"INFO"}, f"Straightened {straightened} selected mesh{'es' if straightened != 1 else ''}.")
        return {"FINISHED"}


class ATLASLEAF_OT_auto_split_material_collections(Operator):
    bl_idname = "atlas_leaf.auto_split_material_collections"
    bl_label = "Auto Split Material Collections"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        root = bpy.data.collections.get(props.collection_name)
        if root is None:
            self.report({"ERROR"}, f"Collection not found: {props.collection_name}")
            return {"CANCELLED"}

        source_objects = {}
        collect_auto_split_sources(root, source_objects)
        if not source_objects:
            self.report({"ERROR"}, f"No mesh objects found in {props.collection_name}")
            return {"CANCELLED"}

        classifications = auto_split_classifications(source_objects.values(), props)
        targets = {name: ensure_child_collection(root, name) for name in AUTO_SPLIT_COLLECTIONS}
        counts = {name: 0 for name in AUTO_SPLIT_COLLECTIONS}
        for obj in source_objects.values():
            group = simple_shape_material_group(obj) or classifications.get(obj.name, "Green")
            if group not in targets:
                group = "Green"
            target = targets[group]
            if not any(target_obj == obj for target_obj in target.objects):
                target.objects.link(obj)
            unlink_from_auto_split_collections(obj, keep_collection=target)
            if any(root_obj == obj for root_obj in root.objects):
                root.objects.unlink(obj)
            obj["atlas_leaf_material_group"] = group
            counts[group] += 1

        for child in list(root.children):
            if child.name in LEGACY_AUTO_SPLIT_COLLECTIONS:
                remove_collection_tree(child)

        bpy.context.view_layer.update()
        summary = ", ".join(f"{name}:{count}" for name, count in counts.items() if count)
        self.report({"INFO"}, f"Auto split {len(source_objects)} meshes ({summary or 'no classified groups'}).")
        return {"FINISHED"}


class ATLASLEAF_OT_build_speedtree_spm(Operator):
    bl_idname = "atlas_leaf.build_speedtree_spm"
    bl_label = "Build/Update Target SPMs"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        try:
            results = export_or_update_speedtree_spm_targets(props)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        props.last_report = str(results[-1]["spm_path"])
        total_meshes = sum(len(result["mesh_ids"]) for result in results)
        summary = ", ".join(f"{result['action']} {Path(result['spm_path']).name}" for result in results)
        self.report({"INFO"}, f"Updated {len(results)} SpeedTree SPMs ({total_meshes} mesh refs): {summary}")
        return {"FINISHED"}


class ATLASLEAF_OT_add_speedtree_spm(Operator):
    bl_idname = "atlas_leaf.add_speedtree_spm"
    bl_label = "Add Target SPM"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        try:
            added, path = add_spm_target_item(props, props.speedtree_spm_path)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"{'Added' if added else 'Already listed'} target SPM: {path}")
        return {"FINISHED"}


class ATLASLEAF_OT_remove_speedtree_spm(Operator):
    bl_idname = "atlas_leaf.remove_speedtree_spm"
    bl_label = "Remove Target SPM"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        if self.index < 0 or self.index >= len(props.speedtree_spm_items):
            self.report({"ERROR"}, "SPM target index is outside the current list.")
            return {"CANCELLED"}
        removed = props.speedtree_spm_items[self.index].path
        props.speedtree_spm_items.remove(self.index)
        self.report({"INFO"}, f"Removed target SPM: {removed}")
        return {"FINISHED"}


class ATLASLEAF_OT_clear_speedtree_spms(Operator):
    bl_idname = "atlas_leaf.clear_speedtree_spms"
    bl_label = "Clear Target SPMs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.atlas_leaf_builder.speedtree_spm_items.clear()
        self.report({"INFO"}, "Cleared target SPM list.")
        return {"FINISHED"}


class ATLASLEAF_PT_panel(Panel):
    bl_label = "Atlas Leaf Mesh"
    bl_idname = "ATLASLEAF_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Atlas Leaf"

    def draw(self, context):
        layout = self.layout
        props = context.scene.atlas_leaf_builder
        layout.prop(props, "albedo_path")
        layout.prop(props, "alpha_path")
        layout.prop(props, "output_dir")
        layout.prop(props, "external_python")
        row = layout.row(align=True)
        row.operator("atlas_leaf.check_dependencies", icon="CHECKMARK")
        row.operator("atlas_leaf.install_dependencies", icon="IMPORT")
        layout.separator()
        layout.prop(props, "quality")
        layout.prop(props, "alpha_threshold")
        layout.prop(props, "min_area")
        layout.prop(props, "surface_mode")
        layout.prop(props, "shell_gap")
        layout.prop(props, "no_shell")
        layout.prop(props, "side_uv_inset")
        layout.prop(props, "shell_side_sharp_angle")
        layout.prop(props, "place_at_origin")
        layout.prop(props, "collection_name")
        layout.prop(props, "clear_existing")
        row = layout.row(align=True)
        row.operator("atlas_leaf.reset_elm01_pairs", icon="FILE_REFRESH")
        row.operator("atlas_leaf.clear_pairs", icon="TRASH")
        layout.operator("atlas_leaf.build_label_preview", icon="IMAGE_DATA")
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Front Island Editor")
        row.label(text=f"{len(props.pair_items)} selected" if len(props.pair_items) else "Auto: all alpha islands")
        if len(props.pair_items) == 0:
            box.label(text="Empty list generates every detected alpha island.")
        add = box.row(align=True)
        add.prop(props, "new_pair_front")
        add.operator("atlas_leaf.add_pair", text="", icon="ADD")
        header = box.row(align=True)
        header.label(text="#", icon="SORT_ASC")
        header.label(text="F")
        header.label(text="")
        for index, item in enumerate(props.pair_items):
            row = box.row(align=True)
            row.label(text=f"{index + 1:02d}")
            row.prop(item, "front", text="")
            remove = row.operator("atlas_leaf.remove_pair", text="", icon="X")
            remove.index = index
        layout.separator()
        layout.operator("atlas_leaf.generate", icon="MESH_DATA")
        layout.operator("atlas_leaf.straight_mesh", icon="MOD_SIMPLEDEFORM")
        layout.operator("atlas_leaf.auto_split_material_collections", icon="OUTLINER_COLLECTION")
        layout.separator()
        layout.prop(props, "speedtree_material_name")
        layout.prop(props, "speedtree_mesh_scale")
        row = layout.row(align=True)
        row.prop(props, "speedtree_spm_path")
        row.operator("atlas_leaf.add_speedtree_spm", text="", icon="ADD")
        box = layout.box()
        header = box.row(align=True)
        header.label(text="Target SPMs")
        header.label(text=f"{len(props.speedtree_spm_items)} listed")
        header.operator("atlas_leaf.clear_speedtree_spms", text="", icon="TRASH")
        for index, item in enumerate(props.speedtree_spm_items):
            row = box.row(align=True)
            row.label(text=f"{index + 1:02d}")
            row.prop(item, "path", text="")
            remove = row.operator("atlas_leaf.remove_speedtree_spm", text="", icon="X")
            remove.index = index
        layout.operator("atlas_leaf.build_speedtree_spm", icon="FILE_TICK")
        if props.last_report:
            layout.label(text="Last report:")
            layout.label(text=props.last_report)
