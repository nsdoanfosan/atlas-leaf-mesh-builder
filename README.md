# Atlas Leaf Mesh Builder

Blender add-on for building no-opacity leaf meshes from texture-atlas alpha islands.

This add-on was built around the `TCom_Leaves_Elm01` atlas workflow:

- keep the original texture atlas set intact,
- use alpha islands as real mesh silhouettes,
- edit the front island list directly,
- choose one plate or two matching front/back plates,
- generate one mesh per front island,
- bridge the real triangulation boundary between front/back surfaces,
- keep shell bridge UVs inset inside the front island,
- place each leaf object at the stem-side pivot origin,
- use quality triangulation to avoid long fan triangles,
- build or update a SpeedTree 10.1 `.spm` asset file plus per-leaf FBX meshes and atlas textures.

## Blender Install

The add-on package lives at:

```text
addons/atlas_leaf_mesh_builder
```

On this machine it is installed into Blender 5.1 by junction:

```text
C:/Users/PARK/AppData/Roaming/Blender Foundation/Blender/5.1/scripts/addons/atlas_leaf_mesh_builder
```

Blender UI:

```text
3D Viewport > Sidebar > Atlas Leaf > Atlas Leaf Mesh
```

## Dependencies

The Blender UI calls an external Python helper for image processing and triangulation.

Required external Python packages:

- Pillow
- opencv-python
- scikit-image
- triangle

The add-on includes `Check Dependencies` and `Install Dependencies` buttons.

## Current State

Implemented:

- albedo input with automatic same-folder opacity/alpha matching,
- Elm01 front island preset,
- editable front island JSON,
- visible Front Island Editor rows,
- marching-squares subpixel contour extraction,
- Triangle quality triangulation,
- one-plate or two-plate mesh output,
- two-plate shell objects with adjustable shell gap,
- adjustable side UV inset for the shell bridge,
- simple shell side geometry with material-boundary sharp edges and angle-based shell-side sharp edges,
- stem-side pivot placement at object origin,
- same-atlas front/back material slots using the same UVs,
- SpeedTree 10.1 `.spm` create/update with one named atlas material linked to all 12 mesh variants,
- repeat updates that reuse the existing material/mesh IDs when the material name already exists,
- generation report.

Not implemented yet:

- visual island label overlay inside Blender,
- normal/roughness/height/translucency material node setup.

## Validation

Validated with Blender 5.1.2:

- add-on enables successfully,
- default enable saved to Blender preferences,
- generation operator creates 12 mesh objects for Elm01,
- front/back material slots both reference the same source albedo atlas and use matching UVs,
- shell bridge side faces have inset UVs inside the front atlas island,
- generated shell meshes use simple side quads, material-boundary sharp edges, shell-side angle sharp edges, and weighted normals,
- SpeedTree build/update operator creates or updates one target `.spm`, exports 12 FBX files, references the original texture files without creating copies, and records the material/mesh IDs in a manifest.
