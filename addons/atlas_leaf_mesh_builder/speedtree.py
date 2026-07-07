import copy
import gzip
import json
import re
import shutil
from pathlib import Path
from xml.dom import minidom
import xml.etree.ElementTree as ET

import bpy

from .constants import SPEEDTREE_101_BLANK_SPM, SPEEDTREE_101_EXTERNAL_MESH_SAMPLE, SPEEDTREE_101_MATERIAL_SAMPLE
from .materials import make_speedtree_material
from .props import speedtree_spm_targets
from .utils import run_external_python


def atlas_texture_paths(albedo_path):
    albedo = Path(albedo_path)
    texture_dir = albedo.parent
    paths = {"albedo": albedo}

    role_tokens = {
        "albedo": ("albedo", "basecolor", "base_color", "base color", "diffuse", "color"),
        "alpha": ("opacity", "alpha", "cutout", "mask"),
        "height": ("height", "displacement", "disp"),
        "normal": ("normal", "norm", "nrm"),
        "roughness": ("roughness", "rough"),
        "translucency": (
            "translucency",
            "translucent",
            "transmission",
            "subsurface",
            "subsurfacecolor",
            "subsurface_color",
            "sss",
            "transqulin",
        ),
    }
    texture_extensions = {".bmp", ".exr", ".jpeg", ".jpg", ".png", ".tga", ".tif", ".tiff"}

    def normalized_stem(path):
        text = path.stem.lower()
        for char in ("-", ".", " "):
            text = text.replace(char, "_")
        while "__" in text:
            text = text.replace("__", "_")
        return text.strip("_")

    def strip_role(stem, tokens):
        result = stem
        for token in sorted(tokens, key=len, reverse=True):
            token = token.lower().replace(" ", "_")
            for pattern in (f"_{token}_", f"_{token}", f"{token}_", token):
                if pattern in result:
                    result = result.replace(pattern, "_")
        while "__" in result:
            result = result.replace("__", "_")
        return result.strip("_")

    albedo_stem = normalized_stem(albedo)
    albedo_base = strip_role(albedo_stem, role_tokens["albedo"])
    candidates = [path for path in texture_dir.iterdir() if path.is_file() and path.suffix.lower() in texture_extensions]

    for key, tokens in role_tokens.items():
        if key == "albedo":
            continue

        best_score = -1
        best_path = None
        for candidate in candidates:
            if candidate == albedo:
                continue
            candidate_stem = normalized_stem(candidate)
            if not any(token.lower().replace(" ", "_") in candidate_stem for token in tokens):
                continue

            candidate_base = strip_role(candidate_stem, tokens)
            score = 0
            if candidate_base == albedo_base:
                score += 100
            elif candidate_base.startswith(albedo_base) or albedo_base.startswith(candidate_base):
                score += 40
            if candidate.suffix.lower() == albedo.suffix.lower():
                score += 10
            if candidate_stem.startswith(albedo_base):
                score += 5

            if score > best_score:
                best_score = score
                best_path = candidate

        if best_path is not None:
            paths[key] = best_path
    return paths


def convert_textures_for_speedtree(python_exe, texture_paths, export_dir):
    texture_dir = Path(export_dir) / "textures"
    texture_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        key: str(path)
        for key, path in texture_paths.items()
        if Path(path).exists()
    }
    code = r"""
import json
from pathlib import Path
from PIL import Image, ImageOps
import re

payload = json.loads(ARG_PAYLOAD)
out_dir = Path(payload["out_dir"])
out_dir.mkdir(parents=True, exist_ok=True)
result = {}
for key, src in payload["textures"].items():
    src_path = Path(src)
    image = Image.open(src_path)
    if key in {"roughness", "alpha", "height"}:
        image = image.convert("L")
    else:
        image = image.convert("RGB")
    out_path = out_dir / f"{src_path.stem}.png"
    image.save(out_path)
    result[key] = str(out_path)
    if key == "roughness":
        gloss = ImageOps.invert(image)
        gloss_stem = re.sub(r"(?i)([_\-. ])roughness$", r"\1gloss_from_roughness", src_path.stem)
        if gloss_stem == src_path.stem:
            gloss_stem = f"{src_path.stem}_gloss_from_roughness"
        gloss_path = out_dir / f"{gloss_stem}.png"
        gloss.save(gloss_path)
        result["gloss"] = str(gloss_path)
print(json.dumps(result))
""".replace("ARG_PAYLOAD", repr(json.dumps({"textures": payload, "out_dir": str(texture_dir)})))
    result = run_external_python(python_exe, ["-c", code], timeout=300)
    if result.returncode == 0:
        return json.loads(result.stdout.strip() or "{}")

    copied = {}
    for key, src in payload.items():
        destination = texture_dir / Path(src).name
        shutil.copy2(src, destination)
        copied[key] = str(destination)
    return copied


def write_speedtree_readme(export_dir, manifest):
    path = Path(export_dir) / "README_SPEEDTREE_IMPORT.md"
    mesh_count = len(manifest["meshes"])
    lines = [
        "# SpeedTree Import Notes",
        "",
        "Generated by Atlas Leaf Mesh Builder.",
        "",
        "## Contents",
        "",
        "- The target `.spm` is a SpeedTree Modeler 10.1 asset file with one named material linked to all generated leaf meshes.",
        f"- Mesh FBX files: `{mesh_count}`",
        "- Textures are in `textures/`.",
        "- Meshes are closed shells with the stem pivot at object origin.",
        "- FBX export uses a single material per mesh for SpeedTree mesh assets.",
        f"- FBX mesh geometry scale: `{manifest.get('mesh_geometry_scale', 1)}`; SpeedTree Mesh asset Scale remains `1`.",
        "",
        "## Suggested SpeedTree Setup",
        "",
        "1. Open the target `.spm` in SpeedTree Modeler 10.1.",
        "2. The atlas material should already reference the texture maps in `textures/`.",
        "3. The material's cutout mesh list should reference the FBX files in `meshes/`.",
        "4. Use that material/mesh set in a Leaf Mesh generator as leaf variants.",
        "5. If SpeedTree asks to relink files, keep the `.spm`, `meshes/`, and `textures/` folders together.",
        "",
        "## Texture Map Hints",
        "",
    ]
    for key, value in manifest["textures"].items():
        lines.append(f"- {key}: `{Path(value).name}`")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def read_spm_xml(path):
    return ET.fromstring(gzip.decompress(Path(path).read_bytes()))


def write_spm_xml(path, root):
    rough = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="\t", encoding="UTF-8")
    Path(path).write_bytes(gzip.compress(pretty, mtime=0))


def relative_spm_path(spm_path, target_path):
    rel = Path(target_path).resolve()
    try:
        import os

        rel_text = os.path.relpath(str(rel), str(Path(spm_path).resolve().parent))
    except ValueError:
        rel_text = str(rel)
    return rel_text.replace("\\", "/")


def child_text(node, tag, value):
    child = node.find(tag)
    if child is None:
        child = ET.SubElement(node, tag)
    child.text = str(value)
    return child


def make_season_control_point(x, y="1"):
    point = ET.Element("ControlPoint")
    child_text(point, "X", x)
    child_text(point, "Y", y)
    child_text(point, "TangentX", "1")
    child_text(point, "TangentY", "0")
    child_text(point, "Length", "0")
    return point


def force_material_season_curve_one(material):
    season_curve = material.find("SeasonCurve")
    if season_curve is None:
        season_curve = ET.SubElement(material, "SeasonCurve", {"DrawMode": "false"})
    season_curve.attrib["DrawMode"] = "false"
    for child in list(season_curve):
        season_curve.remove(child)
    season_curve.append(make_season_control_point("0"))
    season_curve.append(make_season_control_point("1"))

    override = material.find("OverrideSeason")
    if override is not None:
        override.text = "false"
    return season_curve


def first_material_template():
    root = read_spm_xml(SPEEDTREE_101_MATERIAL_SAMPLE)
    assets = root.find("Assets")
    if assets is None:
        raise RuntimeError("SpeedTree 10.1 material sample has no Assets node.")
    for node in assets:
        if node.tag == "Material_v8":
            return copy.deepcopy(node)
    raise RuntimeError("SpeedTree 10.1 material sample has no Material_v8 node.")


def first_external_mesh_template():
    root = read_spm_xml(SPEEDTREE_101_EXTERNAL_MESH_SAMPLE)
    assets = root.find("Assets")
    if assets is None:
        raise RuntimeError("SpeedTree 10.1 mesh sample has no Assets node.")
    for node in assets:
        if node.tag == "Mesh" and node.findtext("Embedded") == "false":
            return copy.deepcopy(node)
    raise RuntimeError("SpeedTree 10.1 mesh sample has no external Mesh node.")


def set_material_map(material, map_name, texture_path, spm_path, enabled=True):
    map_node = None
    for candidate in material.findall("Map"):
        if candidate.attrib.get("Name") == map_name:
            map_node = candidate
            break
    if map_node is None:
        return

    tex_filename = child_text(map_node, "TexFilename", "")
    if texture_path:
        tex_filename.text = relative_spm_path(spm_path, texture_path)
        child_text(map_node, "TexEnabled", "true" if enabled else "false")
        child_text(map_node, "TexSizeX", "4096")
        child_text(map_node, "TexSizeY", "4096")
    else:
        tex_filename.text = ""
        child_text(map_node, "TexEnabled", "false")


def make_spm_material(spm_path, texture_exports, mesh_ids, material_name):
    material = first_material_template()
    material.attrib["ID"] = "1"
    material.attrib["Name"] = material_name
    update_spm_material(material, spm_path, texture_exports, mesh_ids)
    return material


def update_spm_material(material, spm_path, texture_exports, mesh_ids):
    child_text(material, "TwoSided", "true")
    child_text(material, "CutoutMeshID", str(mesh_ids[0]))
    child_text(material, "Width", "4096")
    child_text(material, "Height", "4096")
    child_text(material, "Atlas", "0")
    child_text(material, "AtlasName", "")
    child_text(material, "BackMaterialID", "-1")

    supplemental = material.find("SupplementalCutoutMeshIDs")
    if supplemental is None:
        supplemental = ET.SubElement(material, "SupplementalCutoutMeshIDs")
    for child in list(supplemental):
        supplemental.remove(child)
    supplemental.attrib["Count"] = str(max(0, len(mesh_ids) - 1))
    for mesh_id in mesh_ids[1:]:
        ET.SubElement(supplemental, "CutoutMesh", {"ID": str(mesh_id)})

    set_material_map(material, "Color", texture_exports.get("albedo"), spm_path)
    set_material_map(material, "Opacity", texture_exports.get("alpha"), spm_path)
    set_material_map(material, "Normal", texture_exports.get("normal"), spm_path)
    set_material_map(material, "Gloss", texture_exports.get("gloss") or texture_exports.get("roughness"), spm_path)
    set_material_map(material, "Height", texture_exports.get("height"), spm_path)
    set_material_map(material, "SubsurfaceColor", texture_exports.get("translucency"), spm_path)
    set_material_map(material, "SubsurfaceAmount", texture_exports.get("translucency"), spm_path)
    for empty_map in ("Specular", "Metallic", "AO", "Custom", "Custom2"):
        set_material_map(material, empty_map, None, spm_path, enabled=False)
    force_material_season_curve_one(material)
    return material


def spm_material_mesh_ids(material):
    ids = []
    cutout = material.findtext("CutoutMeshID")
    if cutout and cutout != "-1":
        ids.append(int(cutout))
    supplemental = material.find("SupplementalCutoutMeshIDs")
    if supplemental is not None:
        for child in supplemental.findall("CutoutMesh"):
            mesh_id = child.attrib.get("ID")
            if mesh_id and mesh_id != "-1":
                ids.append(int(mesh_id))
    return ids


def read_json_file(path, fallback):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def find_material_by_name(assets, material_name):
    if assets is None:
        return None
    for node in assets.findall("Material_v8"):
        if node.attrib.get("Name") == material_name:
            return node
    return None


def make_spm_mesh(mesh_template, mesh_id, fbx_path, spm_path):
    mesh = copy.deepcopy(mesh_template)
    mesh.attrib["ID"] = str(mesh_id)
    mesh.attrib["Name"] = Path(fbx_path).stem
    child_text(mesh, "Filename", relative_spm_path(spm_path, fbx_path))
    child_text(mesh, "FixWinding", "false")
    child_text(mesh, "FlipNormals", "false")
    child_text(mesh, "Embedded", "false")
    child_text(mesh, "Orient", "6")
    child_text(mesh, "PivotStyle", "0")
    child_text(mesh, "Scale", "1")
    for child in list(mesh):
        if child.tag.startswith("Lod_") or child.tag == "EmbeddedData_v7" or child.tag == "Cutout":
            mesh.remove(child)
    return mesh


def create_speedtree_spm(spm_path, manifest, material_name):
    if not SPEEDTREE_101_BLANK_SPM.exists():
        raise RuntimeError(f"SpeedTree 10.1 Blank.spm not found: {SPEEDTREE_101_BLANK_SPM}")
    if not SPEEDTREE_101_MATERIAL_SAMPLE.exists():
        raise RuntimeError(f"SpeedTree 10.1 material sample not found: {SPEEDTREE_101_MATERIAL_SAMPLE}")
    if not SPEEDTREE_101_EXTERNAL_MESH_SAMPLE.exists():
        raise RuntimeError(f"SpeedTree 10.1 mesh sample not found: {SPEEDTREE_101_EXTERNAL_MESH_SAMPLE}")

    spm_path = Path(spm_path)
    spm_path.parent.mkdir(parents=True, exist_ok=True)
    root = read_spm_xml(SPEEDTREE_101_BLANK_SPM)
    root.attrib["Title"] = " Elm01 Atlas Leaf Mesh Assets "
    root.attrib["VersionString"] = "10.1.0 "

    assets = root.find("Assets")
    if assets is None:
        assets = ET.SubElement(root, "Assets")

    for node in list(assets):
        if node.tag in {"Material_v8", "Mesh"}:
            assets.remove(node)

    mesh_paths = [Path(item["fbx"]) for item in manifest["meshes"]]
    mesh_ids = list(range(1, len(mesh_paths) + 1))
    material = make_spm_material(spm_path, manifest["textures"], mesh_ids, material_name)
    mesh_template = first_external_mesh_template()
    insert_index = 0
    assets.insert(insert_index, material)
    insert_index += 1
    for mesh_id, mesh_path in zip(mesh_ids, mesh_paths):
        assets.insert(insert_index, make_spm_mesh(mesh_template, mesh_id, mesh_path, spm_path))
        insert_index += 1

    write_spm_xml(spm_path, root)
    parsed = read_spm_xml(spm_path)
    parsed_assets = parsed.find("Assets")
    material_count = len(parsed_assets.findall("Material_v8")) if parsed_assets is not None else 0
    mesh_count = len(parsed_assets.findall("Mesh")) if parsed_assets is not None else 0
    if material_count != 1 or mesh_count != len(mesh_paths):
        raise RuntimeError("Generated SPM validation failed.")
    return spm_path, "created", 1, mesh_ids


def max_asset_id(assets, tag):
    maximum = 0
    if assets is None:
        return maximum
    for node in assets.findall(tag):
        try:
            maximum = max(maximum, int(node.attrib.get("ID", "0")))
        except ValueError:
            continue
    return maximum


def upsert_speedtree_assets_in_spm(spm_path, manifest, material_name):
    if not SPEEDTREE_101_MATERIAL_SAMPLE.exists():
        raise RuntimeError(f"SpeedTree 10.1 material sample not found: {SPEEDTREE_101_MATERIAL_SAMPLE}")
    if not SPEEDTREE_101_EXTERNAL_MESH_SAMPLE.exists():
        raise RuntimeError(f"SpeedTree 10.1 mesh sample not found: {SPEEDTREE_101_EXTERNAL_MESH_SAMPLE}")

    spm_path = Path(spm_path)
    if not spm_path.exists():
        return create_speedtree_spm(spm_path, manifest, material_name)

    root = read_spm_xml(spm_path)
    assets = root.find("Assets")
    if assets is None:
        assets = ET.SubElement(root, "Assets")

    mesh_paths = [Path(item["fbx"]) for item in manifest["meshes"]]
    mesh_template = first_external_mesh_template()
    material = find_material_by_name(assets, material_name)
    action = "updated"
    old_mesh_ids = []
    if material is None:
        action = "injected"
        material_id = max_asset_id(assets, "Material_v8") + 1
        first_mesh_id = max_asset_id(assets, "Mesh") + 1
        mesh_ids = list(range(first_mesh_id, first_mesh_id + len(mesh_paths)))
        material = make_spm_material(spm_path, manifest["textures"], mesh_ids, material_name)
        material.attrib["ID"] = str(material_id)
        assets.append(material)
    else:
        material_id = int(material.attrib.get("ID", max_asset_id(assets, "Material_v8") + 1))
        old_mesh_ids = spm_material_mesh_ids(material)
        mesh_ids = old_mesh_ids[: len(mesh_paths)]
        next_mesh_id = max_asset_id(assets, "Mesh") + 1
        while len(mesh_ids) < len(mesh_paths):
            mesh_ids.append(next_mesh_id)
            next_mesh_id += 1
        update_spm_material(material, spm_path, manifest["textures"], mesh_ids)

    mesh_nodes_by_id = {node.attrib.get("ID"): node for node in assets.findall("Mesh")}
    for mesh_id, mesh_path in zip(mesh_ids, mesh_paths):
        mesh_node = mesh_nodes_by_id.get(str(mesh_id))
        new_mesh = make_spm_mesh(mesh_template, mesh_id, mesh_path, spm_path)
        if mesh_node is None:
            assets.append(new_mesh)
        else:
            insert_at = list(assets).index(mesh_node)
            assets.remove(mesh_node)
            assets.insert(insert_at, new_mesh)

    unused_old_ids = set(old_mesh_ids) - set(mesh_ids)
    if unused_old_ids:
        for node in list(assets.findall("Mesh")):
            try:
                mesh_id = int(node.attrib.get("ID", "0"))
            except ValueError:
                continue
            if mesh_id in unused_old_ids:
                assets.remove(node)

    write_spm_xml(spm_path, root)

    parsed = read_spm_xml(spm_path)
    parsed_assets = parsed.find("Assets")
    parsed_material = None
    for node in parsed_assets.findall("Material_v8"):
        if node.attrib.get("ID") == str(material_id):
            parsed_material = node
            break
    if parsed_material is None:
        raise RuntimeError("SpeedTree SPM validation failed: material was not written.")
    parsed_mesh_ids = {node.attrib.get("ID") for node in parsed_assets.findall("Mesh")}
    parsed_material_mesh_ids = spm_material_mesh_ids(parsed_material)
    if parsed_material_mesh_ids != mesh_ids:
        raise RuntimeError(
            f"SpeedTree SPM validation failed: material mesh IDs are {parsed_material_mesh_ids}, expected {mesh_ids}."
        )
    missing_mesh_ids = [str(mesh_id) for mesh_id in mesh_ids if str(mesh_id) not in parsed_mesh_ids]
    if missing_mesh_ids:
        raise RuntimeError(f"SpeedTree SPM validation failed: missing mesh IDs {missing_mesh_ids}")
    stale_mesh_ids = [str(mesh_id) for mesh_id in unused_old_ids if str(mesh_id) in parsed_mesh_ids]
    if stale_mesh_ids:
        raise RuntimeError(f"SpeedTree SPM validation failed: stale deleted mesh IDs remain {stale_mesh_ids}")
    return spm_path, action, material_id, mesh_ids


def cleanup_stale_mesh_exports(export_dir, exported_meshes):
    manifest_path = Path(export_dir) / "speedtree_import_manifest.json"
    previous = read_json_file(manifest_path, {})
    if not previous:
        return []

    mesh_dir = (Path(export_dir) / "meshes").resolve()
    current_paths = {Path(item["fbx"]).resolve() for item in exported_meshes}
    removed = []
    for item in previous.get("meshes", []):
        fbx = item.get("fbx")
        if not fbx:
            continue
        path = Path(fbx)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in current_paths:
            continue
        if resolved.parent != mesh_dir or resolved.suffix.lower() != ".fbx":
            continue
        if resolved.exists():
            resolved.unlink()
            removed.append(str(resolved))
    return removed


def material_suffix_from_collection_name(collection_name):
    text = collection_name.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "group"


def grouped_source_objects(root_collection, base_material_name):
    direct_objects = [obj for obj in root_collection.objects if obj.type == "MESH"]
    child_groups = []

    def collect_meshes(collection, output):
        for obj in collection.objects:
            if obj.type == "MESH":
                output.append(obj)
        for child in collection.children:
            collect_meshes(child, output)

    for child in root_collection.children:
        objects = []
        collect_meshes(child, objects)
        if objects:
            suffix = material_suffix_from_collection_name(child.name)
            child_groups.append(
                {
                    "collection": child.name,
                    "material": f"{base_material_name}_{suffix}",
                    "objects": objects,
                }
            )

    if not child_groups:
        return [{"collection": root_collection.name, "material": base_material_name, "objects": direct_objects}]

    groups = child_groups
    if direct_objects:
        groups.append({"collection": root_collection.name, "material": base_material_name, "objects": direct_objects})
    return groups


def export_speedtree_assets(props, export_dir):
    collection = bpy.data.collections.get(props.collection_name)
    if not collection:
        raise RuntimeError(f"Collection not found: {props.collection_name}")

    base_material_name = props.speedtree_material_name.strip() or "Elm01_Atlas_Leaf"
    source_groups = grouped_source_objects(collection, base_material_name)
    if not any(group["objects"] for group in source_groups):
        raise RuntimeError(f"No mesh objects in collection: {props.collection_name}")

    if not hasattr(bpy.ops.export_scene, "fbx"):
        try:
            bpy.ops.preferences.addon_enable(module="io_scene_fbx")
        except Exception as exc:
            raise RuntimeError("Blender FBX exporter is not available.") from exc
    if not hasattr(bpy.ops.export_scene, "fbx"):
        raise RuntimeError("Blender FBX exporter is not available.")

    export_dir = Path(export_dir)
    mesh_dir = export_dir / "meshes"
    mesh_dir.mkdir(parents=True, exist_ok=True)

    texture_paths = atlas_texture_paths(bpy.path.abspath(props.albedo_path))
    texture_exports = convert_textures_for_speedtree(props.external_python, texture_paths, export_dir)
    materials = {
        group["material"]: make_speedtree_material(group["material"], bpy.path.abspath(props.albedo_path))
        for group in source_groups
        if group["objects"]
    }
    mesh_geometry_scale = max(float(props.speedtree_mesh_scale), 0.000001)

    temp_collection_name = "_AtlasLeaf_SpeedTree_Export_Temp"
    temp_collection = bpy.data.collections.get(temp_collection_name)
    if temp_collection:
        for obj in list(temp_collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    else:
        temp_collection = bpy.data.collections.new(temp_collection_name)
        bpy.context.scene.collection.children.link(temp_collection)

    previous_selection = list(bpy.context.selected_objects)
    previous_active = bpy.context.view_layer.objects.active
    depsgraph = bpy.context.evaluated_depsgraph_get()
    exported_meshes = []
    material_groups = []
    mesh_index = 1
    try:
        for group in source_groups:
            group_meshes = []
            material = materials.get(group["material"])
            if material is None:
                continue
            for source in group["objects"]:
                evaluated = source.evaluated_get(depsgraph)
                mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
                mesh.materials.clear()
                mesh.materials.append(material)
                for poly in mesh.polygons:
                    poly.material_index = 0
                if mesh_geometry_scale != 1.0:
                    for vertex in mesh.vertices:
                        vertex.co *= mesh_geometry_scale
                    mesh.update()
                temp_obj = bpy.data.objects.new(source.name + "_speedtree", mesh)
                temp_obj.matrix_world = source.matrix_world.copy()
                temp_collection.objects.link(temp_obj)

                for obj in bpy.context.selected_objects:
                    obj.select_set(False)
                temp_obj.select_set(True)
                bpy.context.view_layer.objects.active = temp_obj

                fbx_path = mesh_dir / f"{mesh_index:02d}_{source.name}.fbx"
                bpy.ops.export_scene.fbx(
                    filepath=str(fbx_path),
                    use_selection=True,
                    object_types={"MESH"},
                    apply_unit_scale=True,
                    bake_space_transform=False,
                    add_leaf_bones=False,
                    path_mode="RELATIVE",
                    embed_textures=False,
                )
                item = {
                    "name": source.name,
                    "fbx": str(fbx_path),
                    "source_object": source.name,
                    "source_collection": group["collection"],
                    "material": material.name,
                }
                exported_meshes.append(item)
                group_meshes.append(item)
                mesh_index += 1
                bpy.data.objects.remove(temp_obj, do_unlink=True)
                bpy.data.meshes.remove(mesh, do_unlink=True)
            if group_meshes:
                material_groups.append(
                    {
                        "collection": group["collection"],
                        "material": group["material"],
                        "mesh_count": len(group_meshes),
                        "meshes": group_meshes,
                    }
                )
    finally:
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        for obj in previous_selection:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if previous_active and previous_active.name in bpy.data.objects:
            bpy.context.view_layer.objects.active = previous_active
        if temp_collection and not temp_collection.objects:
            bpy.data.collections.remove(temp_collection)

    removed_stale_mesh_exports = cleanup_stale_mesh_exports(export_dir, exported_meshes)

    manifest = {
        "source_collection": props.collection_name,
        "speedtree_version_target": "10.1.0",
        "material": base_material_name if len(material_groups) == 1 else None,
        "material_groups": material_groups,
        "single_material_per_mesh": True,
        "mesh_geometry_scale": mesh_geometry_scale,
        "mesh_count": len(exported_meshes),
        "meshes": exported_meshes,
        "removed_stale_mesh_exports": removed_stale_mesh_exports,
        "textures": texture_exports,
        "notes": [
            "Use the exported FBX files as SpeedTree mesh assets.",
            "Use one atlas material for all leaf mesh variants.",
            "The SPM material mesh list is synchronized to the current Blender collection when rebuilt.",
            "Use gloss_from_roughness when the SpeedTree material slot expects gloss.",
        ],
    }
    manifest_path = export_dir / "speedtree_import_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    readme_path = write_speedtree_readme(export_dir, manifest)
    return manifest_path, readme_path, exported_meshes


def export_or_update_speedtree_spm_path(props, target_spm):
    target_spm = Path(target_spm)
    if not target_spm.name:
        raise RuntimeError("Target SPM is not set.")
    if target_spm.suffix.lower() != ".spm":
        raise RuntimeError("Target SPM must end with .spm")

    manifest_path, readme_path, exported_meshes = export_speedtree_assets(props, target_spm.parent)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    material_groups = manifest.get("material_groups") or [
        {
            "collection": manifest.get("source_collection", props.collection_name),
            "material": props.speedtree_material_name.strip() or "Elm01_Atlas_Leaf",
            "meshes": manifest.get("meshes", []),
        }
    ]

    group_results = []
    for group in material_groups:
        group_manifest = dict(manifest)
        group_manifest["meshes"] = group.get("meshes", [])
        if not group_manifest["meshes"]:
            continue
        material_name = group.get("material") or props.speedtree_material_name.strip() or "Elm01_Atlas_Leaf"
        spm_path, action, material_id, mesh_ids = upsert_speedtree_assets_in_spm(
            target_spm,
            group_manifest,
            material_name,
        )
        group["material_id"] = material_id
        group["mesh_ids"] = mesh_ids
        group_results.append(
            {
                "collection": group.get("collection"),
                "material": material_name,
                "material_id": material_id,
                "mesh_ids": mesh_ids,
                "action": action,
            }
        )

    if not group_results:
        raise RuntimeError("No SpeedTree material groups contained meshes.")

    action = ",".join(sorted({group["action"] for group in group_results}))
    material_id = group_results[0]["material_id"]
    mesh_ids = [mesh_id for group in group_results for mesh_id in group["mesh_ids"]]
    manifest["spm"] = str(spm_path)
    manifest["spm_action"] = action
    manifest["material_name"] = group_results[0]["material"] if len(group_results) == 1 else None
    manifest["speedtree_material_groups"] = group_results
    manifest["material_id"] = material_id
    manifest["mesh_ids"] = mesh_ids
    Path(manifest_path).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return spm_path, manifest_path, exported_meshes, action, material_id, mesh_ids, group_results


def export_or_update_speedtree_spm_targets(props):
    targets = speedtree_spm_targets(props)
    if not targets:
        raise RuntimeError("Add at least one target SPM.")

    results = []
    for target_spm in targets:
        spm_path, manifest_path, exported_meshes, action, material_id, mesh_ids, material_groups = export_or_update_speedtree_spm_path(
            props,
            target_spm,
        )
        results.append(
            {
                "spm_path": spm_path,
                "manifest_path": manifest_path,
                "exported_meshes": exported_meshes,
                "action": action,
                "material_id": material_id,
                "mesh_ids": mesh_ids,
                "material_groups": material_groups,
            }
        )
    return results

