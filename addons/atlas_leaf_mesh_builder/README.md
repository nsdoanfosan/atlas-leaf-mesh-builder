# Atlas Leaf Mesh Builder

Blender add-on for building no-opacity leaf meshes from texture-atlas alpha islands.

## What It Does

- Reads an albedo atlas and alpha atlas.
- Detects alpha islands.
- Uses an editable front island list.
- Extracts a subpixel silhouette with marching squares.
- Builds a constrained quality triangulation with the external `triangle` Python package.
- Creates one mesh object for each front island.
- Can build one plate or two matching front/back plates.
- In two-plate mode, the back plate uses the same UVs as the front plate.
- Bridges the actual triangulated boundary between front and back surfaces when shell side faces are enabled.
- Assigns bridge side UVs as a narrow inset strip inside the front island.
- Places each leaf at a stem-side pivot origin when enabled.
- Can split selected meshes at the world X axis, delete the part below it, and center the new bottom pivot at the world origin.
- Keeps the original atlas texture set intact.

## Blender UI

Location:

```text
3D Viewport > Sidebar > Atlas Leaf > Atlas Leaf Mesh
```

The panel also includes `SPM To Add`, a `Target SPMs` list, `Atlas Asset Name`, and `Build/Update Target SPMs`. A missing `.spm` is rejected by default; enable `Create Missing Target SPM` only when a blank SpeedTree 10.1 target is intentional.

`Split Below X Axis + Center Pivot` processes every selected mesh. It cuts at world `Y=0`, deletes world `Y<0`, uses the center of the new bottom boundary as the object pivot, and places that pivot at world `0,0,0` while preserving rotation and scale.

## Current Default Preset

The default paths and pair preset target:

```text
D:/OneDrive/Forestportfolio/Texture/Elm/TCom_Leaves_Elm01
```

The default front island list is the Elm01 front set validated during the test pass.

The Front Island Editor is the main editing surface. Each row is one generated leaf:

- `F`: front island number

Use the `+` row to add a front island. Use the `X` button at the end of a row to remove it.

Internally, the rows are serialized into front island JSON for the helper. Entries look like this:

```json
[
  {"front": 9},
  {"front": 4}
]
```

Older JSON entries that still include `back`, `transform`, or `angle` are accepted for loading, but those fields are ignored.

## Dependency Model

The add-on UI runs in Blender, but the image processing and triangulation run in an external Python process. This avoids packaging compiled modules inside Blender.

Required external Python packages:

- Pillow
- opencv-python
- scikit-image
- triangle

Use the add-on's `Check Dependencies` and `Install Dependencies` buttons.

## Generated Structure

The add-on creates:

```text
Atlas_Leaf_Meshes
  leaf_01_front_01_double_plate
  ...
```

Each front island has one mesh object. `Plate Mode` controls whether the object is built as `One Plate` or `Two Plates`.

In `Two Plates` mode, `Shell Gap` separates front/back surfaces before bridging. The back plate uses the same UVs as the front plate. The `Side UV Inset` setting controls how far the bridge UVs move inward from the front island boundary.

The `Shell Sharp Angle` setting controls hard edges on the shell side wall. Front/back-to-side material boundary edges are always marked sharp. Edges shared only by shell side faces are marked sharp when the adjacent face normals exceed the angle threshold. Faces are shade-smoothed and a weighted normal modifier keeps the leaf faces clean without adding bevel or extra rounded shell geometry.

Materials:

- `elm01_leaf_front_atlas`
- `elm01_leaf_back_atlas`
- `elm01_leaf_shell_edge`

The front and back materials reference the same albedo atlas texture and the same UVs. The shell edge uses a solid side material.

## SpeedTree SPM

The SpeedTree SPM build/update creates:

```text
speedtree_export/
  Elm01_Atlas_Leaf_MeshAssets_v10_1.spm
  meshes/
    01_leaf_pair_...fbx
    ...
  speedtree_import_manifest.json
  README_SPEEDTREE_IMPORT.md
```

SpeedTree materials reference the selected original texture set directly. The add-on does not copy, convert, or create texture files in the target SPM folder.

The FBX files are temporary evaluated export copies with all faces assigned to one atlas material. This includes the sharp-edge/weighted-normal setup and matches SpeedTree's mesh asset workflow better than the Blender working meshes, which keep separate front/back/side material slots for inspection.

Each `.spm` target is treated as a separate SpeedTree update target. Add one or more files to the `Target SPMs` list with `SPM To Add` and `+`; each target's parent folder becomes its export folder for `meshes/`, the manifest, and the import notes.

`Atlas Asset Name` explicitly controls the base SpeedTree `Material_v8` name for a new export. Explicit names beginning with `M_cluster_` are canonicalized to `M_leaf_`; leaving the field blank preserves a legacy blend/file-derived name. An existing same-name material is updated when its UUID export scope matches. Older Atlas Leaf materials whose name-based scope exactly equals that same material name are reclaimed once and retagged with the current UUID; a same-name material owned by a different UUID scope remains a conflict.

`Generator Source Mapping` is a JSON object keyed by absolute target SPM path. Each value is a list of exact source `Material_v8` names (or an object with `source_material_names` and optional same-order `source_material_ids`). After asset upsert, matching `Leaf Mesh`/`LeafMesh` and `Frond` Generator `:Material`/`:Mesh` slot pairs are replaced together. Source cutout mesh order maps to exported `leaf_NN` order; SpeedTree's `Mesh=-10` sentinel maps deterministically to `leaf_01`. The target SPM is restored byte-for-byte if any group or connection validation fails.

`FBX Geometry Scale` is applied to the exported FBX mesh geometry before it is linked into the SPM. The SpeedTree external Mesh asset's own `Scale` field stays `1`, so updating an SPM does not bake a `0.01` Scale value into the SPM itself. The default exported geometry scale is `0.01` so SpeedTree generators using `Use Actual Size` start from a manageable size, and you can adjust it per atlas before rebuilding the target SPMs.

Rebuilding existing SPMs synchronizes each material's mesh list to the current Blender collection. Each Blender source collection gets a persistent SpeedTree export scope ID, so multiple atlases can share one `.spm` without cleaning each other up. If a generated material or mesh from the same scope's previous manifest is no longer part of the current export, the add-on removes that stale SpeedTree material/mesh asset and deletes stale FBX/XML files only when they live inside that target's generated `meshes/` folder. Pre-existing SpeedTree materials and meshes outside the add-on's manifest/UserData scope are left untouched.

Final manifests are stored per target under `.atlas_leaf_speedtree_targets/<SPM stem>.json`; per-scope/per-target cleanup records live under `.atlas_leaf_speedtree_scopes/`. Generator-referenced mesh IDs are protected from stale-asset cleanup.

The managed atlas material's `SeasonCurve` is flattened to `1` from season `0` through `1` so the imported atlas leaves are not hidden or faded by SpeedTree seasonal material graphs. Other materials' season curves are left untouched.

Texture maps are matched from the selected Albedo texture's folder. Selecting Albedo automatically fills the matching `Opacity`/`Alpha` field, and generation rechecks that match before use. The SPM material references the original matching `Opacity`/`Alpha`, `Roughness`, `Translucency`/`Subsurface`, `Normal`, and `Height`/`Displacement` files when the same texture-set base name is present.

## Notes

Back-side alpha matching has been removed. Use the front island list and choose `One Plate` or `Two Plates` depending on the mesh output you need.

## Code Layout

- `__init__.py`: add-on metadata, module reload, Blender registration.
- `props.py`: Blender properties plus front island and SPM target list helpers.
- `operators.py`: UI panel and operator classes.
- `materials.py`: Blender material, preview plane, and generated mesh object setup.
- `speedtree.py`: texture matching, texture conversion, FBX export, and SPM XML update logic.
- `utils.py`: external Python helpers and report writing.
- `constants.py`: default paths and SpeedTree sample locations.

Future improvements:

- visual island labeling inside Blender,
- batch processing.
