import math
from pathlib import Path

import bpy


def ensure_collection(name, clear_existing):
    collection = bpy.data.collections.get(name)
    if collection and clear_existing:
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    if not collection:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def make_atlas_material(name, image_path):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    material.use_backface_culling = False
    nodes = material.node_tree.nodes
    nodes.clear()
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(image_path, check_existing=True)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    material.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return material


def make_side_material(name):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0.18, 0.24, 0.15, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.8
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return material


def make_flat_color_material(name, color):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = 0.7
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return material


def make_preview_material(name, image_path):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    material.use_backface_culling = False
    nodes = material.node_tree.nodes
    nodes.clear()
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(image_path), check_existing=True)
    try:
        tex.image.reload()
    except RuntimeError:
        pass
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.55
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    material.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return material, tex.image


def create_preview_plane(collection, object_name, image_path, center_x, width):
    material, image = make_preview_material(object_name + "_material", image_path)
    img_width, img_height = image.size
    if img_width <= 0 or img_height <= 0:
        img_width, img_height = 1, 1
    height = width * (img_height / img_width)
    half_w = width * 0.5
    half_h = height * 0.5
    mesh = bpy.data.meshes.new(object_name + "_Mesh")
    # Vertical XZ plane so the preview reads like a board in the viewport.
    vertices = [
        (-half_w, 0.0, -half_h),
        (half_w, 0.0, -half_h),
        (half_w, 0.0, half_h),
        (-half_w, 0.0, half_h),
    ]
    mesh.from_pydata(vertices, [], [(0, 1, 2, 3)])
    mesh.update()
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for loop_index, uv in zip(mesh.polygons[0].loop_indices, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        uv_layer.data[loop_index].uv = uv
    mesh.materials.append(material)
    obj = bpy.data.objects.new(object_name, mesh)
    obj.location = (center_x, -2.0, height * 0.5)
    collection.objects.link(obj)
    return obj


def show_preview_images_in_view(preview_data):
    island_path = preview_data.get("island_labels")
    pair_path = preview_data.get("pair_preview")
    collection = ensure_collection("Atlas_Leaf_Preview", True)
    objects = []
    if island_path and Path(island_path).exists():
        objects.append(create_preview_plane(collection, "atlas_leaf_island_labels_preview", island_path, -3.2, 4.0))
    if pair_path and Path(pair_path).exists():
        objects.append(create_preview_plane(collection, "atlas_leaf_pair_preview", pair_path, 3.2, 4.8))
    for obj in bpy.context.scene.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    if objects:
        bpy.context.view_layer.objects.active = objects[-1]
    return objects


def make_speedtree_material(name, image_path, opacity_path=None):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    material.use_backface_culling = False
    nodes = material.node_tree.nodes
    nodes.clear()
    color = nodes.new("ShaderNodeTexImage")
    color.name = "SpeedTree Color"
    color.label = "SpeedTree Color"
    color.image = bpy.data.images.load(str(image_path), check_existing=True)
    color.image.reload()
    color.extension = "CLIP"
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(color.outputs["Color"], bsdf.inputs["Base Color"])
    if opacity_path:
        opacity = nodes.new("ShaderNodeTexImage")
        opacity.name = "SpeedTree Opacity"
        opacity.label = "SpeedTree Opacity"
        opacity.image = bpy.data.images.load(str(opacity_path), check_existing=True)
        opacity.image.reload()
        opacity.extension = "CLIP"
        opacity.image.colorspace_settings.name = "Non-Color"
        material.node_tree.links.new(opacity.outputs["Color"], bsdf.inputs["Alpha"])
        if hasattr(material, "surface_render_method"):
            material.surface_render_method = "DITHERED"
        elif hasattr(material, "blend_method"):
            material.blend_method = "HASHED"
    material.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return material


def shell_sharp_edge_indices(mesh, side_angle_degrees):
    edge_faces = {}
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            edge_index = mesh.loops[loop_index].edge_index
            edge_faces.setdefault(edge_index, []).append(poly.index)

    sharp_edges = set()
    angle_limit = math.radians(max(0.0, float(side_angle_degrees)))
    for edge_index, face_indices in edge_faces.items():
        materials = {mesh.polygons[index].material_index for index in face_indices}
        if len(materials) > 1:
            sharp_edges.add(edge_index)
            continue
        if materials == {2} and len(face_indices) == 2:
            first = mesh.polygons[face_indices[0]].normal
            second = mesh.polygons[face_indices[1]].normal
            if first.angle(second, 0.0) >= angle_limit:
                sharp_edges.add(edge_index)
    return sharp_edges


def configure_leaf_surface(obj, side_angle_degrees):
    mesh = obj.data
    for poly in mesh.polygons:
        poly.use_smooth = True
    sharp_edges = shell_sharp_edge_indices(mesh, side_angle_degrees)
    for edge in mesh.edges:
        edge.use_edge_sharp = edge.index in sharp_edges
    mesh.update()

    weighted = obj.modifiers.new("shell_weighted_normals", "WEIGHTED_NORMAL")
    if hasattr(weighted, "keep_sharp"):
        weighted.keep_sharp = True
    if hasattr(weighted, "weight"):
        weighted.weight = 50


def build_mesh_object(mesh_data, materials, collection, side_angle_degrees=35.0):
    mesh = bpy.data.meshes.new(mesh_data["name"] + "_Mesh")
    vertices = [tuple(v) for v in mesh_data["vertices"]]
    faces = [tuple(face) for face in mesh_data["faces"]]
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    for material_name in mesh_data.get("materials", []):
        mesh.materials.append(materials[material_name])
    for poly, material_index in zip(mesh.polygons, mesh_data.get("face_materials", [])):
        poly.material_index = material_index
    uv_layer = mesh.uv_layers.new(name="UVMap")
    uvs = mesh_data["uvs"]
    face_uvs = mesh_data.get("face_uvs")
    for poly_index, poly in enumerate(mesh.polygons):
        if face_uvs:
            for loop_index, uv in zip(poly.loop_indices, face_uvs[poly_index]):
                uv_layer.data[loop_index].uv = uv
        else:
            for loop_index in poly.loop_indices:
                vertex_index = mesh.loops[loop_index].vertex_index
                uv_layer.data[loop_index].uv = uvs[vertex_index]
    obj = bpy.data.objects.new(mesh_data["name"], mesh)
    obj.location = tuple(mesh_data.get("location", (0.0, 0.0, 0.0)))
    collection.objects.link(obj)
    configure_leaf_surface(obj, side_angle_degrees)
    return obj
