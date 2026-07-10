import colorsys
import json
from pathlib import Path

import bpy
import numpy as np
from bpy.props import IntProperty
from bpy.types import Operator, Panel
from mathutils import Matrix, Vector

from .constants import DEFAULT_PAIRS, HELPER_PATH
from .materials import build_mesh_object, ensure_collection, make_atlas_material, make_side_material, show_preview_images_in_view
from .props import add_spm_target_item, ensure_pair_items, fill_pair_items, pair_items_to_json, sync_alpha_path
from .speedtree import export_or_update_speedtree_spm_targets
from .utils import dependency_status, run_external_python, write_report


AUTO_SPLIT_COLLECTIONS = (
    "Green",
    "Green_Light",
    "Yellow",
    "Dead",
    "Flower",
    "Bud",
    "Stem",
    "Twig",
    "Cluster",
)
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


def front_uv_samples(obj, max_samples=400):
    if not obj.data.uv_layers.active:
        return [], None
    uv_layer = obj.data.uv_layers.active.data
    seen = set()
    for poly in obj.data.polygons:
        if poly.material_index != 0:
            continue
        for loop_index in poly.loop_indices:
            uv = uv_layer[loop_index].uv
            seen.add((round(float(uv.x), 5), round(float(uv.y), 5)))
    if not seen:
        return [], None
    uvs = sorted(seen)
    xs = [u for u, _v in uvs]
    ys = [v for _u, v in uvs]
    bounds = ((min(xs), min(ys)), (max(xs), max(ys)))
    step = max(1, len(uvs) // max_samples)
    return uvs[::step], bounds


def convex_hull_2d(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(origin, a, b):
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower = []
    for point in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def polygon_area(points):
    if len(points) < 3:
        return 0.0
    area = 0.0
    for index in range(len(points)):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % len(points)]
        area += x0 * y1 - x1 * y0
    return abs(area) * 0.5


def load_atlas_image_array(path):
    if not path or not Path(path).exists():
        return None, False
    existing = {image.name for image in bpy.data.images}
    image = bpy.data.images.load(path, check_existing=True)
    created = image.name not in existing
    width, height = image.size
    if width == 0 or height == 0:
        if created:
            bpy.data.images.remove(image)
        return None, False
    channels = image.channels
    buffer = np.empty(width * height * channels, dtype=np.float32)
    image.pixels.foreach_get(buffer)
    is_float = image.is_float
    if created:
        bpy.data.images.remove(image)
    if is_float:
        # Float images arrive scene-linear; encode to sRGB so hue/sat/lightness
        # thresholds match byte textures.
        linear = np.clip(buffer, 0.0, 1.0)
        buffer = np.where(
            linear <= 0.0031308,
            linear * 12.92,
            1.055 * np.power(linear, 1.0 / 2.4) - 0.055,
        ).astype(np.float32)
    # Flip to top-origin rows so pixel lookups match image coordinates.
    array = buffer.reshape(height, width, channels)[::-1].copy()
    return array, True


def sample_color_family(hue, sat, lit):
    if sat < 0.10:
        return "white" if lit >= 0.55 else "neutral"
    if (hue < 14.0 or hue >= 340.0) and sat >= 0.30 and lit >= 0.30:
        return "red"
    if hue < 45.0:
        return "warm"
    if hue < 68.0:
        return "yellow"
    if hue < 165.0:
        return "green"
    if hue < 250.0:
        return "blue"
    return "violet"


def atlas_object_features(obj, albedo, alpha, alpha_threshold):
    uvs, bounds = front_uv_samples(obj)
    height, width = albedo.shape[0], albedo.shape[1]
    families = {}
    sats, lits = [], []
    valid = 0
    for u, v in uvs:
        px = max(0, min(width - 1, int(u * (width - 1))))
        py = max(0, min(height - 1, int((1.0 - v) * (height - 1))))
        if alpha is not None and alpha[py, px] < alpha_threshold:
            continue
        red, green, blue = (float(value) for value in albedo[py, px][:3])
        hue, lit, sat = colorsys.rgb_to_hls(red, green, blue)
        family = sample_color_family(hue * 360.0, sat, lit)
        families[family] = families.get(family, 0) + 1
        sats.append(sat)
        lits.append(lit)
        valid += 1
    fractions = {key: count / valid for key, count in families.items()} if valid else {}

    solidity = None
    width_fraction = None
    if bounds is not None and alpha is not None:
        (u0, v0), (u1, v1) = bounds
        x0 = max(0, min(width - 1, int(u0 * (width - 1))))
        x1 = max(0, min(width - 1, int(u1 * (width - 1))))
        y0 = max(0, min(height - 1, int((1.0 - v1) * (height - 1))))
        y1 = max(0, min(height - 1, int((1.0 - v0) * (height - 1))))
        if x1 > x0 and y1 > y0:
            mask = alpha[y0:y1 + 1, x0:x1 + 1] >= alpha_threshold
            ink = int(mask.sum())
            if ink > 2:
                long_px = max(x1 - x0 + 1, y1 - y0 + 1)
                width_fraction = ink / long_px / width
                mask_ys, mask_xs = np.nonzero(mask)
                step = max(1, len(mask_xs) // 800)
                hull = convex_hull_2d(list(zip(mask_xs[::step].tolist(), mask_ys[::step].tolist())))
                hull_area = polygon_area(hull)
                solidity = ink / hull_area if hull_area > 0 else 1.0

    x_size, y_size, _z_size = obj.dimensions
    long_side = max(float(x_size), float(y_size))
    short_side = max(0.0001, min(float(x_size), float(y_size)))
    median = float(np.median(np.array(sats, dtype=np.float32))) if sats else 0.0
    return {
        "valid": valid,
        "fractions": fractions,
        "sat": median,
        "lit": float(np.median(np.array(lits, dtype=np.float32))) if lits else 0.0,
        "solidity": solidity,
        "width_fraction": width_fraction,
        "aspect": long_side / short_side,
        "long": long_side,
    }


def classify_atlas_features(features, median_long):
    if features["valid"] == 0:
        return "GreenBase"
    fractions = features["fractions"]
    flower_f = (
        fractions.get("violet", 0.0)
        + fractions.get("blue", 0.0)
        + fractions.get("red", 0.0)
        + fractions.get("white", 0.0)
    )
    green_f = fractions.get("green", 0.0)
    warm_f = fractions.get("warm", 0.0)
    yellow_f = fractions.get("yellow", 0.0)
    solidity = features["solidity"] if features["solidity"] is not None else 0.7
    width_fraction = features["width_fraction"] if features["width_fraction"] is not None else 0.05
    aspect = features["aspect"]

    if flower_f >= 0.35:
        return "Flower"
    # Hairline ink: bare stalks and vines regardless of hue.
    if width_fraction <= 0.008 and aspect >= 3.2:
        return "Stem"
    if solidity < 0.28:
        # Sparse ink: bare or branched silhouettes rather than solid leaf plates.
        if green_f >= 0.55:
            if aspect >= 3.2:
                return "Stem"
            if features["long"] >= 1.30 * median_long:
                return "Cluster"
            return "GreenBase"
        return "Twig"
    if green_f >= 0.55 and aspect >= 3.2 and solidity <= 0.38:
        return "Stem"
    if features["long"] <= 0.35 * median_long and aspect <= 2.5:
        return "Bud"
    if warm_f >= 0.45 and warm_f >= yellow_f and warm_f >= green_f:
        return "Dead"
    if yellow_f >= 0.5 and yellow_f >= green_f:
        return "Yellow"
    return "GreenBase"


def split_green_groups(named_features, labels):
    greens = [name for name, label in labels.items() if label == "GreenBase"]
    for name in greens:
        labels[name] = "Green"
    if len(greens) < 4:
        return labels
    scored = sorted((named_features[name]["lit"] - 0.4 * named_features[name]["sat"], name) for name in greens)
    best_gap = 0.0
    best_index = None
    for index in range(len(scored) - 1):
        gap = scored[index + 1][0] - scored[index][0]
        if gap > best_gap:
            best_gap = gap
            best_index = index
    # Split light/dark greens only when the file is clearly bimodal.
    if best_gap >= 0.10 and best_index is not None and 2 <= best_index + 1 <= len(scored) - 2:
        for index, (_score, name) in enumerate(scored):
            labels[name] = "Green" if index <= best_index else "Green_Light"
    return labels


def auto_split_classifications(objects, props):
    albedo, _albedo_ok = load_atlas_image_array(bpy.path.abspath(props.albedo_path))
    if albedo is None:
        return {}
    alpha_array, alpha_ok = load_atlas_image_array(bpy.path.abspath(props.alpha_path))
    alpha = alpha_array[:, :, 0] if alpha_ok and alpha_array.shape[:2] == albedo.shape[:2] else None
    threshold = float(props.alpha_threshold) / 255.0

    named_features = {obj.name: atlas_object_features(obj, albedo, alpha, threshold) for obj in objects}
    longs = sorted(feature["long"] for feature in named_features.values())
    median_long = longs[len(longs) // 2] if longs else 1.0
    labels = {name: classify_atlas_features(feature, median_long) for name, feature in named_features.items()}
    return split_green_groups(named_features, labels)


def selected_mesh_objects(context):
    return [obj for obj in context.selected_objects if obj.type == "MESH"]


def make_anchor_container_name(obj):
    return f"{obj.name}_anchors"


def existing_anchor_container(obj):
    parent = obj.parent
    if parent is not None and parent.type == "EMPTY" and parent.get("atlas_leaf_anchor_container"):
        return parent
    return None


def local_positive_y_anchor(obj):
    corners = [Vector(corner) for corner in obj.bound_box]
    y_max = max(corner.y for corner in corners)
    x_mid = (min(corner.x for corner in corners) + max(corner.x for corner in corners)) * 0.5
    z_mid = (min(corner.z for corner in corners) + max(corner.z for corner in corners)) * 0.5
    return Vector((x_mid, y_max, z_mid))


def mesh_anchor_empties(obj, prefix="anchor"):
    prefix = (prefix or "anchor").lower()
    anchors = []
    roots = []
    container = existing_anchor_container(obj)
    if container is not None:
        roots.extend(container.children)
    roots.extend(obj.children)

    def walk(candidate):
        if candidate == obj:
            return
        if candidate.type == "EMPTY":
            is_anchor = bool(candidate.get("atlas_leaf_speedtree_anchor"))
            is_anchor = is_anchor or candidate.name.lower().startswith(prefix)
            if is_anchor:
                anchors.append(candidate)
        for child in candidate.children:
            walk(child)

    for root in roots:
        walk(root)
    return anchors


class ATLASLEAF_OT_create_anchor_container(Operator):
    bl_idname = "atlas_leaf.create_anchor_container"
    bl_label = "Create Anchor Container"
    bl_description = "Parent selected meshes under an empty and add one editable SpeedTree anchor empty at the local +Y tip"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        objects = selected_mesh_objects(context)
        if not objects and context.object and context.object.type == "MESH":
            objects = [context.object]
        if not objects:
            self.report({"ERROR"}, "Select one or more mesh objects.")
            return {"CANCELLED"}

        created = 0
        reused = 0
        for obj in objects:
            container = existing_anchor_container(obj)
            if container is None:
                container = bpy.data.objects.new(make_anchor_container_name(obj), None)
                container.empty_display_type = "PLAIN_AXES"
                container.empty_display_size = max(max(obj.dimensions) * 0.12, 0.05)
                container["atlas_leaf_anchor_container"] = True
                collection = obj.users_collection[0] if obj.users_collection else context.scene.collection
                collection.objects.link(container)
                container.matrix_world = obj.matrix_world.copy()
                mesh_world = obj.matrix_world.copy()
                obj.parent = container
                obj.matrix_world = mesh_world
                created += 1
            else:
                reused += 1

            anchors = mesh_anchor_empties(obj, props.speedtree_anchor_prefix)
            if anchors:
                continue

            anchor = bpy.data.objects.new(props.speedtree_anchor_prefix or "anchor", None)
            anchor.empty_display_type = "SINGLE_ARROW"
            anchor.empty_display_size = max(max(obj.dimensions) * 0.08, 0.04)
            anchor["atlas_leaf_speedtree_anchor"] = True
            anchor["speedtree_anchor_id"] = int(props.speedtree_anchor_material_id)
            collection = container.users_collection[0] if container.users_collection else context.scene.collection
            collection.objects.link(anchor)
            anchor.parent = container
            anchor.matrix_world = obj.matrix_world @ Matrix.Translation(local_positive_y_anchor(obj))

        context.view_layer.update()
        self.report({"INFO"}, f"Prepared anchors for {len(objects)} mesh(es); containers created {created}, reused {reused}.")
        return {"FINISHED"}


def front_plate_vertex_uvs(obj):
    # Parameterize from the front plate (material slot 0) only. Pair meshes map
    # their back plate to a different atlas island (often flipped), so mixing
    # front and back UVs produces a meaningless strip parameter.
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return None

    accumulated = {}
    for poly in mesh.polygons:
        if poly.material_index != 0:
            continue
        for loop_index in poly.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            accumulated.setdefault(vertex_index, []).append(uv_layer.data[loop_index].uv.copy())
    if not accumulated:
        return None

    vertex_uvs = {}
    for vertex_index, values in accumulated.items():
        total = Vector((0.0, 0.0))
        for uv in values:
            total += uv
        vertex_uvs[vertex_index] = total / len(values)
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


def build_uv_centerline(samples, long_min, long_range):
    # samples: (uv_long_value, x, y, vertex_index). The UV long axis is a
    # monotonic parameter along the strip, so the per-bin median of vertex
    # positions traces the true centerline even for hairpin bends.
    bin_count = max(10, min(96, len(samples) // 8))
    bins_x = [[] for _index in range(bin_count)]
    bins_y = [[] for _index in range(bin_count)]
    for long_value, x, y, _index in samples:
        normalized = (long_value - long_min) / long_range
        bin_index = min(bin_count - 1, max(0, int(normalized * bin_count)))
        bins_x[bin_index].append(x)
        bins_y[bin_index].append(y)

    def bin_center(values):
        # Interquartile midpoint: stays on the midline even when a bin holds
        # only silhouette vertices (wide leaves have sparse interiors).
        if not values:
            return None
        ordered = sorted(values)
        return (percentile(ordered, 0.25) + percentile(ordered, 0.75)) * 0.5

    center_x = [bin_center(values) for values in bins_x]
    center_y = [bin_center(values) for values in bins_y]
    center_x = fill_missing_profile_values(center_x)
    center_y = fill_missing_profile_values(center_y)
    if not center_x or not center_y:
        return []

    radius = max(2, bin_count // 16)
    center_x = smooth_profile_values(center_x, radius=radius, passes=2)
    center_y = smooth_profile_values(center_y, radius=radius, passes=2)

    tangents = []
    previous_tangent = (0.0, 1.0)
    for index in range(bin_count):
        prev_index = max(0, index - 1)
        next_index = min(bin_count - 1, index + 1)
        tangent_x = center_x[next_index] - center_x[prev_index]
        tangent_y = center_y[next_index] - center_y[prev_index]
        length = (tangent_x * tangent_x + tangent_y * tangent_y) ** 0.5
        if length > 1.0e-12:
            previous_tangent = (tangent_x / length, tangent_y / length)
        tangents.append(previous_tangent)
    tangent_x_values = smooth_profile_values([tangent[0] for tangent in tangents], radius=radius, passes=2)
    tangent_y_values = smooth_profile_values([tangent[1] for tangent in tangents], radius=radius, passes=2)

    profile = []
    cumulative = 0.0
    previous_tangent = (0.0, 1.0)
    for index in range(bin_count):
        if index:
            d_x = center_x[index] - center_x[index - 1]
            d_y = center_y[index] - center_y[index - 1]
            cumulative += (d_x * d_x + d_y * d_y) ** 0.5
        tangent_x = tangent_x_values[index]
        tangent_y = tangent_y_values[index]
        length = (tangent_x * tangent_x + tangent_y * tangent_y) ** 0.5
        if length > 1.0e-12:
            previous_tangent = (tangent_x / length, tangent_y / length)
        profile.append(
            {
                "long": long_min + long_range * ((index + 0.5) / bin_count),
                "cx": center_x[index],
                "cy": center_y[index],
                "tx": previous_tangent[0],
                "ty": previous_tangent[1],
                "arc": cumulative,
            }
        )
    return profile


def project_to_centerline(profile, x, y):
    # Map a position without UV data onto the centerline frame: nearest segment
    # foot point gives the arc coordinate, signed offset gives the cross one.
    best = None
    for index in range(len(profile) - 1):
        start = profile[index]
        end = profile[index + 1]
        seg_x = end["cx"] - start["cx"]
        seg_y = end["cy"] - start["cy"]
        seg_len_sq = seg_x * seg_x + seg_y * seg_y
        if seg_len_sq <= 1.0e-18:
            continue
        rel_x = x - start["cx"]
        rel_y = y - start["cy"]
        along = (rel_x * seg_x + rel_y * seg_y) / seg_len_sq
        clamped = min(1.0, max(0.0, along))
        foot_x = start["cx"] + seg_x * clamped
        foot_y = start["cy"] + seg_y * clamped
        dist_sq = (x - foot_x) ** 2 + (y - foot_y) ** 2
        if best is None or dist_sq < best[0]:
            seg_len = seg_len_sq ** 0.5
            tangent_x = seg_x / seg_len
            tangent_y = seg_y / seg_len
            arc = start["arc"] + seg_len * along
            offset = rel_x * tangent_y - rel_y * tangent_x
            best = (dist_sq, arc, offset)
    if best is None:
        return 0.0, 0.0
    return best[1], best[2]


def straighten_mesh_by_uv(obj, end_window_pct=0.015, unbend_deviation_pct=0.02):
    if obj.data.users > 1:
        obj.data = obj.data.copy()

    mesh = obj.data
    vertex_uvs = front_plate_vertex_uvs(obj)
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
    long_min = min(long_values.values())
    long_max = max(long_values.values())
    long_range = max(long_max - long_min, 1.0e-12)

    source_positions = {vertex.index: vertex.co.copy() for vertex in mesh.vertices}

    def endpoint_indices(use_min):
        window = long_range * end_window_pct
        if use_min:
            return [index for index, value in long_values.items() if value <= long_min + window]
        return [index for index, value in long_values.items() if value >= long_max - window]

    def endpoint_center(indices):
        positions = [source_positions[index] for index in indices]
        return Vector(
            (
                (min(position.x for position in positions) + max(position.x for position in positions)) * 0.5,
                (min(position.y for position in positions) + max(position.y for position in positions)) * 0.5,
                (min(position.z for position in positions) + max(position.z for position in positions)) * 0.5,
            )
        )

    min_indices = endpoint_indices(True)
    max_indices = endpoint_indices(False)
    if not min_indices or not max_indices:
        return False, "Could not find usable UV endpoints"

    min_endpoint = endpoint_center(min_indices)
    max_endpoint = endpoint_center(max_indices)
    stem_is_min = min_endpoint.length <= max_endpoint.length
    stem_indices = min_indices if stem_is_min else max_indices

    samples = [
        (long_values[index], float(source_positions[index].x), float(source_positions[index].y), index)
        for index in vertex_uvs
    ]
    profile = build_uv_centerline(samples, long_min, long_range)
    if len(profile) < 2:
        return False, "Could not build a usable stem centerline"
    total_arc = profile[-1]["arc"]
    if total_arc <= 1.0e-9:
        return False, "Mesh has no usable stem-tip length"

    chord = Vector((profile[-1]["cx"] - profile[0]["cx"], profile[-1]["cy"] - profile[0]["cy"]))
    chord_length = chord.length
    deviation = 0.0
    if chord_length > 1.0e-12:
        direction = chord / chord_length
        for entry in profile:
            rel_x = entry["cx"] - profile[0]["cx"]
            rel_y = entry["cy"] - profile[0]["cy"]
            deviation = max(deviation, abs(rel_x * direction.y - rel_y * direction.x))

    # Unbending only makes sense for strip-like meshes. On wide leaf cards the
    # centerline is dominated by silhouette noise and bending them would fold
    # the surface, so measure width from centerline offsets and gate on it.
    offsets = []
    for long_value, x, y, _index in samples:
        center_x = interpolated_profile_field(profile, "cx", long_value)
        center_y = interpolated_profile_field(profile, "cy", long_value)
        tangent_x = interpolated_profile_field(profile, "tx", long_value)
        tangent_y = interpolated_profile_field(profile, "ty", long_value)
        length = (tangent_x * tangent_x + tangent_y * tangent_y) ** 0.5
        if length <= 1.0e-12:
            continue
        offsets.append(abs((x - center_x) * tangent_y / length - (y - center_y) * tangent_x / length))
    offsets.sort()
    strip_width = 2.0 * percentile(offsets, 0.9) if offsets else 0.0
    strip_ratio = total_arc / max(strip_width, 1.0e-9)
    use_unbend = strip_ratio >= 2.5 and (chord_length <= 1.0e-12 or deviation > total_arc * unbend_deviation_pct)

    # Both frames below map with determinant +1 so face winding (and therefore
    # normals) are preserved; the previous implementation mirrored the mesh.
    new_positions = {}
    if use_unbend:
        for long_value, x, y, index in samples:
            center_x = interpolated_profile_field(profile, "cx", long_value)
            center_y = interpolated_profile_field(profile, "cy", long_value)
            tangent_x = interpolated_profile_field(profile, "tx", long_value)
            tangent_y = interpolated_profile_field(profile, "ty", long_value)
            length = (tangent_x * tangent_x + tangent_y * tangent_y) ** 0.5
            if length > 1.0e-12:
                tangent_x /= length
                tangent_y /= length
            else:
                tangent_x, tangent_y = 0.0, 1.0
            arc = interpolated_profile_field(profile, "arc", long_value)
            offset_x = x - center_x
            offset_y = y - center_y
            long_position = arc + offset_x * tangent_x + offset_y * tangent_y
            cross_position = offset_x * tangent_y - offset_y * tangent_x
            if not stem_is_min:
                long_position = total_arc - long_position
                cross_position = -cross_position
            new_positions[index] = Vector((cross_position, long_position, source_positions[index].z))
        # Back-plate and shell vertices have no front UVs; project them onto the
        # centerline so both plates travel together.
        for index, position in source_positions.items():
            if index in new_positions:
                continue
            long_position, cross_position = project_to_centerline(profile, float(position.x), float(position.y))
            if not stem_is_min:
                long_position = total_arc - long_position
                cross_position = -cross_position
            new_positions[index] = Vector((cross_position, long_position, position.z))
        mode = "unbend"
    else:
        direction = chord / chord_length
        if not stem_is_min:
            direction = -direction
        right = Vector((direction.y, -direction.x))
        for index, position in source_positions.items():
            new_positions[index] = Vector(
                (
                    position.x * right.x + position.y * right.y,
                    position.x * direction.x + position.y * direction.y,
                    position.z,
                )
            )
        mode = "align"

    anchor_x = (
        min(new_positions[index].x for index in stem_indices) + max(new_positions[index].x for index in stem_indices)
    ) * 0.5
    anchor_y = (
        min(new_positions[index].y for index in stem_indices) + max(new_positions[index].y for index in stem_indices)
    ) * 0.5

    for index, position in new_positions.items():
        mesh.vertices[index].co = Vector((position.x - anchor_x, position.y - anchor_y, position.z))
    mesh.update()
    return True, f"{long_axis} axis aligned to local Y, {mode}, stem {'min' if stem_is_min else 'max'}"


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
        sync_alpha_path(props)
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
        sync_alpha_path(props)
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
        sync_alpha_path(props)
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
        if not classifications:
            self.report({"ERROR"}, f"Albedo not found: {bpy.path.abspath(props.albedo_path)}")
            return {"CANCELLED"}

        counts = {name: 0 for name in AUTO_SPLIT_COLLECTIONS}
        targets = {}
        for obj in source_objects.values():
            group = classifications.get(obj.name, "Green")
            if group not in AUTO_SPLIT_COLLECTIONS:
                group = "Green"
            target = targets.get(group)
            if target is None:
                target = targets[group] = ensure_child_collection(root, group)
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
            elif child.name in AUTO_SPLIT_COLLECTIONS and not child.objects and not child.children:
                bpy.data.collections.remove(child)

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
        removed_materials = sum(len(result.get("cleanup", {}).get("removed_materials", [])) for result in results)
        removed_meshes = sum(len(result.get("cleanup", {}).get("removed_mesh_ids", [])) for result in results)
        summary = ", ".join(f"{result['action']} {Path(result['spm_path']).name}" for result in results)
        cleanup_summary = f"; cleaned {removed_materials} materials/{removed_meshes} meshes" if removed_materials or removed_meshes else ""
        self.report({"INFO"}, f"Updated {len(results)} SpeedTree SPMs ({total_meshes} mesh refs{cleanup_summary}): {summary}")
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
        layout.prop(props, "speedtree_mesh_scale")
        layout.prop(props, "speedtree_anchor_export_mode")
        layout.prop(props, "speedtree_anchor_prefix")
        row = layout.row(align=True)
        row.prop(props, "speedtree_anchor_material_id")
        row.prop(props, "speedtree_anchor_scale")
        layout.operator("atlas_leaf.create_anchor_container", icon="EMPTY_AXIS")
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
