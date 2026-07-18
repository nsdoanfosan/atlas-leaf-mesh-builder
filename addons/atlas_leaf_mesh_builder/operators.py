import colorsys
import json
from pathlib import Path

import bpy
import numpy as np
from bpy.props import IntProperty
from bpy.types import Operator, Panel
from mathutils import Matrix, Vector

from .constants import DEFAULT_PAIRS, HELPER_PATH
from .materials import build_mesh_object, configure_leaf_surface, ensure_collection, make_atlas_material, make_side_material, show_preview_images_in_view
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
STRAIGHT_BACKUP_COLLECTION = "AtlasLeaf_Straight_Backups"
STRAIGHT_BACKUP_FLAG = "atlas_leaf_straight_backup"
STRAIGHT_BACKUP_SOURCE = "atlas_leaf_straight_source"
STRAIGHT_BACKUP_REFERENCE = "atlas_leaf_straight_backup_object"
PROJECTED_SOURCE_BACKUP_COLLECTION = "AtlasLeaf_ProjectedShell_Source_Backups"
PROJECTED_SOURCE_BACKUP_FLAG = "atlas_leaf_projected_source_backup"
PROJECTED_SOURCE_ROLE = "atlas_leaf_projected_source_role"
PROJECTED_SOURCE_OUTPUT = "atlas_leaf_projected_source_output"
PROJECTED_SOURCE_COLLECTIONS = "atlas_leaf_projected_source_collections"


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


def generation_pair_json(props):
    if props.surface_mode == "SINGLE":
        return "[]"
    return pair_items_to_json(props)


def ensure_straight_backup_collection(context):
    collection = bpy.data.collections.get(STRAIGHT_BACKUP_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(STRAIGHT_BACKUP_COLLECTION)
    if collection.name not in context.scene.collection.children:
        context.scene.collection.children.link(collection)
    collection.hide_render = True
    return collection


def existing_straight_backup(obj, collection):
    stored_name = obj.get(STRAIGHT_BACKUP_REFERENCE)
    if stored_name:
        candidate = bpy.data.objects.get(stored_name)
        if candidate is not None and candidate.get(STRAIGHT_BACKUP_FLAG):
            return candidate
    for candidate in collection.objects:
        if candidate.get(STRAIGHT_BACKUP_FLAG) and candidate.get(STRAIGHT_BACKUP_SOURCE) == obj.name:
            obj[STRAIGHT_BACKUP_REFERENCE] = candidate.name
            return candidate
    return None


def create_straight_backup(context, obj):
    collection = ensure_straight_backup_collection(context)
    existing = existing_straight_backup(obj, collection)
    if existing is not None:
        return existing, False

    backup = obj.copy()
    backup.data = obj.data.copy()
    backup.name = f"{obj.name}__straight_backup"
    backup.data.name = f"{backup.name}_Mesh"
    collection.objects.link(backup)
    backup.matrix_world = obj.matrix_world.copy()
    backup[STRAIGHT_BACKUP_FLAG] = True
    backup[STRAIGHT_BACKUP_SOURCE] = obj.name
    backup.hide_render = True
    backup.hide_select = True
    backup.hide_viewport = True
    obj[STRAIGHT_BACKUP_REFERENCE] = backup.name
    return backup, True


def remove_straight_backup(obj, backup):
    backup_data = backup.data if backup and backup.type == "MESH" else None
    backup_name = backup.name if backup else ""
    if backup is not None:
        bpy.data.objects.remove(backup, do_unlink=True)
    if backup_data is not None and backup_data.users == 0:
        bpy.data.meshes.remove(backup_data)
    if obj.get(STRAIGHT_BACKUP_REFERENCE) == backup_name:
        del obj[STRAIGHT_BACKUP_REFERENCE]


class BackProjectionCoverageError(ValueError):
    def __init__(self, outside_count, back_vertex_indices):
        self.outside_count = int(outside_count)
        self.back_vertex_indices = sorted(set(int(index) for index in back_vertex_indices))
        super().__init__(
            f"Back Projection does not cover {self.outside_count} Front vertices. "
            f"The nearest Back boundary vertices were selected; enlarge or realign that area."
        )


def point_segment_distance_squared_2d(point, start, end):
    segment = end - start
    length_squared = segment.length_squared
    if length_squared <= 1.0e-18:
        return (point - start).length_squared
    factor = min(1.0, max(0.0, (point - start).dot(segment) / length_squared))
    nearest = start + segment * factor
    return (point - nearest).length_squared


def nearest_back_boundary_vertices(back_obj, outside_points):
    mesh = back_obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return []
    boundary_records = front_boundary_records(mesh, uv_layer)
    selected = set()
    for point in outside_points:
        best_record = None
        best_distance = None
        for record in boundary_records:
            start = mesh.vertices[record["start"]].co.xy
            end = mesh.vertices[record["end"]].co.xy
            distance = point_segment_distance_squared_2d(point, start, end)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_record = record
        if best_record is not None:
            selected.add(best_record["start"])
            selected.add(best_record["end"])
    return sorted(selected)


def select_back_projection_vertices(context, back_obj, vertex_indices):
    if context.object is not None and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    back_obj.hide_viewport = False
    back_obj.hide_select = False
    try:
        back_obj.hide_set(False)
    except RuntimeError:
        pass
    bpy.ops.object.select_all(action="DESELECT")
    back_obj.select_set(True)
    context.view_layer.objects.active = back_obj
    for vertex in back_obj.data.vertices:
        vertex.select = vertex.index in vertex_indices
    back_obj.data.update()
    context.tool_settings.mesh_select_mode = (True, False, False)
    bpy.ops.object.mode_set(mode="EDIT")


def ensure_projected_source_backup_collection(context):
    collection = bpy.data.collections.get(PROJECTED_SOURCE_BACKUP_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(PROJECTED_SOURCE_BACKUP_COLLECTION)
    if collection.name not in context.scene.collection.children:
        context.scene.collection.children.link(collection)
    collection.hide_render = True
    collection.hide_viewport = True
    return collection


def archive_projected_shell_sources(context, front_obj, back_obj, output_obj):
    sources = ((front_obj, "FRONT"), (back_obj, "BACK"))
    for obj, role in sources:
        if obj.get(PROJECTED_SOURCE_BACKUP_FLAG):
            raise ValueError(f"{role} source is already archived: {obj.name}")

    backup_collection = ensure_projected_source_backup_collection(context)
    states = []
    try:
        for obj, role in sources:
            original_collections = list(obj.users_collection)
            states.append(
                {
                    "object": obj,
                    "collections": original_collections,
                    "hide_viewport": obj.hide_viewport,
                    "hide_render": obj.hide_render,
                    "hide_select": obj.hide_select,
                }
            )
            try:
                obj.select_set(False)
            except RuntimeError:
                pass
            if obj.name not in backup_collection.objects:
                backup_collection.objects.link(obj)
            for collection in original_collections:
                if collection != backup_collection:
                    collection.objects.unlink(obj)
            obj[PROJECTED_SOURCE_BACKUP_FLAG] = True
            obj[PROJECTED_SOURCE_ROLE] = role
            obj[PROJECTED_SOURCE_OUTPUT] = output_obj.name
            obj[PROJECTED_SOURCE_COLLECTIONS] = json.dumps(
                [collection.name for collection in original_collections]
            )
            obj.hide_render = True
            obj.hide_select = True
            obj.hide_viewport = True
    except Exception:
        for state in states:
            obj = state["object"]
            for collection in state["collections"]:
                if obj.name not in collection.objects:
                    collection.objects.link(obj)
            if obj.name in backup_collection.objects:
                backup_collection.objects.unlink(obj)
            for property_name in (
                PROJECTED_SOURCE_BACKUP_FLAG,
                PROJECTED_SOURCE_ROLE,
                PROJECTED_SOURCE_OUTPUT,
                PROJECTED_SOURCE_COLLECTIONS,
            ):
                if property_name in obj:
                    del obj[property_name]
            obj.hide_viewport = state["hide_viewport"]
            obj.hide_render = state["hide_render"]
            obj.hide_select = state["hide_select"]
        raise
    return backup_collection


def remove_mesh_object_and_data(obj):
    mesh = obj.data if obj is not None and obj.type == "MESH" else None
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def barycentric_coordinates_2d(point, a, b, c):
    denominator = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y)
    if abs(denominator) <= 1.0e-14:
        return None
    weight_a = ((b.y - c.y) * (point.x - c.x) + (c.x - b.x) * (point.y - c.y)) / denominator
    weight_b = ((c.y - a.y) * (point.x - c.x) + (a.x - c.x) * (point.y - c.y)) / denominator
    weight_c = 1.0 - weight_a - weight_b
    return weight_a, weight_b, weight_c


def back_projection_triangles(back_obj):
    mesh = back_obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        raise ValueError(f"Back projection mesh has no active UV map: {back_obj.name}")
    mesh.calc_loop_triangles()
    triangles = []
    for triangle in mesh.loop_triangles:
        positions = [mesh.vertices[index].co.xy.copy() for index in triangle.vertices]
        loops = list(triangle.loops)
        uvs = [uv_layer.data[index].uv.copy() for index in loops]
        triangles.append(
            {
                "positions": positions,
                "uvs": uvs,
                "min_x": min(position.x for position in positions),
                "max_x": max(position.x for position in positions),
                "min_y": min(position.y for position in positions),
                "max_y": max(position.y for position in positions),
            }
        )
    if not triangles:
        raise ValueError(f"Back projection mesh has no usable triangles: {back_obj.name}")
    return triangles


def projected_back_uvs(front_obj, back_obj, tolerance=1.0e-6):
    triangles = back_projection_triangles(back_obj)
    front_to_back = back_obj.matrix_world.inverted_safe() @ front_obj.matrix_world
    projected = {}
    outside_points = []
    for vertex in front_obj.data.vertices:
        point_3d = front_to_back @ vertex.co
        point = point_3d.xy
        matched_uv = None
        for triangle in triangles:
            if (
                point.x < triangle["min_x"] - tolerance
                or point.x > triangle["max_x"] + tolerance
                or point.y < triangle["min_y"] - tolerance
                or point.y > triangle["max_y"] + tolerance
            ):
                continue
            weights = barycentric_coordinates_2d(
                point,
                triangle["positions"][0],
                triangle["positions"][1],
                triangle["positions"][2],
            )
            if weights is None or min(weights) < -tolerance or max(weights) > 1.0 + tolerance:
                continue
            matched_uv = Vector((0.0, 0.0))
            for weight, uv in zip(weights, triangle["uvs"]):
                matched_uv += uv * weight
            break
        if matched_uv is None:
            outside_points.append(point.copy())
        else:
            projected[vertex.index] = matched_uv
    if outside_points:
        raise BackProjectionCoverageError(
            len(outside_points),
            nearest_back_boundary_vertices(back_obj, outside_points),
        )
    return projected


def front_boundary_records(mesh, uv_layer):
    edge_uses = {}
    for polygon in mesh.polygons:
        loop_indices = list(polygon.loop_indices)
        polygon_uvs = [uv_layer.data[index].uv.copy() for index in loop_indices]
        face_center_uv = Vector((0.0, 0.0))
        for uv in polygon_uvs:
            face_center_uv += uv
        face_center_uv /= max(len(polygon_uvs), 1)
        for offset, start_loop in enumerate(loop_indices):
            end_loop = loop_indices[(offset + 1) % len(loop_indices)]
            start_vertex = mesh.loops[start_loop].vertex_index
            end_vertex = mesh.loops[end_loop].vertex_index
            key = tuple(sorted((start_vertex, end_vertex)))
            edge_uses.setdefault(key, []).append(
                {
                    "start": start_vertex,
                    "end": end_vertex,
                    "start_uv": uv_layer.data[start_loop].uv.copy(),
                    "end_uv": uv_layer.data[end_loop].uv.copy(),
                    "face_center_uv": face_center_uv.copy(),
                }
            )
    return [records[0] for records in edge_uses.values() if len(records) == 1]


def projected_shell_name(front_obj):
    suffix = "_single_plate"
    base = front_obj.name[:-len(suffix)] if front_obj.name.endswith(suffix) else front_obj.name
    return f"{base}_projected_shell"


def build_projected_shell_object(context, front_obj, back_obj, props):
    if front_obj is back_obj:
        raise ValueError("Front and Back Projection must be different mesh objects")
    if front_obj.type != "MESH" or back_obj.type != "MESH":
        raise ValueError("Front and Back Projection must both be mesh objects")
    if front_obj.get(PROJECTED_SOURCE_BACKUP_FLAG) or back_obj.get(PROJECTED_SOURCE_BACKUP_FLAG):
        raise ValueError("Archived Projected Shell sources cannot be used again")
    front_mesh = front_obj.data
    front_uv_layer = front_mesh.uv_layers.active
    if front_uv_layer is None:
        raise ValueError(f"Front mesh has no active UV map: {front_obj.name}")
    if not front_mesh.polygons:
        raise ValueError(f"Front mesh has no faces: {front_obj.name}")
    if not front_obj.material_slots or front_obj.material_slots[0].material is None:
        raise ValueError(f"Front mesh has no material in slot 1: {front_obj.name}")
    if not back_obj.material_slots or back_obj.material_slots[0].material is None:
        raise ValueError(f"Back projection mesh has no material in slot 1: {back_obj.name}")

    back_uvs = projected_back_uvs(front_obj, back_obj)
    boundary_records = front_boundary_records(front_mesh, front_uv_layer)
    vertex_count = len(front_mesh.vertices)
    half_gap = float(props.shell_gap) * 0.5
    vertices = []
    for vertex in front_mesh.vertices:
        position = vertex.co.copy()
        position.z += half_gap
        vertices.append(tuple(position))
    for vertex in front_mesh.vertices:
        position = vertex.co.copy()
        position.z -= half_gap
        vertices.append(tuple(position))

    faces = []
    face_materials = []
    face_uvs = []
    for polygon in front_mesh.polygons:
        source_vertices = list(polygon.vertices)
        source_uvs = [front_uv_layer.data[index].uv.copy() for index in polygon.loop_indices]
        faces.append(source_vertices)
        face_materials.append(0)
        face_uvs.append(source_uvs)

        faces.append([index + vertex_count for index in reversed(source_vertices)])
        face_materials.append(1)
        face_uvs.append([back_uvs[index].copy() for index in reversed(source_vertices)])

    if not props.no_shell:
        inset = min(0.95, max(0.0, float(props.side_uv_inset)))
        for record in boundary_records:
            start = record["start"]
            end = record["end"]
            start_uv = record["start_uv"]
            end_uv = record["end_uv"]
            center_uv = record["face_center_uv"]
            start_inner_uv = start_uv.lerp(center_uv, inset)
            end_inner_uv = end_uv.lerp(center_uv, inset)
            faces.append([start, start + vertex_count, end + vertex_count, end])
            face_materials.append(2)
            face_uvs.append([start_uv, start_inner_uv, end_inner_uv, end_uv])

    output_name = projected_shell_name(front_obj)
    mesh = bpy.data.meshes.new(f"{output_name}_Mesh")
    output_obj = None
    try:
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        mesh.materials.append(front_obj.material_slots[0].material)
        mesh.materials.append(back_obj.material_slots[0].material)
        mesh.materials.append(make_side_material("elm01_leaf_shell_edge"))
        for polygon, material_index in zip(mesh.polygons, face_materials):
            polygon.material_index = material_index
        uv_layer = mesh.uv_layers.new(name=front_uv_layer.name or "UVMap")
        for polygon, polygon_uvs in zip(mesh.polygons, face_uvs):
            for loop_index, uv in zip(polygon.loop_indices, polygon_uvs):
                uv_layer.data[loop_index].uv = uv

        output_obj = bpy.data.objects.new(output_name, mesh)
        target_collection = front_obj.users_collection[0] if front_obj.users_collection else context.scene.collection
        target_collection.objects.link(output_obj)
        output_obj.matrix_world = front_obj.matrix_world.copy()
        output_obj["atlas_leaf_projected_shell"] = True
        output_obj["atlas_leaf_projected_front"] = front_obj.name
        output_obj["atlas_leaf_projected_back"] = back_obj.name
        configure_leaf_surface(output_obj, props.shell_side_sharp_angle)
    except Exception:
        if output_obj is not None:
            bpy.data.objects.remove(output_obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        raise
    return output_obj, len(boundary_records)


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


def deformation_edge_change(mesh, source_positions, target_positions):
    relative_changes = []
    for edge in mesh.edges:
        start_index, end_index = edge.vertices
        source_length = (
            source_positions[start_index] - source_positions[end_index]
        ).length
        if source_length <= 1.0e-9:
            continue
        target_length = (
            target_positions[start_index] - target_positions[end_index]
        ).length
        relative_changes.append(abs(target_length - source_length) / source_length)
    if not relative_changes:
        return 0.0, 0.0
    relative_changes.sort()
    p95_index = int((len(relative_changes) - 1) * 0.95)
    return relative_changes[p95_index], relative_changes[-1]


def merged_slice_intervals(intervals, tolerance):
    if not intervals:
        return []
    merged = [dict(intervals[0])]
    for interval in intervals[1:]:
        current = merged[-1]
        if interval["start"] <= current["end"] + tolerance:
            if interval["start"] < current["start"]:
                current["start"] = interval["start"]
                current["start_position"] = interval["start_position"].copy()
            if interval["end"] > current["end"]:
                current["end"] = interval["end"]
                current["end_position"] = interval["end_position"].copy()
        else:
            merged.append(dict(interval))
    for interval in merged:
        interval["center_cross"] = (interval["start"] + interval["end"]) * 0.5
        interval["center_position"] = (
            interval["start_position"] + interval["end_position"]
        ) * 0.5
    return merged


def front_uv_slice_analysis(obj, long_axis, long_min, long_max, slice_count=32):
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return 0.0, 0, []

    long_component = 0 if long_axis == "U" else 1
    cross_component = 1 - long_component
    cross_values = [
        uv_layer.data[loop_index].uv[cross_component]
        for poly in mesh.polygons
        if poly.material_index == 0
        for loop_index in poly.loop_indices
    ]
    if not cross_values:
        return 0.0, 0, []
    tolerance = max((max(cross_values) - min(cross_values)) * 1.0e-5, 1.0e-8)
    slices = []
    for sample_index in range(slice_count):
        factor = (sample_index + 0.5) / slice_count
        long_value = long_min + (long_max - long_min) * factor
        intervals = []
        for poly in mesh.polygons:
            if poly.material_index != 0:
                continue
            loops = list(poly.loop_indices)
            coords = [uv_layer.data[index].uv.copy() for index in loops]
            positions = [mesh.vertices[mesh.loops[index].vertex_index].co.copy() for index in loops]
            polygon_longs = [coord[long_component] for coord in coords]
            if long_value < min(polygon_longs) or long_value > max(polygon_longs):
                continue
            crossings = []
            for index, start in enumerate(coords):
                end = coords[(index + 1) % len(coords)]
                start_position = positions[index]
                end_position = positions[(index + 1) % len(positions)]
                start_long = start[long_component]
                end_long = end[long_component]
                if abs(end_long - start_long) <= 1.0e-12:
                    continue
                if not (min(start_long, end_long) <= long_value < max(start_long, end_long)):
                    continue
                edge_factor = (long_value - start_long) / (end_long - start_long)
                crossings.append({
                    "cross": start[cross_component]
                    + (end[cross_component] - start[cross_component]) * edge_factor,
                    "position": start_position.lerp(end_position, edge_factor),
                })
            crossings.sort(key=lambda item: item["cross"])
            for index in range(0, len(crossings) - 1, 2):
                intervals.append({
                    "start": crossings[index]["cross"],
                    "end": crossings[index + 1]["cross"],
                    "start_position": crossings[index]["position"],
                    "end_position": crossings[index + 1]["position"],
                })
        merged = merged_slice_intervals(
            sorted(intervals, key=lambda item: (item["start"], item["end"])),
            tolerance,
        )
        slices.append({"long": long_value, "intervals": merged})

    interval_counts = [len(item["intervals"]) for item in slices]
    body_counts = interval_counts[2:-2] if len(interval_counts) > 4 else interval_counts
    if not body_counts:
        return 0.0, 0, slices
    multi_fraction = sum(count > 1 for count in body_counts) / len(body_counts)
    return multi_fraction, max(body_counts), slices


def build_profile_from_slice_centers(centers):
    if len(centers) < 4:
        return []
    centers = sorted(centers, key=lambda item: item[0])
    long_values = [item[0] for item in centers]
    center_x = [item[1].x for item in centers]
    center_y = [item[1].y for item in centers]
    radius = max(1, len(centers) // 20)
    center_x = smooth_profile_values(center_x, radius=radius, passes=2)
    center_y = smooth_profile_values(center_y, radius=radius, passes=2)

    tangents = []
    previous_tangent = (0.0, 1.0)
    for index in range(len(centers)):
        previous_index = max(0, index - 1)
        next_index = min(len(centers) - 1, index + 1)
        tangent_x = center_x[next_index] - center_x[previous_index]
        tangent_y = center_y[next_index] - center_y[previous_index]
        length = (tangent_x * tangent_x + tangent_y * tangent_y) ** 0.5
        if length > 1.0e-12:
            previous_tangent = (tangent_x / length, tangent_y / length)
        tangents.append(previous_tangent)
    tangent_x_values = smooth_profile_values(
        [tangent[0] for tangent in tangents], radius=radius, passes=2
    )
    tangent_y_values = smooth_profile_values(
        [tangent[1] for tangent in tangents], radius=radius, passes=2
    )

    profile = []
    cumulative = 0.0
    previous_tangent = (0.0, 1.0)
    for index in range(len(centers)):
        if index:
            delta_x = center_x[index] - center_x[index - 1]
            delta_y = center_y[index] - center_y[index - 1]
            cumulative += (delta_x * delta_x + delta_y * delta_y) ** 0.5
        tangent_x = tangent_x_values[index]
        tangent_y = tangent_y_values[index]
        length = (tangent_x * tangent_x + tangent_y * tangent_y) ** 0.5
        if length > 1.0e-12:
            previous_tangent = (tangent_x / length, tangent_y / length)
        profile.append({
            "long": long_values[index],
            "cx": center_x[index],
            "cy": center_y[index],
            "tx": previous_tangent[0],
            "ty": previous_tangent[1],
            "arc": cumulative,
        })
    return profile


def build_connected_uv_centerline(
    slices,
    vertex_uvs,
    stem_indices,
    stem_is_min,
    long_axis,
    object_extent,
):
    cross_component = 1 if long_axis == "U" else 0
    stem_cross_values = [
        vertex_uvs[index][cross_component]
        for index in stem_indices
        if index in vertex_uvs
    ]
    if not stem_cross_values:
        return []
    stem_cross = percentile(sorted(stem_cross_values), 0.5)
    populated = [item for item in slices if item["intervals"]]
    if not stem_is_min:
        populated.reverse()
    if len(populated) < 4:
        return []

    all_crosses = [
        value
        for item in populated
        for interval in item["intervals"]
        for value in (interval["start"], interval["end"])
    ]
    cross_range = max(max(all_crosses) - min(all_crosses), 1.0e-9)
    object_extent = max(object_extent, 1.0e-9)
    selected = []
    previous = None
    for item in populated:
        candidates = item["intervals"]
        if previous is None:
            def initial_score(interval):
                if interval["start"] <= stem_cross <= interval["end"]:
                    cross_distance = 0.0
                else:
                    cross_distance = min(
                        abs(stem_cross - interval["start"]),
                        abs(stem_cross - interval["end"]),
                    )
                pivot_distance = interval["center_position"].xy.length / object_extent
                return cross_distance / cross_range + pivot_distance * 0.2

            chosen = min(candidates, key=initial_score)
        else:
            def continuity_score(interval):
                overlap = min(previous["end"], interval["end"]) - max(
                    previous["start"], interval["start"]
                )
                if overlap >= 0.0:
                    gap = 0.0
                else:
                    gap = -overlap
                center_delta = abs(
                    interval["center_cross"] - previous["center_cross"]
                )
                position_delta = (
                    interval["center_position"].xy
                    - previous["center_position"].xy
                ).length
                return (
                    gap / cross_range * 4.0
                    + center_delta / cross_range * 0.35
                    + position_delta / object_extent
                )

            chosen = min(candidates, key=continuity_score)
        selected.append((item["long"], chosen["center_position"].copy()))
        previous = chosen

    return build_profile_from_slice_centers(selected)


def analyze_straight_mesh(obj, end_window_pct=0.015):
    mesh = obj.data
    vertex_uvs = front_plate_vertex_uvs(obj)
    if vertex_uvs is None:
        return None, "No usable active UVs"

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
        return None, "Could not find usable UV endpoints"

    min_endpoint = endpoint_center(min_indices)
    max_endpoint = endpoint_center(max_indices)
    stem_is_min = min_endpoint.length <= max_endpoint.length

    samples = [
        (long_values[index], float(source_positions[index].x), float(source_positions[index].y), index)
        for index in vertex_uvs
    ]
    multi_fraction, max_intervals, slice_data = front_uv_slice_analysis(
        obj,
        long_axis,
        long_min,
        long_max,
    )
    is_branching_plate = (
        multi_fraction >= 0.15
        or (max_intervals >= 3 and multi_fraction >= 0.05)
    )
    profile = build_uv_centerline(samples, long_min, long_range)
    profile_mode = "silhouette"
    if is_branching_plate:
        x_values = [position.x for position in source_positions.values()]
        y_values = [position.y for position in source_positions.values()]
        object_extent = Vector(
            (max(x_values) - min(x_values), max(y_values) - min(y_values))
        ).length
        connected_profile = build_connected_uv_centerline(
            slice_data,
            vertex_uvs,
            min_indices if stem_is_min else max_indices,
            stem_is_min,
            long_axis,
            object_extent,
        )
        if len(connected_profile) >= 4:
            profile = connected_profile
            profile_mode = "connected stem"
    if len(profile) < 2:
        return None, "Could not build a usable stem centerline"
    total_arc = profile[-1]["arc"]
    if total_arc <= 1.0e-9:
        return None, "Mesh has no usable stem-tip length"

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
    return {
        "mesh": mesh,
        "vertex_uvs": vertex_uvs,
        "long_axis": long_axis,
        "long_values": long_values,
        "long_min": long_min,
        "long_max": long_max,
        "long_range": long_range,
        "source_positions": source_positions,
        "min_indices": min_indices,
        "max_indices": max_indices,
        "min_endpoint": min_endpoint,
        "max_endpoint": max_endpoint,
        "stem_is_min": stem_is_min,
        "samples": samples,
        "profile": profile,
        "total_arc": total_arc,
        "chord": chord,
        "chord_length": chord_length,
        "deviation": deviation,
        "strip_width": strip_width,
        "strip_ratio": strip_ratio,
        "multi_fraction": multi_fraction,
        "max_intervals": max_intervals,
        "is_branching_plate": is_branching_plate,
        "profile_mode": profile_mode,
    }, None


def pivot_body_direction(analysis):
    profile = analysis["profile"]
    from_stem = profile if analysis["stem_is_min"] else list(reversed(profile))
    last_index = len(from_stem) - 1
    start_index = min(last_index, max(0, int(last_index * 0.08)))
    end_index = min(last_index, max(start_index + 1, int(last_index * 0.22)))
    start = from_stem[start_index]
    end = from_stem[end_index]
    direction = Vector((end["cx"] - start["cx"], end["cy"] - start["cy"]))
    tip_direction = Vector(
        (
            from_stem[-1]["cx"] - from_stem[0]["cx"],
            from_stem[-1]["cy"] - from_stem[0]["cy"],
        )
    )
    if direction.length <= 1.0e-12:
        direction = tip_direction
    if direction.length <= 1.0e-12:
        return Vector((0.0, 1.0))
    direction.normalize()
    if tip_direction.length > 1.0e-12 and direction.dot(tip_direction) < 0.0:
        direction.negate()
    return direction


def straighten_mesh_by_uv(obj, end_window_pct=0.015, unbend_deviation_pct=0.02):
    analysis, error = analyze_straight_mesh(obj, end_window_pct)
    if analysis is None:
        return False, error

    root_direction = pivot_body_direction(analysis)

    if obj.data.users > 1:
        obj.data = obj.data.copy()

    mesh = obj.data
    source_positions = analysis["source_positions"]
    samples = analysis["samples"]
    profile = analysis["profile"]
    total_arc = analysis["total_arc"]
    chord = analysis["chord"]
    chord_length = analysis["chord_length"]
    stem_is_min = analysis["stem_is_min"]
    is_branching_plate = analysis["is_branching_plate"]
    use_unbend = (
        (
            (is_branching_plate and analysis["profile_mode"] == "connected stem")
            or (not is_branching_plate and analysis["strip_ratio"] >= 2.5)
        )
        and (chord_length <= 1.0e-12 or analysis["deviation"] > total_arc * unbend_deviation_pct)
    )

    # Both frames below map with determinant +1 so face winding (and therefore
    # normals) are preserved; the previous implementation mirrored the mesh.
    new_positions = {}
    shape_guard_fallback = False
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
        if is_branching_plate:
            p95_change, max_change = deformation_edge_change(
                mesh,
                source_positions,
                new_positions,
            )
            if p95_change > 0.08 or max_change > 0.5:
                use_unbend = False
                shape_guard_fallback = True
                new_positions = {}
        if use_unbend:
            mode = "main-stem unbend" if is_branching_plate else "unbend"
    if not use_unbend:
        if is_branching_plate:
            direction = root_direction
        else:
            if chord_length <= 1.0e-12:
                return False, "Mesh has no usable alignment direction"
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
        mode = "shape-preserving align" if shape_guard_fallback else "align"

    preserve_existing_pivot = is_branching_plate
    if preserve_existing_pivot:
        if use_unbend:
            pivot_y, pivot_x = project_to_centerline(profile, 0.0, 0.0)
            if not stem_is_min:
                pivot_y = total_arc - pivot_y
                pivot_x = -pivot_x
            anchor_x, anchor_y = pivot_x, pivot_y
        else:
            anchor_x, anchor_y = 0.0, 0.0
    else:
        anchor_y = min(position.y for position in new_positions.values())
        root_window = max(total_arc * end_window_pct, 1.0e-6)
        root_indices = [
            index for index, position in new_positions.items()
            if position.y <= anchor_y + root_window
        ]
        anchor_x = (
            min(new_positions[index].x for index in root_indices)
            + max(new_positions[index].x for index in root_indices)
        ) * 0.5

    for index, position in new_positions.items():
        mesh.vertices[index].co = Vector((position.x - anchor_x, position.y - anchor_y, position.z))
    mesh.update()
    classification = "branching plate" if is_branching_plate else "strand"
    return True, (
        f"{analysis['long_axis']} axis aligned to local Y, {mode}, {classification}, "
        f"stem {'min' if stem_is_min else 'max'}, geometry preserved"
    )


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
        pair_json = generation_pair_json(props)

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
        pair_json = generation_pair_json(props)

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


class ATLASLEAF_OT_set_projected_shell_front(Operator):
    bl_idname = "atlas_leaf.set_projected_shell_front"
    bl_label = "Set Front"
    bl_description = "Use the active one-plate mesh as the projected shell's geometry and front UV source"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Make a mesh object active before setting Front.")
            return {"CANCELLED"}
        if obj.get(PROJECTED_SOURCE_BACKUP_FLAG):
            self.report({"ERROR"}, "Archived Projected Shell sources cannot be reused.")
            return {"CANCELLED"}
        context.scene.atlas_leaf_builder.projected_shell_front = obj
        self.report({"INFO"}, f"Projected shell Front: {obj.name}")
        return {"FINISHED"}


class ATLASLEAF_OT_set_projected_shell_back(Operator):
    bl_idname = "atlas_leaf.set_projected_shell_back"
    bl_label = "Set Back"
    bl_description = "Use the active one-plate mesh as the manually aligned back UV projection source"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Make a mesh object active before setting Back Projection.")
            return {"CANCELLED"}
        if obj.get(PROJECTED_SOURCE_BACKUP_FLAG):
            self.report({"ERROR"}, "Archived Projected Shell sources cannot be reused.")
            return {"CANCELLED"}
        context.scene.atlas_leaf_builder.projected_shell_back = obj
        self.report({"INFO"}, f"Projected shell Back Projection: {obj.name}")
        return {"FINISHED"}


class ATLASLEAF_OT_build_projected_shell(Operator):
    bl_idname = "atlas_leaf.build_projected_shell"
    bl_label = "Build Projected Shell"
    bl_description = "Duplicate the Front topology, project its back UVs from the aligned Back Projection plate, and bridge the boundary"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = getattr(context.scene, "atlas_leaf_builder", None)
        return bool(
            props
            and props.projected_shell_front is not None
            and props.projected_shell_back is not None
        )

    def execute(self, context):
        props = context.scene.atlas_leaf_builder
        front_obj = props.projected_shell_front
        back_obj = props.projected_shell_back
        try:
            output_obj, boundary_count = build_projected_shell_object(
                context,
                front_obj,
                back_obj,
                props,
            )
        except BackProjectionCoverageError as exc:
            select_back_projection_vertices(context, back_obj, exc.back_vertex_indices)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        try:
            backup_collection = archive_projected_shell_sources(
                context,
                front_obj,
                back_obj,
                output_obj,
            )
        except Exception as exc:
            remove_mesh_object_and_data(output_obj)
            self.report({"ERROR"}, f"Could not archive source plates; build was rolled back: {exc}")
            return {"CANCELLED"}

        props.projected_shell_front = None
        props.projected_shell_back = None

        bpy.ops.object.select_all(action="DESELECT")
        output_obj.select_set(True)
        context.view_layer.objects.active = output_obj
        context.view_layer.update()
        self.report(
            {"INFO"},
            f"Built {output_obj.name} from {front_obj.name}; projected back UVs from "
            f"{back_obj.name}; bridged {boundary_count if not props.no_shell else 0} boundary edges; "
            f"archived both sources in {backup_collection.name}.",
        )
        return {"FINISHED"}


class ATLASLEAF_OT_straight_mesh(Operator):
    bl_idname = "atlas_leaf.straight_mesh"
    bl_label = "Straight Mesh"
    bl_description = "Back up each selected mesh and straighten it without deleting root geometry or folding branching plates"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        objects = selected_mesh_objects(context)
        if not objects:
            self.report({"ERROR"}, "Select one or more mesh objects.")
            return {"CANCELLED"}

        straightened = 0
        backups_created = 0
        backups_reused = 0
        skipped = []
        for obj in objects:
            backup, backup_created = create_straight_backup(context, obj)
            source_data = obj.data
            source_data_name = source_data.name
            working_data = source_data.copy()
            working_data.name = f"{source_data_name}__straight_work"
            obj.data = working_data
            try:
                ok, message = straighten_mesh_by_uv(obj)
            except Exception as exc:
                ok, message = False, str(exc)
            if ok:
                if source_data.users == 0:
                    bpy.data.meshes.remove(source_data)
                    working_data.name = source_data_name
                else:
                    working_data.name = f"{source_data_name}_straight"
                straightened += 1
                if backup_created:
                    backups_created += 1
                else:
                    backups_reused += 1
            else:
                obj.data = source_data
                if working_data.users == 0:
                    bpy.data.meshes.remove(working_data)
                if backup_created:
                    remove_straight_backup(obj, backup)
                skipped.append(f"{obj.name}: {message}")

        context.view_layer.update()
        if straightened == 0:
            self.report({"ERROR"}, "; ".join(skipped) or "No meshes were straightened.")
            return {"CANCELLED"}
        if skipped:
            self.report(
                {"WARNING"},
                f"Straightened {straightened}; skipped {len(skipped)}; backups {backups_created} new, {backups_reused} reused.",
            )
        else:
            self.report(
                {"INFO"},
                f"Straightened {straightened} selected mesh{'es' if straightened != 1 else ''}; "
                f"backups {backups_created} new, {backups_reused} reused.",
            )
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
        layout.separator()
        layout.operator("atlas_leaf.generate", icon="MESH_DATA")
        projected_box = layout.box()
        projected_box.label(text="Manual Front/Back Projected Shell", icon="UV")
        row = projected_box.row(align=True)
        row.prop(props, "projected_shell_front", text="Front")
        row.operator("atlas_leaf.set_projected_shell_front", text="Set Front")
        row = projected_box.row(align=True)
        row.prop(props, "projected_shell_back", text="Back")
        row.operator("atlas_leaf.set_projected_shell_back", text="Set Back")
        projected_box.label(text="Align and enlarge Back over Front, then build.")
        projected_box.operator("atlas_leaf.build_projected_shell", icon="MOD_UVPROJECT")
        layout.operator("atlas_leaf.straight_mesh", text="Straight Mesh (Backup Original)", icon="MOD_SIMPLEDEFORM")
        layout.operator("atlas_leaf.auto_split_material_collections", icon="OUTLINER_COLLECTION")
        layout.separator()
        layout.prop(props, "speedtree_atlas_asset_name")
        layout.prop(props, "speedtree_create_missing_spm")
        layout.prop(props, "speedtree_source_materials_json")
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
