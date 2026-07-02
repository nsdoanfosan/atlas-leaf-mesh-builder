bl_info = {
    "name": "Atlas Leaf Mesh Builder",
    "author": "Codex for PARK",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Atlas Leaf",
    "description": "Build no-opacity front/back leaf meshes from paired atlas alpha islands.",
    "category": "Object",
}

import json
import subprocess
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup


ADDON_DIR = Path(__file__).resolve().parent
HELPER_PATH = ADDON_DIR / "helper_pipeline.py"
DEFAULT_ALBEDO = r"D:/OneDrive/Forestportfolio/Texture/Elm/TCom_Leaves_Elm01/TCom_Leaves_Elm01_4K_albedo.tif"
DEFAULT_ALPHA = r"D:/OneDrive/Forestportfolio/Texture/Elm/TCom_Leaves_Elm01/TCom_Leaves_Elm01_4K_alpha.tif"
DEFAULT_PAIRS = [
    {"front": 1, "back": 18},
    {"front": 2, "back": 17},
    {"front": 3, "back": 16},
    {"front": 4, "back": 15},
    {"front": 10, "back": 5},
    {"front": 6, "back": 19},
    {"front": 7, "back": 21},
    {"front": 8, "back": 23},
    {"front": 9, "back": 20},
    {"front": 11, "back": 14},
    {"front": 12, "back": 22},
    {"front": 13, "back": 24},
]


def default_output_dir():
    return str(Path.home() / "Documents" / "Codex" / "atlas_leaf_mesh_builder_output")


def run_external_python(python_exe, args, timeout=300):
    command = [python_exe] + args
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def dependency_status(python_exe):
    code = "import PIL, cv2, skimage, triangle; print('ok')"
    result = run_external_python(python_exe, ["-c", code], timeout=30)
    return result.returncode == 0, result.stdout.strip(), result.stderr.strip()


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
    material.use_backface_culling = True
    nodes = material.node_tree.nodes
    nodes.clear()
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(image_path, check_existing=True)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    material.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return material


def build_mesh_object(mesh_data, material, collection):
    mesh = bpy.data.meshes.new(mesh_data["name"] + "_Mesh")
    vertices = [tuple(v) for v in mesh_data["vertices"]]
    faces = [tuple(face) for face in mesh_data["faces"]]
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    uv_layer = mesh.uv_layers.new(name="UVMap")
    uvs = mesh_data["uvs"]
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            uv_layer.data[loop_index].uv = uvs[vertex_index]
    obj = bpy.data.objects.new(mesh_data["name"], mesh)
    obj.data.materials.append(material)
    collection.objects.link(obj)
    return obj


def write_report(result, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "atlas_leaf_mesh_builder_report.txt"
    lines = [
        "Atlas Leaf Mesh Builder Report",
        f"Albedo: {result['albedo_path']}",
        f"Alpha: {result['alpha_path']}",
        f"Quality: {result['quality']}",
        f"Component count: {result['component_count']}",
        f"Object count: {len(result['objects'])}",
        "",
    ]
    for item in result["summary"]:
        lines.append(
            "pair {pair:02d}: front {front:02d} -> back {back:02d}, "
            "transform {transform}, iou {iou:.4f}, verts {quality_vertices}, "
            "tris/side {triangles_per_side}, min_angle {min_angle:.2f}".format(**item)
        )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


class ATLASLEAF_Properties(PropertyGroup):
    albedo_path: StringProperty(
        name="Albedo",
        subtype="FILE_PATH",
        default=DEFAULT_ALBEDO,
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
        name="Pairs JSON",
        default=json.dumps(DEFAULT_PAIRS),
        description="List of front/back island pairs. Example: [{\"front\":1,\"back\":18}]",
    )
    quality: EnumProperty(
        name="Quality",
        items=[
            ("FAST", "Fast", "Lower density, less internal regularity"),
            ("BALANCED", "Balanced", "Recommended q18 balanced triangulation"),
            ("HIGH", "High", "Higher density q22 triangulation"),
        ],
        default="BALANCED",
    )
    alpha_threshold: IntProperty(name="Alpha Threshold", default=127, min=1, max=254)
    min_area: IntProperty(name="Min Island Area", default=400, min=1)
    clear_existing: BoolProperty(name="Clear Collection", default=True)
    last_report: StringProperty(name="Last Report", default="")


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
    bl_label = "Reset Elm01 Pairs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.atlas_leaf_builder.pair_json = json.dumps(DEFAULT_PAIRS)
        self.report({"INFO"}, "Elm01 pair preset restored.")
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

        args = [
            str(HELPER_PATH),
            "--albedo",
            albedo,
            "--alpha",
            alpha,
            "--output",
            str(json_path),
            "--pairs-json",
            props.pair_json,
            "--quality",
            props.quality,
            "--alpha-threshold",
            str(props.alpha_threshold),
            "--min-area",
            str(props.min_area),
        ]
        result = run_external_python(props.external_python, args, timeout=600)
        if result.returncode != 0:
            message = result.stderr[-1200:] or result.stdout[-1200:] or "Helper failed."
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        data = json.loads(json_path.read_text(encoding="utf-8"))
        collection = ensure_collection(props.collection_name, props.clear_existing)
        front_material = make_atlas_material("elm01_leaf_front_atlas", albedo)
        back_material = make_atlas_material("elm01_leaf_back_atlas", albedo)
        materials = {"front": front_material, "back": back_material}
        for mesh_data in data["objects"]:
            build_mesh_object(mesh_data, materials[mesh_data["material"]], collection)

        report_path = write_report(data, output_dir)
        props.last_report = str(report_path)
        self.report({"INFO"}, f"Generated {len(data['objects'])} objects. Report: {report_path}")
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
        layout.prop(props, "collection_name")
        layout.prop(props, "clear_existing")
        layout.operator("atlas_leaf.reset_elm01_pairs", icon="FILE_REFRESH")
        layout.prop(props, "pair_json")
        layout.separator()
        layout.operator("atlas_leaf.generate", icon="MESH_DATA")
        if props.last_report:
            layout.label(text="Last report:")
            layout.label(text=props.last_report)


classes = (
    ATLASLEAF_Properties,
    ATLASLEAF_OT_check_dependencies,
    ATLASLEAF_OT_install_dependencies,
    ATLASLEAF_OT_reset_elm01_pairs,
    ATLASLEAF_OT_generate,
    ATLASLEAF_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.atlas_leaf_builder = bpy.props.PointerProperty(type=ATLASLEAF_Properties)


def unregister():
    del bpy.types.Scene.atlas_leaf_builder
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
